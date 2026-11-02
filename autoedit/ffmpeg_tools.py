"""Small helpers around the ffmpeg program (bundled via imageio-ffmpeg)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    override = os.environ.get("AUTOEDIT_FFMPEG")
    if override:
        return override
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - fall back to whatever is installed
        found = shutil.which("ffmpeg")
        if found:
            return found
    raise FFmpegError("Could not find ffmpeg. Run the start script again to reinstall.")


_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # hide console windows on Windows


def run(args: list[str], cwd: str | Path | None = None) -> str:
    """Run ffmpeg with the given args. Returns stderr (ffmpeg logs there)."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", *map(str, args)]
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-25:])
        raise FFmpegError(f"ffmpeg failed:\n{tail}")
    return proc.stderr


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_video: bool
    has_audio: bool


def probe(path: str | Path) -> MediaInfo:
    """Read basic facts about a media file by parsing `ffmpeg -i` output."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=_NO_WINDOW,
    )
    text = proc.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    duration = 0.0
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    width = height = 0
    fps = 30.0
    has_video = False
    for line in text.splitlines():
        if "Video:" in line and not has_video and "attached pic" not in line:
            has_video = True
            dm = re.search(r",\s*(\d{2,5})x(\d{2,5})", line)
            if dm:
                width, height = int(dm.group(1)), int(dm.group(2))
            fm = re.search(r"([\d.]+)\s*fps", line) or re.search(r"([\d.]+)\s*tbr", line)
            if fm:
                try:
                    fps = float(fm.group(1))
                except ValueError:
                    pass
    has_audio = bool(re.search(r"Stream #.*Audio:", text))
    rot = re.search(r"rotation of (-?[\d.]+) degrees", text) or re.search(r"rotate\s*:\s*(-?\d+)", text)
    if rot and has_video and abs(round(float(rot.group(1)))) % 180 == 90:
        width, height = height, width  # phone videos: ffmpeg auto-rotates on decode
    if not (1 <= fps <= 120):
        fps = 30.0
    if duration <= 0 and not has_video and not has_audio:
        raise FFmpegError(f"Could not read {Path(path).name}. Is it a video file?")
    return MediaInfo(duration, width, height, fps, has_video, has_audio)


def nice_fps(fps: float) -> str:
    """Turn 29.97-style rates into exact fractions, cap at 60."""
    for approx, exact in ((23.976, "24000/1001"), (29.97, "30000/1001"), (59.94, "60000/1001")):
        if abs(fps - approx) < 0.02:
            return exact
    fps = min(60, max(10, round(fps)))
    return str(fps)


def fps_value(fps: str) -> float:
    if "/" in fps:
        a, b = fps.split("/")
        return float(a) / float(b)
    return float(fps)


def extract_audio(src: str | Path, dst: str | Path) -> None:
    """16 kHz mono WAV, which is what speech recognition wants."""
    run(["-i", src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst])


def extract_frame(src: str | Path, at: float, dst: str | Path, width: int = 512) -> None:
    run(["-ss", f"{max(0.0, at):.3f}", "-i", src, "-frames:v", "1",
         "-vf", f"scale={width}:-2", "-q:v", "4", dst])


def run_with_progress(args: list[str], total: float, progress=None, cwd=None) -> None:
    """Like run(), but calls progress(0..1) while ffmpeg works."""
    if not progress or total <= 0:
        run(args, cwd=cwd)
        return
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *map(str, args)]
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    import threading

    err_lines: list[str] = []
    t = threading.Thread(target=lambda: err_lines.extend(proc.stderr), daemon=True)
    t.start()
    for line in proc.stdout:
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
                progress(min(1.0, us / 1e6 / total))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=5)
    if proc.returncode != 0:
        tail = "".join(err_lines[-25:])
        raise FFmpegError(f"ffmpeg failed:\n{tail}")
