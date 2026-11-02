"""Music, sound effects and your own b-roll clips."""

from __future__ import annotations

import json
import random
import re
import wave
from pathlib import Path

import numpy as np

from . import config
from .ffmpeg_tools import extract_frame, probe

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".mts"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MOODS = ["upbeat", "calm", "inspiring", "luxury", "dramatic", "fun"]
SFX_NAMES = ["whoosh", "pop", "ding", "swoosh"]
SR = 44100


# ---------------------------------------------------------------- audio synth

def _write_wav(path: Path, samples: np.ndarray) -> None:
    samples = np.clip(samples, -1, 1)
    if samples.ndim == 1:
        samples = np.stack([samples, samples], axis=1)
    data = (samples * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def _lowpass_sweep(noise: np.ndarray, cutoffs: np.ndarray) -> np.ndarray:
    out = np.zeros_like(noise)
    y = 0.0
    alphas = 1 - np.exp(-2 * np.pi * cutoffs / SR)
    for i, x in enumerate(noise):
        y += alphas[i] * (x - y)
        out[i] = y
    return out


def _sfx(name: str) -> np.ndarray:
    rng = np.random.default_rng(7)
    if name in ("whoosh", "swoosh"):
        dur = 0.65 if name == "whoosh" else 0.4
        n = int(SR * dur)
        t = np.linspace(0, 1, n)
        env = np.sin(np.pi * t ** 0.7) ** 2
        cut = 300 + 5000 * np.sin(np.pi * t) ** 2
        sig = _lowpass_sweep(rng.standard_normal(n), cut)
        sig -= _lowpass_sweep(sig, np.full(n, 120.0))  # remove rumble
        sig = sig / (np.abs(sig).max() + 1e-9) * env * 0.8
        pan = np.linspace(-0.6, 0.6, n)
        return np.stack([sig * (1 - pan) / 1.6, sig * (1 + pan) / 1.6], axis=1)
    if name == "pop":
        n = int(SR * 0.09)
        t = np.arange(n) / SR
        freq = 900 * np.exp(-t * 25) + 250
        phase = 2 * np.pi * np.cumsum(freq) / SR
        return np.sin(phase) * np.exp(-t * 45) * 0.7
    if name == "ding":
        n = int(SR * 1.0)
        t = np.arange(n) / SR
        tone = np.sin(2 * np.pi * 1318.5 * t) + 0.4 * np.sin(2 * np.pi * 2637 * t)
        return tone * np.exp(-t * 4.5) * np.minimum(1, t * 400) * 0.35
    raise ValueError(name)


_CHORDS = [  # C, G, Am, F  (Hz, roughly)
    [261.6, 329.6, 392.0], [196.0, 246.9, 392.0], [220.0, 261.6, 329.6], [174.6, 220.0, 349.2],
]


def _music(mood: str, seconds: float = 32.0) -> np.ndarray:
    """A simple, royalty-free placeholder bed so music works out of the box."""
    n = int(SR * seconds)
    t = np.arange(n) / SR
    bar = seconds / 8
    out = np.zeros(n)
    for i in range(8):
        a, b = int(i * bar * SR), int((i + 1) * bar * SR)
        tt = t[a:b] - t[a]
        env = np.minimum(1, tt * 3) * np.minimum(1, (bar - tt) * 3)
        for f in _CHORDS[i % 4]:
            out[a:b] += env * (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(4 * np.pi * f * tt)) / 6
        bass = _CHORDS[i % 4][0] / 2
        out[a:b] += env * np.sin(2 * np.pi * bass * tt) * 0.25
    if mood in ("upbeat", "fun", "inspiring", "dramatic"):
        beat = 60 / 104
        rng = np.random.default_rng(3)
        k = int(SR * 0.25)
        kt = np.arange(k) / SR
        kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-kt * 30)) * kt) * np.exp(-kt * 14) * 0.55
        h = int(SR * 0.05)
        hat = rng.standard_normal(h) * np.exp(-np.arange(h) / SR * 90) * 0.08
        pos = 0.0
        step = 0
        while pos < seconds - 0.3:
            i = int(pos * SR)
            if step % 2 == 0:
                out[i:i + k] += kick[: len(out[i:i + k])]
            j = int((pos + beat / 2) * SR)
            out[j:j + h] += hat[: len(out[j:j + h])]
            pos += beat
            step += 1
    out /= np.abs(out).max() + 1e-9
    return out * 0.6


