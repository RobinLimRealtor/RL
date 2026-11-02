"""The "editor brain": turns your prompt + the transcript into an edit plan.

With an Anthropic API key, Claude writes the plan. Without one, simple rules
read keywords in your prompt (captions, music, vertical, tight, b-roll...).
"""

from __future__ import annotations

import base64
import copy
import json
import re
from pathlib import Path

from .library import MOODS

FORMATS = ["original", "landscape", "vertical", "square"]
CAPTION_STYLES = ["bold", "clean", "boxed"]

DEFAULT_PLAN = {
    "summary": "",
    "format": "original",
    "cut": {"enabled": True, "tightness": "normal", "remove_fillers": True, "remove_ranges": []},
    "captions": {"enabled": True, "style": "bold", "position": "bottom", "words_per_line": 3},
    "broll": [],
    "music": {"enabled": True, "mood": "upbeat", "track": "", "volume": 0.5, "duck": True},
    "sfx": {"on_broll": "whoosh", "items": []},
}

_range = {
    "type": "object",
    "properties": {"start": {"type": "number"}, "end": {"type": "number"}, "reason": {"type": "string"}},
    "required": ["start", "end", "reason"],
    "additionalProperties": False,
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-4 plain sentences for the user: what you did and why."},
        "format": {"type": "string", "enum": FORMATS},
        "cut": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "tightness": {"type": "string", "enum": ["loose", "normal", "tight"]},
                "remove_fillers": {"type": "boolean"},
                "remove_ranges": {"type": "array", "items": _range},
            },
            "required": ["enabled", "tightness", "remove_fillers", "remove_ranges"],
            "additionalProperties": False,
        },
        "captions": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "style": {"type": "string", "enum": CAPTION_STYLES},
                "position": {"type": "string", "enum": ["bottom", "middle"]},
                "words_per_line": {"type": "integer"},
            },
            "required": ["enabled", "style", "position", "words_per_line"],
            "additionalProperties": False,
        },
        "broll": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "source": {"type": "string", "enum": ["own", "stock"]},
                    "clip": {"type": "string", "description": "Own clip file name, or empty for stock."},
                    "query": {"type": "string", "description": "Short stock-footage search, e.g. 'modern kitchen'."},
                },
                "required": ["start", "end", "source", "clip", "query"],
                "additionalProperties": False,
            },
        },
        "music": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "mood": {"type": "string", "enum": MOODS},
                "track": {"type": "string", "description": "Exact track file from the list, or empty to pick by mood."},
                "volume": {"type": "number", "description": "0.0-1.0. 0.5 is normal background level."},
                "duck": {"type": "boolean", "description": "Lower the music while someone talks."},
            },
            "required": ["enabled", "mood", "track", "volume", "duck"],
            "additionalProperties": False,
        },
        "sfx": {
            "type": "object",
            "properties": {
                "on_broll": {"type": "string", "description": "Sound to play as each b-roll clip starts, or 'none'."},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"time": {"type": "number"}, "sound": {"type": "string"}},
                        "required": ["time", "sound"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["on_broll", "items"],
            "additionalProperties": False,
        },
    },
    "required": ["summary", "format", "cut", "captions", "broll", "music", "sfx"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You are a professional video editor working inside an automatic editing app.
The user is a busy real-estate agent and content creator. They give you a raw video's transcript
and a request in plain words, and you return an edit plan that the app renders into a finished,
ready-to-post video. Make tasteful choices a good human editor would make; the user should not
need to touch anything afterwards.

How the plan works:
- All times are in seconds on the ORIGINAL video's timeline (the transcript's timeline).
- cut: removes dead space automatically (pauses between words, filler words when
  remove_fillers is true). tightness: loose (keeps ~0.75s pauses), normal (~0.4s), tight (~0.2s).
  remove_ranges removes extra parts: false starts, repeated takes (keep the LAST good take),
  "wait let me start again" moments, off-topic rambles, or anything the user asks to cut.
  Use transcript times for range boundaries.
- captions: burned-in subtitles from the transcript. style bold = big punchy words with the
  current word highlighted (social media look), clean = classic subtitles, boxed = text on a dark
  box. words_per_line 2-4 for bold, 5-8 for clean/boxed.
- broll: cutaway clips placed OVER the voice (voice keeps playing). Choose moments where the
  speaker describes something visual. Each 2-6 seconds, not overlapping, at least ~4 seconds
  apart, never in the first 2 seconds. Prefer the user's own clips (source "own", clip = exact
  file name from the list) when one fits; otherwise source "stock" with a short concrete search
  query. If no stock is available, only use own clips. Don't add b-roll if the user says not to.
