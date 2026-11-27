"""Segment-level re-alignment helper (E9).

Proposes new timings for a single cue from a slice of audio.  The lyrics
remain authoritative: the caller must show the proposal and apply it only
after explicit acceptance.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any


def realign_segment(
    audio_path: str | Path,
    start: float,
    end: float,
    text: str,
    *,
    language: str = "pt",
    separate: bool = True,
    padding: float = 1.0,
) -> dict[str, Any] | None:
    audio_path = Path(audio_path)
    range_start = max(0.0, float(start) - padding)
    range_end = float(end) + padding

    from .audio import ensure_wav
    from .backends.demucs import separate_vocals
    from .backends.stable_whisper import StableWhisperAligner
    from .device import choose_devices
    from .media import extract_audio_range

    with tempfile.TemporaryDirectory(prefix="vilm-realign-") as tmp:
        tmp_path = Path(tmp)
        selected = extract_audio_range(
            audio_path, tmp_path / "segment.wav", range_start, range_end
        )
        align_audio = selected
        if separate:
            try:
                align_audio = separate_vocals(
                    selected, tmp_path / "stems", device=choose_devices().separation
                )
            except Exception:
                align_audio = selected

        aligner = StableWhisperAligner(model_name="small", device=choose_devices().whisper)
        units = aligner.transcribe(align_audio, language)
        if not units:
            return None

        words = [
            {"text": u.text, "start": round(u.start + range_start, 3),
             "end": round(u.end + range_start, 3)}
            for u in units
        ]
        return {"start": words[0]["start"], "end": words[-1]["end"], "words": words}
