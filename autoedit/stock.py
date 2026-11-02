"""Free stock b-roll from Pixabay and/or Pexels (each needs its own free API key)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests

from . import config

PIXABAY_API = "https://pixabay.com/api/videos/"
PEXELS_API = "https://api.pexels.com/videos/search"


def _download(link: str, name: str) -> Path:
    out_dir = config.CACHE_DIR / "stock"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}_{hashlib.md5(link.encode()).hexdigest()[:6]}.mp4"
    if not out.exists():
        tmp = out.with_suffix(".part")
        with requests.get(link, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    fh.write(chunk)
        tmp.replace(out)
    return out


def _pixabay(query, key, portrait, min_duration, skip):
    params = {"key": key, "q": query[:100], "per_page": 20, "safesearch": "true", "video_type": "film"}
    resp = requests.get(PIXABAY_API, params=params, timeout=20)
    resp.raise_for_status()
    hits = resp.json().get("hits", [])

    def shape_ok(h):
        v = (h.get("videos") or {}).get("medium") or {}
        w, ht = v.get("width") or 0, v.get("height") or 0
        return (ht > w) == portrait

    hits.sort(key=lambda h: (not shape_ok(h), (h.get("duration") or 0) < min_duration))
    for h in hits:
        sid = f"pixabay:{h.get('id')}"
        if sid in skip:
            continue
        videos = h.get("videos") or {}
        for size in ("large", "medium", "small"):
            v = videos.get(size) or {}
            if v.get("url") and (v.get("width") or 0) >= 640:
                return _download(v["url"], f"pixabay_{h['id']}"), sid
    return None


def _pexels(query, key, portrait, min_duration, skip):
    params = {"query": query, "per_page": 10, "orientation": "portrait" if portrait else "landscape"}
    resp = requests.get(PEXELS_API, params=params, headers={"Authorization": key}, timeout=20)
    resp.raise_for_status()
    videos = resp.json().get("videos", [])
    videos.sort(key=lambda v: v.get("duration", 0) < min_duration)
    for v in videos:
        sid = f"pexels:{v.get('id')}"
        if sid in skip:
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")]
        if not files:
            continue
        target = 1080 if portrait else 1920
        files.sort(key=lambda f: abs((f.get("width") or 0) - target))
        return _download(files[0]["link"], f"pexels_{v['id']}"), sid
    return None


def available(settings: dict) -> bool:
    return bool(settings.get("pixabay_api_key") or settings.get("pexels_api_key"))


def find_and_download(query: str, settings: dict, portrait: bool, min_duration: float,
                      skip_ids: set[str] | None = None) -> tuple[Path, str] | None:
    """Search the configured stock sites and download the best match. Returns (path, id) or None."""
    if not query.strip():
        return None
    skip = skip_ids or set()
    sources = [(_pixabay, settings.get("pixabay_api_key", "")), (_pexels, settings.get("pexels_api_key", ""))]
    for fn, key in sources:
        if not key:
            continue
        try:
            found = fn(query, key, portrait, min_duration, skip)
        except requests.RequestException:
            found = None
        if found:
            return found
    return None
