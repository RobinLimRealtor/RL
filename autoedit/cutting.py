"""Decide which parts of the original video to keep (dead-space removal)."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "hm", "mm", "mhm"}

# Longest pause (seconds) we leave in, per tightness setting.
PAUSE = {"loose": 0.75, "normal": 0.4, "tight": 0.22}
LEAD_PAD = 0.08   # keep a little air before each word
TAIL_PAD = 0.14   # and after, so words don't sound clipped
MIN_SEGMENT = 0.25


def normalize(word: str) -> str:
    return re.sub(r"[^a-z']", "", word.lower())


def is_filler(word: str) -> bool:
    return normalize(word) in FILLERS


def _in_ranges(t: float, ranges: list[tuple[float, float]]) -> bool:
    return any(a <= t <= b for a, b in ranges)


def _subtract(segments, ranges):
    out = []
    for s, e in segments:
        pieces = [(s, e)]
        for a, b in ranges:
            nxt = []
            for ps, pe in pieces:
                if b <= ps or a >= pe:
                    nxt.append((ps, pe))
                    continue
                if a > ps:
                    nxt.append((ps, a))
                if b < pe:
                    nxt.append((b, pe))
            pieces = nxt
        out.extend(pieces)
    return out


def paced_segments(duration, scenes, max_shot):
    """Footage without talking: trim every shot to at most `max_shot` seconds.

    Long single takes (e.g. one continuous walk-through) become a series of short
    pieces with the in-between skipped, which gives the quick "reel" rhythm.
    """
    bounds = [0.0] + [t for t in scenes if 0 < t < duration] + [duration]
    segs = []
    for s, e in zip(bounds, bounds[1:]):
        length = e - s
        if length < 0.4:
            continue
        if length <= max_shot * 1.15:
            segs.append((s, e))
        elif length <= max_shot * 3:
            start = s + min((length - max_shot) / 2, 1.0)
            segs.append((start, start + max_shot))
        else:
            t = s + 0.3
            while t + max_shot <= e:
                segs.append((t, t + max_shot))
                t += max_shot * 2
    return segs


def keep_segments(words, duration, cut_plan, fps=30.0, scenes=None, max_shot=0.0):
    """Return a list of (start, end) source ranges to keep, in order."""
    remove = sorted(
        (max(0.0, float(r["start"])), min(duration, float(r["end"])))
        for r in cut_plan.get("remove_ranges", [])
        if float(r["end"]) > float(r["start"])
    )
    if not words and max_shot > 0 and scenes is not None:
        segs = _subtract(paced_segments(duration, scenes, max_shot), remove)
    elif not cut_plan.get("enabled", True) or not words:
        segs = _subtract([(0.0, duration)], remove)
    else:
        max_pause = PAUSE.get(cut_plan.get("tightness", "normal"), PAUSE["normal"])
        remove_fillers = cut_plan.get("remove_fillers", True)
        kept_words = [
            w for w in words
            if not (remove_fillers and is_filler(w["text"]))
            and not _in_ranges((w["start"] + w["end"]) / 2, remove)
        ]
        segs = []
        for w in kept_words:
            s = max(0.0, w["start"] - LEAD_PAD)
            e = min(duration, w["end"] + TAIL_PAD)
            if segs and s - segs[-1][1] <= max_pause:
                segs[-1] = (segs[-1][0], max(segs[-1][1], e))
            else:
                segs.append((s, e))
        segs = _subtract(segs, remove)
    # Snap to the frame grid so audio and picture stay in sync after joining.
    snapped = []
    for s, e in segs:
        s2 = round(s * fps) / fps
        e2 = round(e * fps) / fps
        if e2 - s2 >= MIN_SEGMENT:
            snapped.append((round(s2, 4), round(min(e2, duration), 4)))
    if not snapped:
        snapped = [(0.0, duration)]
    return snapped


@dataclass
class TimeMap:
    """Maps a time in the original video to a time in the edited video."""

    segments: list[tuple[float, float]]

    def __post_init__(self):
        self.starts = [s for s, _ in self.segments]
        self.offsets = []
        acc = 0.0
        for s, e in self.segments:
            self.offsets.append(acc)
            acc += e - s
        self.duration = acc

    def contains(self, t: float) -> bool:
        i = bisect_right(self.starts, t) - 1
        return i >= 0 and t <= self.segments[i][1]

    def to_output(self, t: float) -> float:
        i = bisect_right(self.starts, t) - 1
        if i < 0:
            return 0.0
        s, e = self.segments[i]
        if t <= e:
            return self.offsets[i] + (t - s)
        # t falls in a removed gap: snap to the start of the next kept part
        return self.offsets[i + 1] if i + 1 < len(self.segments) else self.duration
