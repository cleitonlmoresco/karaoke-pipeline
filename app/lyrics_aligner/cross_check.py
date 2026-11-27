"""Cross-check between two aligners for confidence scoring (E1 full).

Runs a secondary alignment (Whisper 'base' model) and compares cue boundaries
with the primary ('small') alignment.  The agreement per cue is combined with
the transcript-similarity score to produce a final confidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .models import Cue
from .pipeline import align_to_srt


def cross_check(
    audio_path: str | Path,
    lyrics: str,
    primary_cues: Sequence[Cue],
    *,
    secondary_model: str = "base",
    tolerance: float = 0.5,
) -> list[float]:
    """Return per-cue agreement scores (0..1) between primary and secondary."""

    import tempfile

    audio_path = Path(audio_path)
    scores: list[float] = [0.5] * len(primary_cues)

    try:
        with tempfile.TemporaryDirectory(prefix="vilm-crosscheck-") as tmp:
            tmp_path = Path(tmp)
            srt_path = tmp_path / "secondary.srt"
            secondary_cues = align_to_srt(
                audio_path,
                lyrics,
                srt_path,
                max_chars=80,
                auto_segment=True,
                language="auto",
                separate=True,
                model_name=secondary_model,
                lead_ms=0,
                end_pad_ms=500,
                min_duration_ms=500,
                max_duration_ms=30000,
                min_gap_ms=80,
                offset_seconds=0.0,
            )
    except Exception:
        return scores

    # Match by index (both use same lyrics order)
    count = min(len(primary_cues), len(secondary_cues))
    for i in range(count):
        p = primary_cues[i]
        s = secondary_cues[i]
        # Agreement on start and end
        start_diff = abs(p.start - s.start)
        end_diff = abs(p.end - s.end)
        start_score = max(0.0, 1.0 - start_diff / max(tolerance, 1e-6))
        end_score = max(0.0, 1.0 - end_diff / max(tolerance, 1e-6))
        scores[i] = (start_score + end_score) / 2.0

    return scores


def combine_confidence(
    transcript_scores: list[float],
    cross_scores: list[float],
) -> list[float]:
    """Combine transcript-based and cross-check scores (geometric-ish mean)."""

    n = max(len(transcript_scores), len(cross_scores))
    result: list[float] = []
    for i in range(n):
        t = transcript_scores[i] if i < len(transcript_scores) else 0.5
        c = cross_scores[i] if i < len(cross_scores) else 0.5
        result.append(round(max(0.0, min(1.0, (t * 0.6 + c * 0.4))), 3))
    return result
