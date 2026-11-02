"""Free stock b-roll from Pexels (needs a free Pexels API key)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests

from . import config

API = "https://api.pexels.com/videos/search"


def find_and_download(query: str, api_key: str, portrait: bool, min_duration: float,
                      skip_ids: set[int] | None = None) -> tuple[Path, int] | None:
    """Search Pexels and download the best match. Returns (path, pexels_id) or None."""
    if not api_key or not query.strip():
        return None
    skip_ids = skip_ids or set()
    params = {"query": query, "per_page": 10, "orientation": "portrait" if portrait else "landscape"}
    resp = requests.get(API, params=params, headers={"Authorization": api_key}, timeout=20)
    resp.raise_for_status()
    videos = resp.json().get("videos", [])
    videos.sort(key=lambda v: (v.get("duration", 0) < min_duration, 0))
    for v in videos:
        if v.get("id") in skip_ids:
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
        if not files:
            continue
        target = 1920 if not portrait else 1080
        files.sort(key=lambda f: abs((f.get("width") or 0) - target))
        link = files[0]["link"]
        out_dir = config.CACHE_DIR / "stock"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"pexels_{v['id']}_{hashlib.md5(link.encode()).hexdigest()[:6]}.mp4"
        if not out.exists():
            tmp = out.with_suffix(".part")
            with requests.get(link, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 16):
                        fh.write(chunk)
            tmp.replace(out)
        return out, v["id"]
    return None
