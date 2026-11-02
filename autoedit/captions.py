"""Build caption files (.ass for burning in, .srt for uploading to YouTube etc.)."""

from __future__ import annotations

import re

from .cutting import TimeMap, is_filler, normalize

# Words that make good "big" caption words even without a keyword list.
_ROOM_WORDS = {"kitchen", "bedroom", "bathroom", "toilet", "balcony", "living", "dining", "garden", "pool",
               "garage", "study", "patio", "rooftop", "terrace", "yard", "backyard", "master", "attic", "basement",
               "landed", "condo", "price", "sold", "sale", "new", "now", "huge", "luxury", "view", "views"}
_COMMON = {"because", "actually", "something", "everything", "anything", "through", "another", "between",
           "without", "already", "probably", "basically", "literally", "there's", "that's", "they're"}


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def output_words(words: list[dict], tmap: TimeMap, remove_fillers: bool) -> list[dict]:
    """Words that survive the cut, with times on the edited timeline."""
    out = []
    for w in words:
        mid = (w["start"] + w["end"]) / 2
        if not tmap.contains(mid) or (remove_fillers and is_filler(w["text"])):
            continue
        s, e = tmap.to_output(max(w["start"], 0)), tmap.to_output(w["end"])
        if e <= s:
            e = s + 0.15
        out.append({"text": w["text"], "start": s, "end": e})
    return out


def chunk_words(words: list[dict], per_line: int) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= per_line or w["start"] - cur[-1]["end"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w["text"][-1:] in ".?!,;:" and len(cur) >= max(1, per_line // 2):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def is_keyword(word: str, keywords: set[str]) -> bool:
    n = normalize(word)
    if not n and not re.search(r"\d", word):
        return False
    if keywords:
        return n in keywords or bool(re.search(r"[\d$%]", word))
    return bool(re.search(r"[\d$%]", word)) or n in _ROOM_WORDS or (len(n) >= 8 and n not in _COMMON)


def _reel_events(words, width, height, keywords, per_line):
    """Social-media tour look: 1-2 words in the middle, words pop in as they're spoken,
    key words big and bold in capitals, other words smaller (italic when paired with a key word)."""
    kw = {normalize(k) for k in keywords if normalize(k)}
    lines = []
    per_line = max(1, min(per_line, 3))
    # Chunks: a key word always starts a new chunk, so it leads ("TASTEFULLY / renovated").
    chunks, cur = [], []
    for w in words:
        key = is_keyword(w["text"], kw)
        if cur and (key or len(cur) >= per_line or w["start"] - cur[-1]["end"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w["text"][-1:] in ".?!":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    base = min(width, height)
    big, small, plain = int(base * 0.14), int(base * 0.07), int(base * 0.08)
    for ci, chunk in enumerate(chunks):
        end_of_chunk = chunk[-1]["end"] + 0.3
        if ci + 1 < len(chunks):
            end_of_chunk = min(end_of_chunk, chunks[ci + 1][0]["start"])
        flags = [is_keyword(w["text"], kw) for w in chunk]
        first = flags.index(True) if True in flags else -1
        flags = [k == first for k in range(len(chunk))]  # at most one big word per chunk
        has_key = any(flags)
        for i, w in enumerate(chunk):
            start = w["start"]
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else end_of_chunk
            if end - start < 0.04:
                continue
            parts = []
            for j, (cw, key) in enumerate(zip(chunk[: i + 1], flags)):
                t = _escape(cw["text"]).strip(",.;:")
                if key:
                    color = "\\c&H00D7FF&" if re.search(r"[\d$%]", t) else ""
                    piece = f"{{\\fnArial Black\\fs{big}\\b1\\i0{color}}}{t.upper()}"
                elif has_key:
                    piece = f"{{\\fnGeorgia\\fs{small}\\b0\\i1\\c&HFFFFFF&}}{t.lower()}"
                else:
                    piece = f"{{\\fnArial\\fs{plain}\\b1\\i0\\c&HFFFFFF&}}{t.lower()}"
                pop = "{\\fscx125\\fscy125\\t(0,110,\\fscx100\\fscy100)}" if j == i else ""
                parts.append(pop + piece + "{\\fscx100\\fscy100}")
            sep = "\\N" if has_key else " "
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{sep.join(parts)}")
    return lines


def build_ass(words: list[dict], width: int, height: int, style: str, position: str, per_line: int,
              keywords: list[str] | None = None) -> str:
    base = min(width, height)
    if style == "reel":
        size, bold, outline, shadow, border = int(base * 0.075), -1, max(2, base // 360), max(3, base // 180), 1
        font = "Arial"
    elif style == "bold":
        size, bold, outline, shadow, border = int(base * 0.09), -1, max(4, base // 150), 0, 1
        font = "Arial Black"
    elif style == "boxed":
        size, bold, outline, shadow, border = int(base * 0.055), -1, max(6, base // 90), 0, 3
        font = "Arial"
    else:
        size, bold, outline, shadow, border = int(base * 0.058), -1, max(2, base // 300), max(1, base // 400), 1
        font = "Arial"
    margin_v = int(height * (0.40 if style == "reel" and position == "middle" else
                             0.30 if position == "middle" else 0.09))
    margin_h = int(width * 0.08)
    back = "&H80000000" if style == "boxed" else "&H64000000"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,{'&H80000000' if style == 'boxed' else '&H00000000'},{back},{bold},0,0,0,100,100,0,0,{border},{outline},{shadow},2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    if style == "reel":
        return header + "\n".join(_reel_events(words, width, height, keywords or [], per_line)) + "\n"
    lines = []
    chunks = chunk_words(words, per_line)
    for ci, chunk in enumerate(chunks):
        end_of_chunk = chunk[-1]["end"] + 0.25
        if ci + 1 < len(chunks):  # never overlap the next caption
            end_of_chunk = min(end_of_chunk, chunks[ci + 1][0]["start"])
        texts = [_escape(w["text"]) for w in chunk]
        if style == "bold":
            texts = [t.upper().rstrip(",.") for t in texts]
            for i, w in enumerate(chunk):
                start = w["start"]
                end = chunk[i + 1]["start"] if i + 1 < len(chunk) else end_of_chunk
                parts = [
                    ("{\\c&H00E5FF&}" + t + "{\\c&HFFFFFF&}") if j == i else t
                    for j, t in enumerate(texts)
                ]
                pop = "{\\fscx108\\fscy108\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
                lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{pop}{' '.join(parts)}")
        else:
            lines.append(f"Dialogue: 0,{_ass_time(chunk[0]['start'])},{_ass_time(end_of_chunk)},Default,,0,0,0,,{' '.join(texts)}")
    return header + "\n".join(lines) + "\n"


def build_srt(words: list[dict], per_line: int = 7) -> str:
    out = []
    chunks = chunk_words(words, max(per_line, 5))
    for i, chunk in enumerate(chunks, 1):
        text = " ".join(w["text"] for w in chunk)
        end = chunk[-1]["end"] + 0.2
        if i < len(chunks):
            end = min(end, chunks[i][0]["start"])
        out.append(f"{i}\n{_srt_time(chunk[0]['start'])} --> {_srt_time(end)}\n{text}\n")
    return "\n".join(out)
