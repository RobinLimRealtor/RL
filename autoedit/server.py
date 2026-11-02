"""The local web app (only reachable from this computer)."""

from __future__ import annotations

import re
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from . import __version__, config, jobs, library

STATIC = Path(__file__).parent / "static"
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = None


def _mask(key: str) -> str:
    return (key[:7] + "…" + key[-4:]) if len(key) > 14 else ("set" if key else "")


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:name>")
def static_files(name):
    return send_from_directory(STATIC, name)


@app.get("/api/status")
def status():
    s = config.load_settings()
    folder = Path(s["broll_folder"])
    clips = [p for p in folder.rglob("*") if p.suffix.lower() in library.VIDEO_EXT] if folder.exists() else []
    tracks = [t for t in library.music_tracks() if not t["builtin"]]
    return jsonify({
        "version": __version__,
        "claude": bool(s["anthropic_api_key"]),
        "stock": bool(s["pixabay_api_key"] or s["pexels_api_key"]),
        "broll_folder": str(folder),
        "broll_count": len(clips),
        "music_folder": str(config.MUSIC_DIR),
        "music_count": len(tracks),
        "sfx": library.sfx_names(),
    })


@app.get("/api/settings")
def get_settings():
    s = config.load_settings()
    return jsonify({
        "anthropic_api_key": _mask(s["anthropic_api_key"]),
        "pixabay_api_key": _mask(s["pixabay_api_key"]),
        "pexels_api_key": _mask(s["pexels_api_key"]),
        "whisper_model": s["whisper_model"],
        "claude_model": s["claude_model"],
        "broll_folder": s["broll_folder"],
    })


@app.post("/api/settings")
def post_settings():
    data = request.get_json(force=True) or {}
    changes = {}
    for key in ("anthropic_api_key", "pixabay_api_key", "pexels_api_key"):
        val = data.get(key)
        if isinstance(val, str) and "…" not in val and val != "set":
            changes[key] = val
    if data.get("whisper_model") in ("tiny", "base", "small", "medium", "large-v3"):
        changes["whisper_model"] = data["whisper_model"]
    if isinstance(data.get("claude_model"), str) and re.fullmatch(r"[a-z0-9.\-]{3,60}", data["claude_model"]):
        changes["claude_model"] = data["claude_model"]
    if isinstance(data.get("broll_folder"), str) and data["broll_folder"].strip():
        folder = Path(data["broll_folder"].strip()).expanduser()
        if not folder.is_dir():
            return jsonify({"error": f"That folder doesn't exist: {folder}"}), 400
        changes["broll_folder"] = str(folder)
    config.save_settings(changes)
    return get_settings()


@app.post("/api/broll")
def upload_broll():
    folder = Path(config.load_settings()["broll_folder"])
    folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for f in request.files.getlist("clips"):
        name = secure_filename(f.filename or "")
        if name and Path(name).suffix.lower() in library.VIDEO_EXT:
            f.save(folder / name)
            saved += 1
    return jsonify({"saved": saved})


@app.post("/api/music")
def upload_music():
    mood = request.form.get("mood", "")
    if mood not in library.MOODS:
        mood = ""
    folder = config.MUSIC_DIR / mood if mood else config.MUSIC_DIR
    folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for f in request.files.getlist("tracks"):
        name = secure_filename(f.filename or "")
        if name and Path(name).suffix.lower() in library.AUDIO_EXT:
            f.save(folder / name)
            saved += 1
    return jsonify({"saved": saved})


def _public(job: dict) -> dict:
    out = dict(job)
    out["versions"] = [{k: v for k, v in ver.items() if k != "plan"} for ver in job.get("versions", [])]
    return out


@app.get("/api/jobs")
def get_jobs():
    return jsonify([_public(j) for j in jobs.list_jobs()])


@app.post("/api/jobs")
def new_job():
    f = request.files.get("video")
    if not f or not f.filename:
        return jsonify({"error": "Please choose a video file."}), 400
    prompt = (request.form.get("prompt") or "").strip()[:4000]
    job = jobs.create(f.filename, f.save, prompt)
    return jsonify(_public(job))


def _job_or_404(job_id):
    try:
        return jobs.load(job_id)
    except KeyError:
        abort(404)


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    return jsonify(_public(_job_or_404(job_id)))


@app.post("/api/jobs/<job_id>/revise")
def revise(job_id):
    job = _job_or_404(job_id)
    if job["status"] in ("queued", "working"):
        return jsonify({"error": "Still working on the last change. Try again when it's done."}), 409
    prompt = ((request.get_json(force=True) or {}).get("prompt") or "").strip()[:4000]
    if not prompt:
        return jsonify({"error": "Type what you'd like changed."}), 400
    jobs.enqueue(job_id, prompt)
    return jsonify(_public(jobs.load(job_id)))


@app.get("/api/jobs/<job_id>/plan/<int:n>")
def get_plan(job_id, n):
    job = _job_or_404(job_id)
    for v in job["versions"]:
        if v["n"] == n:
            return jsonify(v["plan"])
    abort(404)


def _version_file(job_id, n, key):
    job = _job_or_404(job_id)
    for v in job["versions"]:
        if v["n"] == n:
            return jobs.job_dir(job_id) / v[key], job
    abort(404)


@app.get("/api/jobs/<job_id>/video/<int:n>")
def get_video(job_id, n):
    path, job = _version_file(job_id, n, "file")
    download = request.args.get("download")
    name = f"{Path(job['name']).stem}-edit-v{n}.mp4"
    return send_file(path, mimetype="video/mp4", conditional=True, as_attachment=bool(download), download_name=name)


@app.get("/api/jobs/<job_id>/srt/<int:n>")
def get_srt(job_id, n):
    path, job = _version_file(job_id, n, "srt")
    return send_file(path, mimetype="text/plain", as_attachment=True,
                     download_name=f"{Path(job['name']).stem}-captions-v{n}.srt")


@app.get("/api/jobs/<job_id>/thumb/<int:n>")
def get_thumb(job_id, n):
    path = jobs.job_dir(job_id) / f"thumb_v{n}.jpg"
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/jpeg")
