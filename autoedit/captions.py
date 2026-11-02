"""Build caption files (.ass for burning in, .srt for uploading to YouTube etc.)."""

from __future__ import annotations

from .cutting import TimeMap, is_filler


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


def build_ass(words: list[dict], width: int, height: int, style: str, position: str, per_line: int) -> str:
    base = min(width, height)
    if style == "bold":
        size, bold, outline, shadow, border = int(base * 0.09), -1, max(4, base // 150), 0, 1
        font = "Arial Black"
    elif style == "boxed":
        size, bold, outline, shadow, border = int(base * 0.055), -1, max(6, base // 90), 0, 3
        font = "Arial"
    else:
        size, bold, outline, shadow, border = int(base * 0.058), -1, max(2, base // 300), max(1, base // 400), 1
        font = "Arial"
    margin_v = int(height * (0.30 if position == "middle" else 0.09))
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
