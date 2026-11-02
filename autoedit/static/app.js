const $ = (id) => document.getElementById(id);
let currentJob = null;
let currentVersion = null;
let pollTimer = null;
let chosenFile = null;

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function fmtDur(s) {
  s = Math.round(s || 0);
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

// ---------------------------------------------------------------- status + list
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    $("pills").innerHTML = `
      <span class="pill ${s.claude ? "on" : ""}">${s.claude ? "Smart editing on" : "Simple mode (add a Claude key)"}</span>
      <span class="pill ${s.stock ? "on" : ""}">${s.stock ? "Stock b-roll on" : "Stock b-roll off"}</span>
      <span class="pill">${s.broll_count} b-roll clip${s.broll_count === 1 ? "" : "s"}</span>`;
    $("brollCount").textContent = `(${s.broll_count} clip${s.broll_count === 1 ? "" : "s"} there now)`;
    $("musicCount").textContent = `(${s.music_count} song${s.music_count === 1 ? "" : "s"} added)`;
  } catch (e) { /* app starting */ }
}

const STATUS_LABEL = { queued: "Waiting…", working: "Editing…", done: "Ready", error: "Needs attention" };

async function refreshList() {
  const jobs = await api("/api/jobs");
  const ul = $("jobList");
  if (!jobs.length) { ul.innerHTML = '<li class="muted small">Nothing yet.</li>'; return; }
  ul.innerHTML = "";
  for (const j of jobs) {
    const li = document.createElement("li");
    li.className = "job" + (currentJob && currentJob.id === j.id ? " active" : "");
    const last = j.versions[j.versions.length - 1];
    const thumb = last ? `<img src="/api/jobs/${j.id}/thumb/${last.n}" alt="" onerror="this.className='ph';this.removeAttribute('src')">` : '<div class="ph"></div>';
    li.innerHTML = `${thumb}<div><div class="name"></div><div class="st">${STATUS_LABEL[j.status] || j.status}${j.versions.length > 1 ? ` · ${j.versions.length} versions` : ""}</div></div>`;
    li.querySelector(".name").textContent = j.name;
    li.onclick = () => openJob(j.id);
    ul.appendChild(li);
  }
}

// ---------------------------------------------------------------- new edit
function showNew() {
  currentJob = null;
  clearTimeout(pollTimer);
  $("newView").classList.remove("hidden");
  $("jobView").classList.add("hidden");
  refreshList();
}

function setFile(file) {
  chosenFile = file;
  $("dropText").innerHTML = file ? `<b></b> <span class="muted">(${(file.size / 1e6).toFixed(0)} MB)</span>` : "<b>Drop your video here</b> or click to choose";
  if (file) $("dropText").querySelector("b").textContent = file.name;
  $("goBtn").disabled = !file;
}

$("fileInput").onchange = (e) => setFile(e.target.files[0]);
const drop = $("drop");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });
document.querySelectorAll("#examples .chip").forEach((c) => (c.onclick = () => { $("prompt").value = c.textContent; $("prompt").focus(); }));

$("goBtn").onclick = () => {
  if (!chosenFile) return;
  const form = new FormData();
  form.append("video", chosenFile);
  form.append("prompt", $("prompt").value);
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/jobs");
  $("goBtn").disabled = true;
  $("uploadBox").classList.remove("hidden");
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round((e.loaded / e.total) * 100);
    $("uploadBar").style.width = pct + "%";
    $("uploadText").textContent = `Loading video… ${pct}%`;
  };
  xhr.onload = () => {
    $("uploadBox").classList.add("hidden");
    $("goBtn").disabled = false;
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch (e) {}
    if (xhr.status !== 200) { alert(data.error || "Upload failed"); return; }
    setFile(null);
    $("fileInput").value = "";
    $("prompt").value = "";
    openJob(data.id);
  };
  xhr.onerror = () => { $("goBtn").disabled = false; alert("Upload failed. Is the app still running?"); };
  xhr.send(form);
};

