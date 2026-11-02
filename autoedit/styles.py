"""Saved editing styles, learned from a reference video the user likes.

We only *measure* the reference (cut speed, look of the captions, music feel);
none of its footage or audio is ever used in an edit.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import config
from .ffmpeg_tools import detect_scenes, extract_audio, extract_frame, probe
from .library import MOODS
from .planner import CAPTION_STYLES, FORMATS, _call_claude, _parse_json

STYLE_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "description": "2-3 plain sentences describing how the reference is edited."},
        "instructions": {"type": "string", "description": "Concrete editing instructions to reproduce this style on other footage."},
        "format": {"type": "string", "enum": FORMATS},
        "captions_enabled": {"type": "boolean"},
        "caption_style": {"type": "string", "enum": CAPTION_STYLES},
        "caption_position": {"type": "string", "enum": ["bottom", "middle"]},
        "words_per_line": {"type": "integer"},
        "music_mood": {"type": "string", "enum": MOODS},
        "music_volume": {"type": "number"},
        "sound_on_cuts": {"type": "string", "enum": ["none", "whoosh", "swoosh", "pop"]},
        "tightness": {"type": "string", "enum": ["loose", "normal", "tight"]},
    },
    "required": ["description", "instructions", "format", "captions_enabled", "caption_style", "caption_position",
                 "words_per_line", "music_mood", "music_volume", "sound_on_cuts", "tightness"],
    "additionalProperties": False,
}

_PROMPT = """These are {n} frames, in order, from a short video a real-estate agent likes and wants their
own videos to be edited like. Facts measured from the video: {facts}.
Study the EDITING STYLE only (not the property): framing/shape, how fast it cuts, captions or text on
screen (are there any, size, position, bold/clean/boxed look, how many words at a time), colour/mood,
and what music and sound effects would fit. Then fill in the fields. "instructions" should be written for
an editor applying this style to different footage, e.g. "Fast 1.5-2 second shots, punchy upbeat music,
big bold captions in the middle, whoosh between shots".
Caption styles: "reel" = 1-2 words at a time in the middle, key words huge in capitals with a smaller
(often italic) word next to them, popping in as spoken (typical property-tour reel); "bold" = 2-4
chunky words near the bottom with the spoken word highlighted; "clean" = classic subtitles;
"boxed" = text on a dark box. Ignore any app interface around the video (icons, like buttons,
black bars from a screen recording)."""


def styles_dir() -> Path:
    d = config.WORKSPACE / "styles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(style_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{10}", style_id or ""):
        raise KeyError(style_id)
    return styles_dir() / f"{style_id}.json"


def load(style_id: str) -> dict:
    p = _path(style_id)
    if not p.exists():
        raise KeyError(style_id)
    return json.loads(p.read_text("utf-8"))


def _save(style: dict) -> None:
    p = _path(style["id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(style, indent=1), "utf-8")
    tmp.replace(p)


def list_styles() -> list[dict]:
    out = []
    for p in styles_dir().glob("*.json"):
        try:
            out.append(json.loads(p.read_text("utf-8")))
        except (OSError, ValueError):
            pass
    return sorted(out, key=lambda s: s.get("created", 0), reverse=True)


def delete(style_id: str) -> None:
    _path(style_id).unlink(missing_ok=True)


def hints_from(ai: dict, shot_seconds: float, has_speech: bool) -> dict:
    """Plan settings to start from when this style is picked."""
    pace = round(min(6.0, max(0.8, shot_seconds)), 1) if shot_seconds else 0
    hints = {
        "format": ai.get("format", "original"),
        "captions": {
            "enabled": bool(ai.get("captions_enabled", has_speech)),
            "style": ai.get("caption_style", "bold"),
            "position": ai.get("caption_position", "bottom"),
            "words_per_line": int(max(1, min(10, ai.get("words_per_line", 3)))),
            "keywords": [],
        },
        "music": {"enabled": True, "mood": ai.get("music_mood", "upbeat"),
                  "volume": float(max(0.1, min(1.0, ai.get("music_volume", 0.5)))), "track": ""},
        "sfx": {"on_broll": ai.get("sound_on_cuts", "whoosh"), "items": []},
        "cut": {"tightness": ai.get("tightness", "normal")},
        "pace": {"max_shot_seconds": pace},
    }
    return hints


def _basic_ai(info, shot_seconds, has_speech) -> dict:
    fast = shot_seconds and shot_seconds < 2.5
    return {
        "description": (f"{'Fast' if fast else 'Relaxed'} edit, about {shot_seconds:.1f} seconds per shot, "
                        f"{'vertical' if info.height > info.width else 'landscape'}"
                        f"{', with talking' if has_speech else ', no talking'}."),
        "instructions": "Match the shot length and shape of the reference.",
        "format": "vertical" if info.height > info.width else "landscape",
        "captions_enabled": has_speech, "caption_style": "bold", "caption_position": "bottom",
        "words_per_line": 3, "music_mood": "upbeat" if fast else "calm", "music_volume": 0.5 if has_speech else 0.8,
        "sound_on_cuts": "whoosh" if fast else "none", "tightness": "tight" if fast else "normal",
    }


def analyze(style_id: str, video: Path) -> None:
    settings = config.load_settings()
    api_key = settings.get("anthropic_api_key", "")
    model = settings.get("claude_model") or config.DEFAULTS["claude_model"]
    style = load(style_id)
    info = probe(video)
    _update(style, step="Measuring the cuts")
    scenes = detect_scenes(video) if info.has_video else []
    shot_seconds = round(info.duration / (len(scenes) + 1), 2) if info.duration else 0

    has_speech, wpm = False, 0
    if info.has_audio:
        _update(style, step="Listening for talking")
        try:
            from .transcribe import transcribe

            wav = video.with_suffix(".wav")
            extract_audio(video, wav)
            words = transcribe(wav, settings.get("whisper_model") or "small")["words"]
            wav.unlink(missing_ok=True)
            has_speech = len(words) >= max(5, info.duration / 6)
            wpm = round(len(words) / max(info.duration / 60, 0.01))
        except Exception:  # noqa: BLE001 - talking detection is a nice-to-have
            traceback.print_exc()

    ai = _basic_ai(info, shot_seconds, has_speech)
    if api_key and info.has_video:
        _update(style, step="Studying the look")
        frames = []
        n = 8
        for i in range(n):
            fp = video.parent / f"{style_id}_f{i}.jpg"
            try:
                extract_frame(video, info.duration * (i + 0.5) / n, fp, 512)
                frames.append(fp)
            except Exception:  # noqa: BLE001
                pass
        facts = (f"{info.duration:.0f}s long, {'vertical' if info.height > info.width else 'landscape'}, "
                 f"{len(scenes)} cuts (about {shot_seconds:.1f}s per shot), "
                 f"{'has talking (' + str(wpm) + ' words/min)' if has_speech else 'no talking (music only)'}")
        import base64

        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                "data": base64.b64encode(f.read_bytes()).decode()}} for f in frames]
        content.append({"type": "text", "text": _PROMPT.format(n=len(frames), facts=facts)})
        try:
            ai.update(_parse_json(_call_claude(api_key, model, "You analyse video editing styles.", content,
                                               8000, STYLE_SCHEMA)))
        finally:
            for f in frames:
                f.unlink(missing_ok=True)
    style.update(
        status="ready", step="", description=ai["description"], instructions=ai["instructions"],
        shot_seconds=shot_seconds, cuts=len(scenes), has_speech=has_speech,
        hints=hints_from(ai, shot_seconds, has_speech),
    )
    _save(style)


def _update(style: dict, **changes) -> None:
    style.update(changes)
    _save(style)


def create(name: str, save_upload, upload_name: str) -> dict:
    style_id = uuid.uuid4().hex[:10]
    ref_dir = styles_dir() / "references"
    ref_dir.mkdir(exist_ok=True)
    ext = Path(upload_name).suffix.lower()
    ext = ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext or "") else ".mp4"
    video = ref_dir / f"{style_id}{ext}"
    save_upload(video)
    style = {"id": style_id, "name": (name or "My style").strip()[:60], "created": time.time(),
             "status": "analyzing", "step": "Starting", "error": ""}
    _save(style)

    def run():
        try:
            analyze(style_id, video)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            s = load(style_id)
            s.update(status="error", step="", error=str(exc)[-600:])
            _save(s)
        finally:
            video.unlink(missing_ok=True)  # the reference is not kept

    threading.Thread(target=run, daemon=True).start()
    return style
