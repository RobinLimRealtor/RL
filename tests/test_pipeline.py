"""End-to-end check: make a fake talking video, run the full edit, check the result.

Speech recognition is replaced by a fixed transcript so the test runs offline.
Run with:  python -m pytest -q
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOEDIT_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    from autoedit import config

    importlib.reload(config)
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "lib")
    monkeypatch.setattr(config, "MUSIC_DIR", tmp_path / "lib" / "music")
    monkeypatch.setattr(config, "SFX_DIR", tmp_path / "lib" / "sfx")
    monkeypatch.setattr(config, "DEFAULT_BROLL_DIR", tmp_path / "broll")
    monkeypatch.setitem(config.DEFAULTS, "broll_folder", str(tmp_path / "broll"))
    for mod in ("library", "planner", "render", "jobs"):
        importlib.reload(importlib.import_module(f"autoedit.{mod}"))
    config.ensure_dirs()
    return tmp_path


WORDS = [
    ("Hi,", 0.5, 0.8), ("welcome", 0.85, 1.3), ("to", 1.35, 1.5), ("the", 1.55, 1.7), ("house.", 1.75, 2.0),
    ("Um,", 3.5, 3.9), ("this", 4.0, 4.3), ("kitchen", 4.35, 4.8), ("island", 4.85, 5.3), ("is", 5.35, 5.5),
    ("huge.", 5.55, 6.0), ("Look", 6.1, 6.4), ("at", 6.45, 6.55), ("the", 6.6, 6.7), ("light.", 6.75, 7.2),
    ("Call", 10.0, 10.3), ("me", 10.35, 10.5), ("today!", 10.55, 11.2),
]


def _make_media(tmp: Path):
    from autoedit.ffmpeg_tools import run

    expr = "+".join(f"between(t,{s},{e})" for _, s, e in WORDS)
    src = tmp / "talk.mp4"
    run(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=12",
         "-f", "lavfi", "-i", f"aevalsrc='0.4*sin(2*PI*220*t)*({expr})':s=48000:d=12",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", src])
    broll = tmp / "broll"
    broll.mkdir(exist_ok=True)
    run(["-f", "lavfi", "-i", "color=c=blue:size=1920x1080:rate=30:duration=4",
         "-c:v", "libx264", "-preset", "ultrafast", broll / "modern-kitchen-island.mp4"])
    return src


def _fake_transcript(job_dir: Path):
    words = [{"text": t, "start": s, "end": e} for t, s, e in WORDS]
    segs = [
        {"text": "Hi, welcome to the house.", "start": 0.5, "end": 2.0},
        {"text": "Um, this kitchen island is huge. Look at the light.", "start": 3.5, "end": 7.2},
        {"text": "Call me today!", "start": 10.0, "end": 11.2},
    ]
    (job_dir / "transcript.json").write_text(json.dumps({"language": "en", "words": words, "segments": segs}))


def test_cutting_math():
    from autoedit.cutting import TimeMap, keep_segments

    words = [{"text": t, "start": s, "end": e} for t, s, e in WORDS]
    segs = keep_segments(words, 12.0, {"enabled": True, "tightness": "normal", "remove_fillers": True,
                                       "remove_ranges": []}, 30)
    assert segs[0][0] == pytest.approx(0.4, abs=0.04)
    assert all(b[0] > a[1] for a, b in zip(segs, segs[1:]))
    tm = TimeMap(segs)
    assert tm.duration < 9.0  # pauses + the "um" are gone
    assert tm.to_output(segs[1][0]) == pytest.approx(segs[0][1] - segs[0][0])


def test_full_edit_and_revision(env):
    from autoedit import jobs
    from autoedit.ffmpeg_tools import probe

    src = _make_media(env)
    job = jobs.create("talk.mp4", lambda dst: Path(dst).write_bytes(src.read_bytes()),
                      "make it vertical for reels with captions, upbeat music and whoosh on b-roll")
    _fake_transcript(jobs.job_dir(job["id"]))
    jobs._queue.get_nowait()
    jobs.process(job["id"], job["pending_prompt"])
    job = jobs.load(job["id"])
    assert job["status"] == "done", job
    v = job["versions"][0]
    assert v["plan"]["format"] == "vertical"
    assert v["broll_count"] == 1
    out = probe(jobs.job_dir(job["id"]) / v["file"])
    assert (out.width, out.height) == (1080, 1920)
    assert out.has_audio
    assert 6.0 < out.duration < 9.0, out.duration
    srt = (jobs.job_dir(job["id"]) / v["srt"]).read_text()
    assert "kitchen" in srt and "Um" not in srt

    jobs.process(job["id"], "no music, and no captions please")
    job = jobs.load(job["id"])
    v2 = job["versions"][1]
    assert v2["plan"]["music"]["enabled"] is False
    assert v2["plan"]["captions"]["enabled"] is False
    assert v2["plan"]["format"] == "vertical"  # unchanged from v1
    assert probe(jobs.job_dir(job["id"]) / v2["file"]).duration > 6.0


def test_claude_request_shape(monkeypatch):
    """The planner sends a valid request and reads the plan back (no real API call)."""
    import anthropic
    import httpx2

    from autoedit import planner

    plan = dict(planner.DEFAULT_PLAN, summary="ok", format="vertical",
                broll=[{"start": 4, "end": 7, "source": "stock", "clip": "", "query": "kitchen"}])
    seen = []

    def sse(text):
        events = [
            ("message_start", {"type": "message_start", "message": {
                "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": text}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 5}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=sse(json.dumps(plan)))

    real = anthropic.Anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: real(
        **kw, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))))
    out = planner.plan_with_claude(
        api_key="sk-test", model="claude-opus-5-5", prompt="vertical please",
        transcript={"segments": [{"text": "hi", "start": 0, "end": 1}]}, duration=12, video_shape="landscape",
        own_clips=[], stock_available=True, music_tracks=[], sfx_names=["whoosh"])
    body = seen[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "vertical please" in body["messages"][0]["content"][0]["text"]
    assert planner.normalize_plan(out, 12, [], ["whoosh"])["format"] == "vertical"


def test_pixabay_pick(env, monkeypatch):
    from autoedit import stock

    class Resp:
        def __init__(self, data=None):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    calls = []

    def fake_get(url, params=None, timeout=None, **kw):
        calls.append((url, params))
        return Resp({"hits": [
            {"id": 1, "duration": 2, "videos": {"large": {"url": "", "width": 0, "height": 0},
                                                "medium": {"url": "https://x/1.mp4", "width": 1280, "height": 720}}},
            {"id": 2, "duration": 9, "videos": {"large": {"url": "https://x/2.mp4", "width": 1920, "height": 1080},
                                                "medium": {"url": "https://x/2m.mp4", "width": 1280, "height": 720}}},
        ]})

    downloaded = []
    monkeypatch.setattr(stock.requests, "get", fake_get)
    monkeypatch.setattr(stock, "_download", lambda link, name: downloaded.append(link) or Path(name))
    path, sid = stock.find_and_download("modern kitchen", {"pixabay_api_key": "k"}, False, 5.0)
    assert sid == "pixabay:2" and downloaded == ["https://x/2.mp4"]
    assert calls[0][1]["q"] == "modern kitchen"
    assert stock.find_and_download("kitchen", {}, False, 5.0) is None


SOURCES = ["testsrc2", "smptebars", "mandelbrot", "rgbtestsrc", "cellauto", "yuvtestsrc"]


def _color_clips(tmp: Path, name: str, sources, seconds, size="1080x1920"):
    """A video made of visibly different shots: a stand-in for real footage with clear cuts."""
    from autoedit.ffmpeg_tools import run

    parts = []
    for i, (c, s) in enumerate(zip(sources, seconds)):
        p = tmp / f"{name}_{i}.mp4"
        w, h = size.split("x")
        run(["-t", str(s), "-f", "lavfi", "-i", f"{c}=rate=30",
             "-f", "lavfi", "-i", f"anoisesrc=d={s}:a=0.02", "-shortest", "-vf", f"scale={w}:{h},setsar=1,format=yuv420p",
             "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", p])
        parts.append(p)
    (tmp / f"{name}.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
    out = tmp / f"{name}.mp4"
    run(["-f", "concat", "-safe", "0", "-i", f"{name}.txt", "-c", "copy", out.name], cwd=tmp)
    return out


def test_paced_segments():
    from autoedit.cutting import paced_segments

    segs = paced_segments(20.0, [4.0, 6.0], 2.0)
    assert segs[0] == pytest.approx((1.0, 3.0))       # 4s shot -> middle 2s
    assert segs[1] == pytest.approx((4.0, 6.0))       # 2s shot kept whole
    assert all(e - s == pytest.approx(2.0) for s, e in segs[2:])  # 14s take -> several 2s pieces
    assert len(segs) > 4


def test_learn_style_and_apply(env, monkeypatch):
    import time as _time

    from autoedit import jobs, styles
    from autoedit.ffmpeg_tools import probe

    # Reference: fast vertical reel, ~1.5s shots, music only.
    ref = _color_clips(env, "ref", SOURCES, [1.5] * 6)
    monkeypatch.setattr("autoedit.transcribe.transcribe", lambda *a, **k: {"words": [], "segments": []})
    style = styles.create("Fast reel", lambda dst: Path(dst).write_bytes(ref.read_bytes()), "ref.mp4")
    for _ in range(100):
        style = styles.load(style["id"])
        if style["status"] != "analyzing":
            break
        _time.sleep(0.2)
    assert style["status"] == "ready", style
    assert 1.0 <= style["hints"]["pace"]["max_shot_seconds"] <= 3.0  # synthetic cuts: some go undetected
    assert style["hints"]["format"] == "vertical"

    # Own footage: landscape home tour with long takes and no talking.
    tour = _color_clips(env, "tour", SOURCES[:3], [6, 8, 6], size="1280x720")
    job = jobs.create("tour.mp4", lambda dst: Path(dst).write_bytes(tour.read_bytes()), "", style["id"])
    jobs._queue.get_nowait()
    (jobs.job_dir(job["id"]) / "transcript.json").write_text(json.dumps({"words": [], "segments": []}))
    jobs.process(job["id"], "")
    job = jobs.load(job["id"])
    assert job["status"] == "done", job
    v = job["versions"][0]
    assert v["style_name"] == "Fast reel"
    out = probe(jobs.job_dir(job["id"]) / v["file"])
    assert (out.width, out.height) == (1080, 1920)
    assert out.duration < 14  # 20s of long takes trimmed to the reference's quick rhythm