// ---------------------------------------------------------------- job view
async function openJob(id, keepVersion = false) {
  clearTimeout(pollTimer);
  let job;
  try { job = await api(`/api/jobs/${id}`); } catch (e) { showNew(); return; }
  const changedJob = !currentJob || currentJob.id !== id;
  const seen = changedJob ? undefined : currentJob._seen;
  currentJob = job;
  currentJob._seen = seen;
  $("newView").classList.add("hidden");
  $("jobView").classList.remove("hidden");
  $("jobTitle").textContent = job.name;

  const busy = job.status === "queued" || job.status === "working";
  $("progressCard").classList.toggle("hidden", !busy);
  $("stepText").textContent = job.step || "Working…";
  $("progBar").style.width = Math.round((job.progress || 0) * 100) + "%";
  $("errorCard").classList.toggle("hidden", job.status !== "error");
  $("errorText").textContent = job.error || "";
  $("reviseBtn").disabled = busy;

  const versions = job.versions || [];
  $("resultCard").classList.toggle("hidden", !versions.length);
  if (versions.length) {
    const latest = versions[versions.length - 1].n;
    const grew = currentJob._seen !== undefined && latest > currentJob._seen;
    if (changedJob || !keepVersion || grew || !versions.some((v) => v.n === currentVersion)) currentVersion = latest;
    renderVersions(versions);
  }
  currentJob._seen = versions.length ? versions[versions.length - 1].n : 0;
  refreshList();
  if (busy) pollTimer = setTimeout(() => openJob(id, true), 1500);
}

function renderVersions(versions) {
  const box = $("versions");
  box.innerHTML = "";
  if (versions.length > 1) {
    for (const v of versions) {
      const b = document.createElement("button");
      b.textContent = `Version ${v.n}`;
      b.title = v.prompt || "First edit";
      b.className = v.n === currentVersion ? "active" : "";
      b.onclick = () => { currentVersion = v.n; renderVersions(versions); };
      box.appendChild(b);
    }
  }
  const v = versions.find((x) => x.n === currentVersion);
  const src = `/api/jobs/${currentJob.id}/video/${v.n}`;
  const player = $("player");
  if (player.dataset.src !== src) { player.src = src + `?t=${v.created}`; player.dataset.src = src; }
  $("summary").textContent = v.summary || "";
  const parts = [`${fmtDur(v.original_duration)} → ${fmtDur(v.duration)}`];
  if (v.broll_count) parts.push(`${v.broll_count} b-roll clip${v.broll_count === 1 ? "" : "s"}`);
  if (v.prompt) parts.push(`You asked: “${v.prompt}”`);
  $("stats").textContent = parts.join(" · ");
  $("dlVideo").href = src + "?download=1";
  $("dlSrt").href = `/api/jobs/${currentJob.id}/srt/${v.n}`;
}

$("reviseBtn").onclick = async () => {
  const prompt = $("revise").value.trim();
  if (!prompt || !currentJob) return;
  try {
    await api(`/api/jobs/${currentJob.id}/revise`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt }),
    });
    $("revise").value = "";
    openJob(currentJob.id);
  } catch (e) { alert(e.message); }
};
$("revise").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("reviseBtn").click(); });
$("newBtn").onclick = showNew;

// ---------------------------------------------------------------- settings
async function openSettings() {
  const s = await api("/api/settings");
  $("anthropicKey").value = s.anthropic_api_key;
  $("pexelsKey").value = s.pexels_api_key;
  $("brollFolder").value = s.broll_folder;
  $("whisperModel").value = s.whisper_model;
  $("settingsMsg").textContent = "";
  await refreshStatus();
  $("settings").classList.remove("hidden");
}
$("settingsBtn").onclick = openSettings;
$("closeSettings").onclick = () => $("settings").classList.add("hidden");
$("settings").onclick = (e) => { if (e.target.id === "settings") $("settings").classList.add("hidden"); };
$("saveSettings").onclick = async () => {
  try {
    await api("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        anthropic_api_key: $("anthropicKey").value, pexels_api_key: $("pexelsKey").value,
        broll_folder: $("brollFolder").value, whisper_model: $("whisperModel").value,
      }),
    });
    $("settingsMsg").textContent = "Saved.";
    refreshStatus();
    setTimeout(() => $("settings").classList.add("hidden"), 500);
  } catch (e) { $("settingsMsg").textContent = e.message; }
};

async function uploadFiles(url, field, files, extra = {}) {
  const form = new FormData();
  for (const f of files) form.append(field, f);
  for (const [k, v] of Object.entries(extra)) form.append(k, v);
  $("settingsMsg").textContent = "Adding…";
  const r = await api(url, { method: "POST", body: form });
  $("settingsMsg").textContent = `Added ${r.saved} file${r.saved === 1 ? "" : "s"}.`;
  refreshStatus();
}
$("brollUpload").onchange = (e) => uploadFiles("/api/broll", "clips", e.target.files).catch((err) => ($("settingsMsg").textContent = err.message));
$("musicUpload").onchange = (e) => uploadFiles("/api/music", "tracks", e.target.files, { mood: $("musicMood").value }).catch((err) => ($("settingsMsg").textContent = err.message));

refreshStatus();
refreshList();
setInterval(refreshStatus, 15000);