- music: background track picked by mood (or an exact track file from the list). It is lowered
  automatically under speech when duck is true. volume 0.5 is normal; quieter ~0.3, louder ~0.8.
  For videos without speech (e.g. a silent home tour), music carries the video: volume ~0.8.
- sfx: on_broll plays a sound as each b-roll clip appears (e.g. "whoosh") or "none".
  items are extra one-off sounds at specific times (e.g. a "ding" on a price reveal). Use only
  sound names from the list. Keep it tasteful: a few well-placed sounds, not dozens.
- format: original keeps the camera's shape; vertical = 9:16 (Reels/TikTok/Shorts),
  landscape = 16:9 (YouTube), square = 1:1.

For follow-up requests you receive the previous plan: change only what the user asks and keep
everything else exactly as it was. Write the summary to the user in plain, friendly words
(no jargon, no timestamps lists), saying what changed."""


# ---------------------------------------------------------------- helpers

def _fmt_time(t: float) -> str:
    return f"{t:.2f}"


def transcript_text(transcript: dict, max_chars: int = 120_000) -> str:
    lines = [f"[{_fmt_time(s['start'])}-{_fmt_time(s['end'])}] {s['text']}" for s in transcript.get("segments", [])]
    text = "\n".join(lines) or "(no speech detected - this video has no talking)"
    return text[:max_chars]


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def normalize_plan(plan: dict, duration: float, own_clips: list[dict], sfx_names: list[str]) -> dict:
    """Fill in missing fields and drop anything that can't be rendered."""
    out = copy.deepcopy(DEFAULT_PLAN)
    for key in out:
        if key in plan:
            if isinstance(out[key], dict) and isinstance(plan[key], dict):
                out[key].update(plan[key])
            else:
                out[key] = plan[key]
    if out["format"] not in FORMATS:
        out["format"] = "original"
    cut = out["cut"]
    if cut.get("tightness") not in ("loose", "normal", "tight"):
        cut["tightness"] = "normal"
    cut["remove_ranges"] = [
        {"start": _clamp(r.get("start"), 0, duration, 0), "end": _clamp(r.get("end"), 0, duration, 0),
         "reason": str(r.get("reason", ""))}
        for r in cut.get("remove_ranges", []) if isinstance(r, dict)
    ]
    cut["remove_ranges"] = [r for r in cut["remove_ranges"] if r["end"] > r["start"]]
    cap = out["captions"]
    if cap.get("style") not in CAPTION_STYLES:
        cap["style"] = "bold"
    if cap.get("position") not in ("bottom", "middle"):
        cap["position"] = "bottom"
    cap["words_per_line"] = int(_clamp(cap.get("words_per_line"), 1, 10, 3))
    own_names = {c["file"]: c for c in own_clips}
    by_basename = {Path(c["file"]).name: c["file"] for c in own_clips}
    broll, last_end = [], -1.0
    for b in sorted((b for b in out.get("broll", []) if isinstance(b, dict)), key=lambda b: float(b.get("start", 0))):
        s = _clamp(b.get("start"), 0, duration, -1)
        e = _clamp(b.get("end"), 0, duration, -1)
        if s < 0 or e - s < 1.0 or s < last_end:
            continue
        e = min(e, s + 8.0)
        clip = str(b.get("clip", "") or "")
        clip = clip if clip in own_names else by_basename.get(clip, "")
        source = "own" if clip else "stock"
        query = str(b.get("query", "") or "")
        if source == "stock" and not query:
            continue
        broll.append({"start": round(s, 2), "end": round(e, 2), "source": source, "clip": clip, "query": query})
        last_end = e
    out["broll"] = broll
    mus = out["music"]
    if mus.get("mood") not in MOODS:
        mus["mood"] = "upbeat"
    mus["volume"] = _clamp(mus.get("volume"), 0.0, 1.0, 0.5)
    sfx = out["sfx"]
    if sfx.get("on_broll") not in sfx_names:
        sfx["on_broll"] = "none"
    sfx["items"] = [
        {"time": _clamp(i.get("time"), 0, duration, 0), "sound": i["sound"]}
        for i in sfx.get("items", []) if isinstance(i, dict) and i.get("sound") in sfx_names
    ][:40]
    out["summary"] = str(out.get("summary") or "")
    return out


