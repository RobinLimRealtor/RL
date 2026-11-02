"""Where things live on disk, and the user's saved settings."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
WORKSPACE = Path(os.environ.get("AUTOEDIT_WORKSPACE", APP_DIR / "workspace"))
JOBS_DIR = WORKSPACE / "jobs"
CACHE_DIR = WORKSPACE / "cache"
LIBRARY_DIR = APP_DIR / "library"
MUSIC_DIR = LIBRARY_DIR / "music"
SFX_DIR = LIBRARY_DIR / "sfx"
DEFAULT_BROLL_DIR = APP_DIR / "my-broll"
SETTINGS_FILE = WORKSPACE / "settings.json"

DEFAULTS = {
    "anthropic_api_key": "",
    "pexels_api_key": "",
    "claude_model": "claude-opus-5-5",
    # tiny / base / small / medium / large-v3. "small" is a good balance on a laptop.
    "whisper_model": "small",
    "broll_folder": str(DEFAULT_BROLL_DIR),
}

_lock = threading.Lock()


def ensure_dirs() -> None:
    for d in (JOBS_DIR, CACHE_DIR, MUSIC_DIR, SFX_DIR, DEFAULT_BROLL_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    with _lock:
        data = dict(DEFAULTS)
        if SETTINGS_FILE.exists():
            try:
                data.update(json.loads(SETTINGS_FILE.read_text("utf-8")))
            except (OSError, ValueError):
                pass
    # Environment variables win, so power users can skip the settings screen.
    if os.environ.get("ANTHROPIC_API_KEY"):
        data["anthropic_api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("PEXELS_API_KEY"):
        data["pexels_api_key"] = os.environ["PEXELS_API_KEY"]
    return data


def save_settings(changes: dict) -> dict:
    with _lock:
        data = dict(DEFAULTS)
        if SETTINGS_FILE.exists():
            try:
                data.update(json.loads(SETTINGS_FILE.read_text("utf-8")))
            except (OSError, ValueError):
                pass
        for key, value in changes.items():
            if key in DEFAULTS and isinstance(value, str):
                data[key] = value.strip()
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), "utf-8")
    return load_settings()
