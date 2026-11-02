"""Turn an edit plan into a finished MP4 using ffmpeg."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import captions as cap
from .cutting import TimeMap
from .ffmpeg_tools import MediaInfo, fps_value, nice_fps, probe, run, run_with_progress

FORMAT_SIZES = {"vertical": (1080, 1920), "landscape": (1920, 1080), "square": (1080, 1080)}


def target_size(info: MediaInfo, fmt: str) -> tuple[int, int]:
    if fmt in FORMAT_SIZES:
        return FORMAT_SIZES[fmt]
    w, h = info.width or 1920, info.height or 1080
    scale = min(1.0, 1920 / max(w, h))
    return int(w * scale) // 2 * 2, int(h * scale) // 2 * 2


def _fit(w: int, h: int, fps: str) -> str:
    """Scale to fill the frame, then crop the overflow from the centre."""
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"setsar=1,fps={fps},format=yuv420p")


@dataclass
class BrollPlacement:
    path: Path
    out_start: float
    out_end: float
    clip_offset: float = 0.0


@dataclass
class SfxPlacement:
    path: Path
    out_time: float
    volume: float = 0.55


def _encode_segment(src, info, seg, idx, work: Path, w, h, fps) -> str:
    s, e = seg
    d = e - s
    name = f"seg_{idx:04d}.mkv"
    args = ["-ss", f"{s:.4f}", "-t", f"{d:.4f}", "-i", src]
    if not info.has_audio:
        args += ["-f", "lavfi", "-t", f"{d:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
    fade = min(0.015, d / 4)
    afilter = (f"aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo,"
               f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={d - fade:.4f}:d={fade:.3f},apad")
    args += [
        "-map", "0:v:0", "-map", "0:a:0" if info.has_audio else "1:a:0",
        "-vf", _fit(w, h, fps), "-af", afilter, "-t", f"{d:.4f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-g", "60",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", name,
    ]
    run(args, cwd=work)
    return name


def render(*, src: Path, info: MediaInfo, plan: dict, transcript: dict, segments: list[tuple[float, float]],
           broll: list[BrollPlacement], music: Path | None, sfx: list[SfxPlacement],
           out_path: Path, srt_path: Path, work: Path, progress=None) -> None:
    work.mkdir(parents=True, exist_ok=True)
    for old in work.glob("seg_*.mkv"):
        old.unlink()
    w, h = target_size(info, plan["format"])
    fps = nice_fps(info.fps)
    tmap = TimeMap(segments)
    total = tmap.duration
    report = progress or (lambda frac, msg="": None)

    # 1) Cut: encode every kept piece, then join them.
    done = [0]

    def job(args):
        idx, seg = args
        name = _encode_segment(src, info, seg, idx, work, w, h, fps)
        done[0] += 1
        report(0.55 * done[0] / len(segments), f"Cutting ({done[0]}/{len(segments)})")
        return name

    with ThreadPoolExecutor(max_workers=2) as pool:
        names = list(pool.map(job, enumerate(segments)))
    (work / "concat.txt").write_text("".join(f"file '{n}'\n" for n in names), "utf-8")
    run(["-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", "base.mkv"], cwd=work)
    base_dur = probe(work / "base.mkv").duration or total

    # 2) Captions
    words = cap.output_words(transcript.get("words", []), tmap, plan["cut"].get("remove_fillers", True))
    srt_path.write_text(cap.build_srt(words), "utf-8")
    use_captions = plan["captions"]["enabled"] and bool(words)
    if use_captions:
        c = plan["captions"]
        (work / "captions.ass").write_text(
            cap.build_ass(words, w, h, c["style"], c["position"], c["words_per_line"]), "utf-8")

    # 3) Compose b-roll, captions, music and sound effects in one pass.
    args = ["-i", "base.mkv"]
    vf, af = [], []
    n_in = 1
    vlabel = "0:v"
    for i, b in enumerate(broll):
        dur = b.out_end - b.out_start
        args += ["-ss", f"{b.clip_offset:.3f}", "-t", f"{dur:.3f}", "-i", str(b.path)]
        vf.append(f"[{n_in}:v]{_fit(w, h, fps)},setpts=PTS-STARTPTS+{b.out_start:.3f}/TB[b{i}]")
        vf.append(f"[{vlabel}][b{i}]overlay=0:0:eof_action=pass:"
                  f"enable='between(t,{b.out_start:.3f},{b.out_end:.3f})'[v{i}]")
        vlabel = f"v{i}"
        n_in += 1
    if use_captions:
        vf.append(f"[{vlabel}]subtitles=captions.ass[vout]")
    else:
        vf.append(f"[{vlabel}]null[vout]")

    has_speech = bool(words)
    mix = ["[voice]"]
    voice_gain = 1.0 if has_speech or not (music and plan["music"]["enabled"]) else 0.6
    af.append(f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={voice_gain}[voice0]")
    if music and plan["music"]["enabled"]:
        m = plan["music"]
        gain = m["volume"] * (0.35 if has_speech else 0.9)
        args += ["-stream_loop", "-1", "-t", f"{base_dur + 1:.3f}", "-i", str(music)]
        fade_out = max(0.0, base_dur - 2.0)
        af.append(f"[{n_in}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{base_dur:.3f},"
                  f"volume={gain:.3f},afade=t=in:d=1,afade=t=out:st={fade_out:.3f}:d=2[mraw]")
        n_in += 1
        if m.get("duck", True) and has_speech:
            af.append("[voice0]asplit=2[voice][sc]")
            af.append("[mraw][sc]sidechaincompress=threshold=0.012:ratio=12:attack=15:release=450[mus]")
        else:
            af.append("[voice0]anull[voice]")
            af.append("[mraw]anull[mus]")
        mix.append("[mus]")
    else:
        af.append("[voice0]anull[voice]")
    for i, s in enumerate(sfx):
        if s.out_time >= base_dur:
            continue
        args += ["-i", str(s.path)]
        ms = int(s.out_time * 1000)
        af.append(f"[{n_in}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={s.volume},"
                  f"adelay={ms}:all=1[s{i}]")
        mix.append(f"[s{i}]")
        n_in += 1
    if len(mix) > 1:
        af.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first:dropout_transition=0[mix]")
    else:
        af.append("[voice]anull[mix]")
    af.append("[mix]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[aout]")

    graph = ";".join(vf + af)
    (work / "graph.txt").write_text(graph.replace(";", ";\n"), "utf-8")  # kept for troubleshooting
    tmp = work / "render.mp4"
    args += [
        "-filter_complex", graph,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-r", fps, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-t", f"{base_dur:.3f}", tmp.name,
    ]
    run_with_progress(args, base_dur, lambda f: report(0.55 + 0.45 * f, "Adding captions, b-roll and music"),
                      cwd=work)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(out_path)
    for old in work.glob("seg_*.mkv"):
        old.unlink()


__all__ = ["render", "BrollPlacement", "SfxPlacement", "target_size", "fps_value"]
