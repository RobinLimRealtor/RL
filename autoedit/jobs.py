"""Runs edits in the background, one at a time, and remembers every version."""

from __future__ import annotations

import json
import queue
import random
import re
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import config, library, planner, stock
from .cutting import TimeMap, keep_segments
from .ffmpeg_tools import extract_audio, extract_frame, probe
from .render import BrollPlacement, SfxPlacement, render
from .transcribe import load_or_transcribe

_lock = threading.RLock()
_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()


# ---------------------------------------------------------------- storage

def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", job_id or ""):
        raise KeyError(job_id)
    return config.JOBS_DIR / job_id


def load(job_id: str) -> dict:
    path = job_dir(job_id) / "job.json"
    if not path.exists():
        raise KeyError(job_id)
    return json.loads(path.read_text("utf-8"))


def save(job: dict) -> None:
    with _lock:
        d = job_dir(job["id"])
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job, indent=1), "utf-8")
        tmp.replace(d / "job.json")


def update(job_id: str, **changes) -> dict:
    with _lock:
        job = load(job_id)
        job.update(changes)
        save(job)
        return job


def list_jobs() -> list[dict]:
    jobs = []
    for d in config.JOBS_DIR.glob("*/job.json"):
        try:
            jobs.append(json.loads(d.read_text("utf-8")))
        except (OSError, ValueError):
            continue
    jobs.sort(key=lambda j: j.get("created", 0), reverse=True)
    return jobs


def create(upload_name: str, save_upload, prompt: str) -> dict:
    job_id = uuid.uuid4().hex[:12]
    d = config.JOBS_DIR / job_id
    d.mkdir(parents=True)
    ext = Path(upload_name).suffix.lower() or ".mp4"
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", ext):
        ext = ".mp4"
    src = d / f"source{ext}"
    save_upload(src)
    job = {
        "id": job_id, "name": Path(upload_name).name or "video", "source": src.name,
        "created": time.time(), "status": "queued", "step": "Waiting to start", "progress": 0.0,
        "error": "", "versions": [], "pending_prompt": prompt,
    }
    save(job)
    enqueue(job_id, prompt)
    return job


def enqueue(job_id: str, prompt: str) -> None:
    update(job_id, status="queued", step="Waiting to start", progress=0.0, error="", pending_prompt=prompt)
    _queue.put((job_id, prompt))


# ---------------------------------------------------------------- pipeline

def _progress(job_id: str, step: str, start: float, span: float):
    last = [0.0]

    def cb(frac: float, msg: str = ""):
        now = time.time()
        if now - last[0] < 0.4 and frac < 1:
            return
        last[0] = now
        update(job_id, step=msg or step, progress=round(start + span * max(0.0, min(1.0, frac)), 3))

    return cb