# ---------------------------------------------------------------- Claude

def _client(api_key: str):
    import anthropic

    return anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=600)


def _call_claude(api_key: str, model: str, system: str, content: list, max_tokens: int,
                 schema: dict | None = None) -> str:
    import anthropic

    client = _client(api_key)
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "thinking": {"type": "adaptive"},
    }
    output_config = {"effort": "medium"}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    kwargs["output_config"] = output_config
    try:
        # If a safety check declines, let the API retry on its recommended fallback model.
        with client.beta.messages.stream(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.BadRequestError:
        with client.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("Claude declined to plan this edit. Try rewording your request.")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if not text.strip():
        raise RuntimeError(f"Claude returned no plan (stop reason: {msg.stop_reason}).")
    return text


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def plan_with_claude(*, api_key, model, prompt, transcript, duration, video_shape, own_clips,
                     stock_available, music_tracks, sfx_names, previous_plan=None, history=None) -> dict:
    own_list = "\n".join(
        f"- {c['file']} ({c['duration']}s, {'portrait' if c['portrait'] else 'landscape'}): {c['description']}"
        for c in own_clips[:300]
    ) or "(none)"
    tracks = "\n".join(
        f"- {t['file']} (mood: {t['mood'] or 'unsorted'})" for t in music_tracks if not t["builtin"]
    ) or "(no tracks added yet; built-in placeholder beds will be used, pick by mood)"
    parts = [
        f"Video length: {duration:.1f}s. Shape: {video_shape}.",
        f"Stock footage available: {'yes' if stock_available else 'no (only own clips can be used)'}.",
        f"Sound effects available: {', '.join(sfx_names) or 'none'}.",
        f"Music tracks:\n{tracks}",
        f"User's own b-roll clips:\n{own_list}",
        f"Transcript (original timeline):\n{transcript_text(transcript)}",
    ]
    if previous_plan is not None:
        earlier = "\n".join(f"- {h}" for h in (history or [])) or "(none)"
        parts.append(f"Earlier requests for this video:\n{earlier}")
        parts.append("Current plan (JSON):\n" + json.dumps(previous_plan, indent=1))
        parts.append(f"Follow-up request from the user: {prompt}")
    else:
        parts.append(f"User's request: {prompt or '(no request: make a great standard edit)'}")
    content = [{"type": "text", "text": "\n\n".join(parts)}]
    text = _call_claude(api_key, model, SYSTEM_PROMPT, content, 32000, PLAN_SCHEMA)
    return _parse_json(text)


def describe_frames(api_key: str, model: str, frames: list[Path]) -> str:
    content = []
    for f in frames:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.b64encode(Path(f).read_bytes()).decode()}})
    content.append({"type": "text", "text": "These are frames from one short b-roll clip. Describe what it shows "
                    "in one short line (max 15 words), for picking b-roll later. Example: "
                    "'Bright modern kitchen with white quartz island, slow pan'. Reply with the line only."})
    return _call_claude(api_key, model, "You label video clips for an editing app.", content, 2000).strip()[:200]


# ---------------------------------------------------------------- rules (no API key)

def _has(p: str, *words) -> bool:
    return any(re.search(rf"\b{w}\b", p) for w in words)


def _keyword_broll(transcript: dict, own_clips: list[dict], duration: float) -> list[dict]:
    """Match own clips to sentences that share words with the clip description."""
    stop = {"the", "a", "and", "with", "of", "in", "on", "to", "is", "this", "that", "file", "clip", "shot", "mp4", "mov"}
    clip_words = []
    for c in own_clips:
        words = {w for w in re.findall(r"[a-z]+", c["description"].lower()) if w not in stop and len(w) > 2}
        clip_words.append((c, words))
    used, picks, last_end = set(), [], -10.0
    for seg in transcript.get("segments", []):
        if seg["start"] < 2 or seg["start"] < last_end + 4:
            continue
        words = {w for w in re.findall(r"[a-z]+", seg["text"].lower()) if len(w) > 2}
        best = max(
            ((len(words & cw) - (5 if c["file"] in used else 0), c) for c, cw in clip_words),
            key=lambda x: x[0], default=(0, None),
        )
        if best[0] > 0 and best[1]:
            c = best[1]
            end = min(seg["start"] + min(5.0, c["duration"]), duration)
            picks.append({"start": seg["start"], "end": end, "source": "own", "clip": c["file"], "query": ""})
            used.add(c["file"])
            last_end = end
    return picks