def ensure_builtin_audio() -> None:
    config.SFX_DIR.mkdir(parents=True, exist_ok=True)
    for name in SFX_NAMES:
        path = config.SFX_DIR / f"{name}.wav"
        if not any(config.SFX_DIR.glob(f"{name}.*")):
            _write_wav(path, _sfx(name))
    builtin = config.MUSIC_DIR / "_built-in"
    builtin.mkdir(parents=True, exist_ok=True)
    for mood in ("calm", "upbeat"):
        path = builtin / f"{mood}.wav"
        if not path.exists():
            _write_wav(path, _music(mood))


# ---------------------------------------------------------------- music + sfx

def music_tracks() -> list[dict]:
    """Every track in library/music. Sub-folder name = mood."""
    tracks = []
    for p in sorted(config.MUSIC_DIR.rglob("*")):
        if p.suffix.lower() in AUDIO_EXT and p.is_file():
            rel = p.relative_to(config.MUSIC_DIR)
            mood = rel.parts[0].lower() if len(rel.parts) > 1 else ""
            tracks.append({"file": rel.as_posix(), "mood": mood, "builtin": mood == "_built-in"})
    return tracks


def pick_music(mood: str, track: str = "", rng: random.Random | None = None) -> Path | None:
    rng = rng or random.Random()
    tracks = music_tracks()
    if track:
        for t in tracks:
            if t["file"] == track or Path(t["file"]).name == track:
                return config.MUSIC_DIR / t["file"]
    real = [t for t in tracks if not t["builtin"]]
    same_mood = [t for t in real if t["mood"] == mood.lower()]
    pool = same_mood or real
    if pool:
        return config.MUSIC_DIR / rng.choice(pool)["file"]
    fallback = "calm" if mood.lower() in ("calm", "luxury") else "upbeat"
    path = config.MUSIC_DIR / "_built-in" / f"{fallback}.wav"
    return path if path.exists() else None


def sfx_path(name: str) -> Path | None:
    for p in sorted(config.SFX_DIR.glob(f"{name}.*")):
        if p.suffix.lower() in AUDIO_EXT:
            return p
    return None


def sfx_names() -> list[str]:
    return sorted({p.stem for p in config.SFX_DIR.iterdir() if p.suffix.lower() in AUDIO_EXT})


# ---------------------------------------------------------------- own b-roll

def _tags_file(folder: Path) -> Path:
    return folder / ".autoedit-tags.json"


def _words_from_name(name: str) -> str:
    return re.sub(r"[_\-.]+", " ", Path(name).stem).strip()


def scan_broll(folder: Path, describe=None, progress=None) -> list[dict]:
    """List the user's b-roll clips, with a short description of each.

    `describe(frame_paths) -> str` is optional (Claude looks at frames). Results are
    cached next to the clips so each clip is only described once.
    """
    if not folder.exists():
        return []
    files = [p for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix.lower() in VIDEO_EXT]
    cache = {}
    tf = _tags_file(folder)
    if tf.exists():
        try:
            cache = json.loads(tf.read_text("utf-8"))
        except ValueError:
            cache = {}
    clips, changed = [], False
    for i, p in enumerate(files):
        rel = p.relative_to(folder).as_posix()
        stat = p.stat()
        key = f"{stat.st_size}-{int(stat.st_mtime)}"
        entry = cache.get(rel)
        if not entry or entry.get("key") != key or (describe and not entry.get("ai")):
            try:
                info = probe(p)
            except Exception:  # noqa: BLE001 - skip unreadable files
                continue
            desc, ai = _words_from_name(rel), False
            if describe and info.duration > 0:
                frames_dir = config.CACHE_DIR / "frames"
                frames_dir.mkdir(parents=True, exist_ok=True)
                frames = []
                for k, frac in enumerate((0.2, 0.5, 0.8)):
                    fp = frames_dir / f"{abs(hash(rel + key))}_{k}.jpg"
                    try:
                        extract_frame(p, info.duration * frac, fp)
                        frames.append(fp)
                    except Exception:  # noqa: BLE001
                        pass
                if frames:
                    try:
                        desc = f"{describe(frames)} (file: {_words_from_name(rel)})"
                        ai = True
                    except Exception:  # noqa: BLE001 - keep filename description
                        pass
            entry = {"key": key, "duration": round(info.duration, 2), "description": desc, "ai": ai,
                     "portrait": info.height > info.width}
            cache[rel] = entry
            changed = True
        clips.append({"file": rel, **{k: entry[k] for k in ("duration", "description", "portrait")}})
        if progress:
            progress((i + 1) / max(1, len(files)))
    if changed:
        try:
            tf.write_text(json.dumps(cache, indent=1), "utf-8")
        except OSError:
            pass
    return clips