def process(job_id: str, prompt: str) -> None:
    settings = config.load_settings()
    api_key = settings.get("anthropic_api_key", "")
    model = settings.get("claude_model") or config.DEFAULTS["claude_model"]
    has_stock = stock.available(settings)
    d = job_dir(job_id)
    job = update(job_id, status="working", step="Reading your video", progress=0.01)
    src = d / job["source"]
    info = probe(src)
    if not info.has_video:
        raise RuntimeError("This file has no picture. Please upload a video file.")
    library.ensure_builtin_audio()

    # Listen
    if info.has_audio:
        audio = d / "audio.wav"
        if not (d / "transcript.json").exists():
            update(job_id, step="Listening to your video (first time can take a few minutes)", progress=0.03)
            extract_audio(src, audio)
        transcript = load_or_transcribe(
            audio, d / "transcript.json", settings.get("whisper_model") or "small",
            _progress(job_id, "Listening to your video", 0.03, 0.27))
        if audio.exists():
            audio.unlink()
    else:
        transcript = {"language": "", "words": [], "segments": []}
    transcript["duration"] = info.duration

    # Look at the b-roll library
    update(job_id, step="Looking through your b-roll", progress=0.31)
    describe = (lambda frames: planner.describe_frames(api_key, model, frames)) if api_key else None
    folder = Path(settings.get("broll_folder") or config.DEFAULT_BROLL_DIR)
    own_clips = library.scan_broll(folder, describe, _progress(job_id, "Looking through your b-roll", 0.31, 0.05))

    # Plan
    update(job_id, step="Planning the edit", progress=0.37)
    previous = job["versions"][-1]["plan"] if job["versions"] else None
    history = [v["prompt"] for v in job["versions"]]
    shape = "portrait (vertical)" if info.height > info.width else "landscape"
    note = ""
    if api_key:
        try:
            raw = planner.plan_with_claude(
                api_key=api_key, model=model, prompt=prompt, transcript=transcript, duration=info.duration,
                video_shape=shape, own_clips=own_clips, stock_available=has_stock,
                music_tracks=library.music_tracks(), sfx_names=library.sfx_names(),
                previous_plan=previous, history=history)
        except Exception as exc:  # noqa: BLE001 - fall back to simple rules, but tell the user
            raw = planner.plan_with_rules(prompt, transcript, own_clips, previous)
            note = f" (Claude couldn't be reached: {str(exc)[:160]}. Used simple mode instead.)"
    else:
        raw = planner.plan_with_rules(prompt, transcript, own_clips, previous)
    plan = planner.normalize_plan(raw, info.duration, own_clips, library.sfx_names())

    # Work out timings on the edited timeline
    segments = keep_segments(transcript["words"], info.duration, plan["cut"], info.fps)
    tmap = TimeMap(segments)
    broll, used_stock, skipped = [], set(), 0
    portrait = plan["format"] == "vertical" or (plan["format"] == "original" and info.height > info.width)
    for i, b in enumerate(plan["broll"]):
        update(job_id, step=f"Getting b-roll ({i + 1}/{len(plan['broll'])})", progress=0.4)
        out_s, out_e = tmap.to_output(b["start"]), tmap.to_output(b["end"])
        if out_e - out_s < 1.0:
            continue
        path = None
        if b["source"] == "own":
            path = folder / b["clip"]
        elif has_stock:
            try:
                found = stock.find_and_download(b["query"], settings, portrait, out_e - out_s, used_stock)
                if found:
                    path, sid = found
                    used_stock.add(sid)
            except Exception:  # noqa: BLE001 - a missing stock clip shouldn't stop the edit
                path = None
        if not path or not path.exists():
            skipped += 1
            continue
        clip_dur = probe(path).duration
        offset = 0.4 if clip_dur > (out_e - out_s) + 1.0 else 0.0
        out_e = min(out_e, out_s + max(0.5, clip_dur - offset))
        if broll and out_s < broll[-1].out_end:
            continue
        broll.append(BrollPlacement(path, round(out_s, 3), round(out_e, 3), offset))
    if skipped:
        note += f" {skipped} b-roll spot(s) were skipped because no matching clip was found" + \
            ("" if has_stock else " (add a free Pixabay key in Settings for stock footage)") + "."

    sfx = []
    on_broll = plan["sfx"]["on_broll"]
    if on_broll != "none" and library.sfx_path(on_broll):
        for b in broll:
            sfx.append(SfxPlacement(library.sfx_path(on_broll), max(0.0, b.out_start - 0.2), 0.45))
    for item in plan["sfx"]["items"]:
        p = library.sfx_path(item["sound"])
        if p:
            sfx.append(SfxPlacement(p, tmap.to_output(item["time"]), 0.5))
    music = None
    if plan["music"]["enabled"]:
        music = library.pick_music(plan["music"]["mood"], plan["music"]["track"], random.Random(job_id))
        if music:
            plan["music"]["track"] = music.relative_to(config.MUSIC_DIR).as_posix()

    # Render
    n = len(job["versions"]) + 1
    out = d / f"edit_v{n}.mp4"
    srt = d / f"edit_v{n}.srt"
    render(src=src, info=info, plan=plan, transcript=transcript, segments=segments, broll=broll,
           music=music, sfx=sfx, out_path=out, srt_path=srt, work=d / "work",
           progress=_progress(job_id, "Editing", 0.42, 0.56))
    try:
        extract_frame(out, min(1.0, tmap.duration / 3), d / f"thumb_v{n}.jpg", 480)
    except Exception:  # noqa: BLE001
        pass
    version = {
        "n": n, "prompt": prompt, "summary": (plan["summary"] + note).strip(), "plan": plan,
        "file": out.name, "srt": srt.name, "created": time.time(),
        "duration": round(tmap.duration, 2), "original_duration": round(info.duration, 2),
        "broll_count": len(broll),
    }
    with _lock:
        job = load(job_id)
        job["versions"].append(version)
        job.update(status="done", step="Done", progress=1.0, error="", pending_prompt="")
        save(job)


def _worker() -> None:
    while True:
        job_id, prompt = _queue.get()
        try:
            process(job_id, prompt)
        except Exception as exc:  # noqa: BLE001 - show the problem in the app
            traceback.print_exc()
            try:
                update(job_id, status="error", step="Something went wrong", error=str(exc)[-1500:])
            except Exception:  # noqa: BLE001
                pass
        finally:
            _queue.task_done()


def start_worker() -> None:
    config.ensure_dirs()
    for job in list_jobs():  # anything left half-done when the app was closed
        if job.get("status") in ("queued", "working"):
            update(job["id"], status="error", step="Stopped",
                   error="The app was closed before this edit finished. Type your request again to retry.")
    threading.Thread(target=_worker, daemon=True, name="autoedit-worker").start()