def plan_with_rules(prompt, transcript, own_clips, previous_plan=None) -> dict:
    p = (prompt or "").lower()
    plan = copy.deepcopy(previous_plan) if previous_plan else copy.deepcopy(DEFAULT_PLAN)
    notes = []
    has_speech = bool(transcript.get("words"))
    if not previous_plan:
        if not has_speech:
            plan["captions"]["enabled"] = False
            plan["music"]["volume"] = 0.8
        notes.append("I removed the pauses and filler words" if has_speech else "There was no talking, so I kept the footage as is")
    if _has(p, "vertical", "reel", "reels", "tiktok", "shorts", "short", "9:16", "portrait", "instagram"):
        plan["format"] = "vertical"
        notes.append("made it vertical for Reels/TikTok")
    elif _has(p, "landscape", "youtube", "16:9", "horizontal", "widescreen"):
        plan["format"] = "landscape"
        notes.append("made it 16:9 for YouTube")
    elif _has(p, "square", "1:1"):
        plan["format"] = "square"
    if _has(p, "tight", "tighter", "fast", "faster", "snappy", "punchy"):
        plan["cut"]["tightness"] = "tight"
        notes.append("tightened the cuts")
    elif _has(p, "loose", "looser", "slower", "relaxed", "natural"):
        plan["cut"]["tightness"] = "loose"
        notes.append("left more natural pauses")
    if _has(p, "keep the pauses", "don't cut", "no cuts", "dont cut"):
        plan["cut"]["enabled"] = False
    if re.search(r"\bno (captions|subtitles)\b|without (captions|subtitles)|remove (the )?(captions|subtitles)", p):
        plan["captions"]["enabled"] = False
        notes.append("turned captions off")
    elif _has(p, "caption", "captions", "subtitles", "subtitle"):
        plan["captions"]["enabled"] = True
    if _has(p, "clean", "simple", "classic"):
        plan["captions"].update(style="clean", words_per_line=6)
        notes.append("switched to clean captions")
    elif _has(p, "boxed", "box"):
        plan["captions"].update(style="boxed", words_per_line=6)
        notes.append("switched to boxed captions")
    elif _has(p, "bold", "punchy captions"):
        plan["captions"].update(style="bold", words_per_line=3)
    if re.search(r"\bno music\b|without music|remove (the )?music|turn off (the )?music", p):
        plan["music"]["enabled"] = False
        notes.append("removed the music")
    else:
        if _has(p, "music", "song", "track"):
            plan["music"]["enabled"] = True
        for mood in MOODS:
            if _has(p, mood) and plan["music"]["mood"] != mood:
                plan["music"].update(mood=mood, track="")
                if previous_plan:
                    notes.append(f"switched to {mood} music")
        if re.search(r"less music|quieter music|music (is )?(too )?loud|lower (the )?music|softer music", p):
            plan["music"]["volume"] = round(plan["music"]["volume"] * 0.6, 2)
            notes.append("lowered the music")
        elif re.search(r"more music|louder music|music (is )?(too )?quiet|raise (the )?music", p):
            plan["music"]["volume"] = round(min(1.0, plan["music"]["volume"] * 1.4 + 0.05), 2)
            notes.append("raised the music")
    if re.search(r"\bno (sound effects|sfx|whoosh)", p):
        plan["sfx"] = {"on_broll": "none", "items": []}
    elif _has(p, "whoosh", "sound effects", "sfx", "swoosh"):
        plan["sfx"]["on_broll"] = "swoosh" if "swoosh" in p else "whoosh"
    if re.search(r"\bno b-?roll\b|remove (the )?b-?roll", p):
        plan["broll"] = []
    elif not previous_plan or _has(p, "broll", "b-roll", "roll", "cutaway", "cutaways"):
        plan["broll"] = _keyword_broll(transcript, own_clips, transcript.get("duration", 1e9)) or plan["broll"]
        if plan["broll"]:
            notes.append(f"added {len(plan['broll'])} b-roll clip(s) from your folder")
    if plan["music"]["enabled"] and not previous_plan:
        notes.append(f"added {plan['music']['mood']} background music that dips under your voice")
    if plan["captions"]["enabled"] and not previous_plan and has_speech:
        notes.append("added captions")
    text = ", ".join(notes) or "Updated the edit"
    plan["summary"] = text[0].upper() + text[1:] + "." + \
        " (Simple mode: add an Anthropic API key in Settings for smarter edits.)"
    return plan
