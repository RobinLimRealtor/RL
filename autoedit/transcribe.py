"""Speech to text with word timings, using faster-whisper (runs on your computer)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

_model_lock = threading.Lock()
_models: dict[str, object] = {}

# Whisper tends to "clean up" ums and uhs. Priming it with a disfluent prompt
# makes it write them down, so we can cut them.
_DISFLUENT_PROMPT = "Umm, let me think like, hmm... Okay, here's what I'm, like, thinking. Uh, so."


def _get_model(size: str):
    from faster_whisper import WhisperModel

    with _model_lock:
        if size not in _models:
            _models[size] = WhisperModel(size, device="cpu", compute_type="int8")
        return _models[size]


def _load_wav(path) -> "np.ndarray":
    """Read our 16 kHz mono 16-bit WAV straight into memory.

    We hand Whisper raw samples instead of a file name so it never has to open
    the file itself (its file reader breaks with some versions of a helper library).
    """
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def transcribe(audio_path: str | Path, model_size: str = "small", progress=None) -> dict:
    """Returns {"language", "words": [{text,start,end}], "segments": [{text,start,end}]}."""
    model = _get_model(model_size)
    segments_iter, info = model.transcribe(
        _load_wav(audio_path),
        word_timestamps=True,
        initial_prompt=_DISFLUENT_PROMPT,
        condition_on_previous_text=False,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
    )
    words, segments = [], []
    total = max(float(info.duration or 0), 0.001)
    for seg in segments_iter:
        seg_words = []
        for w in seg.words or []:
            text = w.word.strip()
            if not text:
                continue
            item = {"text": text, "start": round(float(w.start), 3), "end": round(float(w.end), 3)}
            words.append(item)
            seg_words.append(item)
        if seg_words:
            segments.append({
                "text": seg.text.strip(),
                "start": seg_words[0]["start"],
                "end": seg_words[-1]["end"],
            })
        if progress:
            progress(min(1.0, float(seg.end) / total))
    return {"language": info.language, "words": words, "segments": segments}


def load_or_transcribe(audio_path: Path, cache_file: Path, model_size: str, progress=None) -> dict:
    if cache_file.exists():
        return json.loads(cache_file.read_text("utf-8"))
    result = transcribe(audio_path, model_size, progress)
    cache_file.write_text(json.dumps(result, indent=1), "utf-8")
    return result
