"""Validation helpers for lyric cue timelines.

The supplied lyrics are always the subtitle text source, so this module never
changes cue text.  It only inspects cue times and reports problems that would
make a render (ASS/MP4) wrong or misleading: inverted ranges, overlaps,
monotonicity violations, durations outside sane bounds, cues that fall outside
the media, and cues whose display would disappear well before the next cue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .models import Cue


@dataclass(frozen=True)
class TimelineIssue:
    """A single validation finding."""

    severity: str  # "error", "warning" or "info"
    code: str
    line: int | None  # 1-based cue index, None for a global finding
    message: str

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


def _extract(item: Any) -> tuple[str, float | None, float | None]:
    """Return (text, start, end) for a Cue or a mapping, or None values."""

    if isinstance(item, Cue):
        return item.text, float(item.start), float(item.end)
    if isinstance(item, Mapping):
        text = str(item.get("text", ""))
        try:
            start = float(item.get("start"))
        except (TypeError, ValueError):
            start = None
        try:
            end = float(item.get("end"))
        except (TypeError, ValueError):
            end = None
        return text, start, end
    return str(item), None, None


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def validate_timeline(
    cues: Sequence[Any],
    *,
    audio_duration: float | None = None,
    min_gap_ms: int = 80,
    min_duration_ms: int = 500,
    max_duration_ms: int = 30000,
    hold_seconds: float = 3.0,
) -> list[TimelineIssue]:
    """Return all validation findings for ``cues`` (empty list means OK).

    Accepts either :class:`Cue` objects or mappings with ``text``/``start``/
    ``end`` keys, so that invalid raw data can be reported instead of raising.
    """

    issues: list[TimelineIssue] = []
    min_gap = max(0, min_gap_ms) / 1000.0
    min_duration = max(0.0, min_duration_ms) / 1000.0
    max_duration = max(0.0, max_duration_ms) / 1000.0 if max_duration_ms else 0.0

    # Empty timeline
    if not cues:
        issues.append(
            TimelineIssue("error", "empty_timeline", None, "A timeline não tem cues.")
        )
        return issues

    extracted: list[tuple[str, float | None, float | None]] = []
    for index, item in enumerate(cues, start=1):
        text, start, end = _extract(item)
        extracted.append((text, start, end))
        if start is None or end is None:
            issues.append(
                TimelineIssue(
                    "error", "invalid_time", index,
                    f"Linha {index}: tempo inválido (start={start}, end={end}).",
                )
            )
            continue
        if not text.strip():
            issues.append(
                TimelineIssue(
                    "error", "empty_text", index, f"Linha {index}: texto vazio."
                )
            )
        if start < 0:
            issues.append(
                TimelineIssue(
                    "error", "negative_start", index,
                    f"Linha {index}: início negativo ({start:.2f}s).",
                )
            )
        if end < start:
            issues.append(
                TimelineIssue(
                    "error", "end_before_start", index,
                    f"Linha {index}: fim ({end:.2f}s) antes do início ({start:.2f}s).",
                )
            )
            continue
        duration = end - start
        if duration > 0 and duration < min_duration:
            issues.append(
                TimelineIssue(
                    "warning", "too_short", index,
                    f"Linha {index}: duração muito curta ({duration:.2f}s < {min_duration:.2f}s).",
                )
            )
        if max_duration > 0 and duration > max_duration:
            issues.append(
                TimelineIssue(
                    "warning", "too_long", index,
                    f"Linha {index}: duração muito longa ({duration:.2f}s > {max_duration:.2f}s).",
                )
            )
        if audio_duration is not None and start > audio_duration + 0.01:
            issues.append(
                TimelineIssue(
                    "error", "start_beyond_audio", index,
                    f"Linha {index}: início ({start:.2f}s) além da duração do áudio ({audio_duration:.2f}s).",
                )
            )
        if audio_duration is not None and end > audio_duration + 0.5:
            issues.append(
                TimelineIssue(
                    "warning", "beyond_audio", index,
                    f"Linha {index}: fim ({end:.2f}s) além da duração do áudio ({audio_duration:.2f}s).",
                )
            )

    # Ordering and overlap checks (only on valid entries)
    valid = [(i, e) for i, e in enumerate(extracted, start=1) if e[1] is not None and e[2] is not None]
    for pos in range(1, len(valid)):
        prev_line, (prev_text, prev_start, prev_end) = valid[pos - 1]
        curr_line, (curr_text, curr_start, curr_end) = valid[pos]
        if curr_start < prev_start - 1e-9:  # type: ignore[operator]
            issues.append(
                TimelineIssue(
                    "error", "not_monotonic", curr_line,
                    f"Linha {curr_line}: início ({curr_start:.2f}s) anterior ao da linha {prev_line} ({prev_start:.2f}s).",
                )
            )
        gap = curr_start - prev_end  # type: ignore[operator]
        if gap < -1e-9:
            issues.append(
                TimelineIssue(
                    "error", "overlap", curr_line,
                    f"Linha {curr_line}: sobrepõe a linha {prev_line} "
                    f"({prev_end:.2f}s → {curr_start:.2f}s).",
                )
            )
        elif 0 <= gap < min_gap:
            issues.append(
                TimelineIssue(
                    "warning", "gap_too_small", curr_line,
                    f"Linha {curr_line}: intervalo muito curto "
                    f"({gap:.2f}s < {min_gap:.2f}s) após a linha {prev_line}.",
                )
            )

    # "Disappears before the next cue" checks
    for index in range(len(cues) - 1):
        text_a, start_a, end_a = extracted[index]
        text_b, start_b, end_b = extracted[index + 1]
        if start_a is None or end_a is None or start_b is None:
            continue
        gap_to_next = start_b - end_a
        if gap_to_next > hold_seconds + 0.5:
            issues.append(
                TimelineIssue(
                    "info", "could_hold_longer", index + 1,
                    f"Linha {index + 1}: termina em {end_a:.2f}s mas a próxima só começa em "
                    f"{start_b:.2f}s; o hold atual ({hold_seconds:.1f}s) pode deixar a "
                    "frase sumir antes do tempo.",
                )
            )

    # Global audio bounds
    if audio_duration is not None and extracted:
        last = extracted[-1]
        if last[2] is not None and last[2] > audio_duration + 0.5:
            issues.append(
                TimelineIssue(
                    "warning", "beyond_audio_end", None,
                    f"Última cue termina em {last[2]:.2f}s, além da duração do áudio ({audio_duration:.2f}s).",
                )
            )

    return issues


def check_sync(
    cantado_cues: Sequence[Any],
    playback_cues: Sequence[Any] | None,
    *,
    tolerance: float = 0.01,
) -> list[TimelineIssue]:
    """Validate that cantado and playback cues share the same timeline.

    Used to enforce the "Quem É Ele" requirement that both videos start at the
    same time (offset 0).
    """

    issues: list[TimelineIssue] = []
    if not playback_cues:
        return issues
    if len(cantado_cues) != len(playback_cues):
        issues.append(
            TimelineIssue(
                "warning", "cue_count_mismatch", None,
                f"Quantidade de cues diferente: cantado={len(cantado_cues)}, "
                f"playback={len(playback_cues)}.",
            )
        )
    count = min(len(cantado_cues), len(playback_cues))
    for index in range(count):
        c_text, c_start, c_end = _extract(cantado_cues[index])
        p_text, p_start, p_end = _extract(playback_cues[index])
        if c_start is None or p_start is None:
            continue
        if abs(c_start - p_start) > tolerance:
            issues.append(
                TimelineIssue(
                    "error", "sync_start_mismatch", index + 1,
                    f"Linha {index + 1}: início divergente "
                    f"(cantado={c_start:.2f}s, playback={p_start:.2f}s).",
                )
            )
        if c_end is not None and p_end is not None and abs(c_end - p_end) > tolerance * 5:
            issues.append(
                TimelineIssue(
                    "warning", "sync_end_mismatch", index + 1,
                    f"Linha {index + 1}: fim divergente "
                    f"(cantado={c_end:.2f}s, playback={p_end:.2f}s).",
                )
            )
    # First-cue check (the most important for "start at same time")
    if cantado_cues and playback_cues:
        c0 = _extract(cantado_cues[0])[1]
        p0 = _extract(playback_cues[0])[1]
        if c0 is not None and p0 is not None and abs(c0 - p0) > tolerance:
            issues.append(
                TimelineIssue(
                    "error", "first_cue_mismatch", 1,
                    f"Primeira cue divergente: cantado={c0:.2f}s, playback={p0:.2f}s. "
                    "Ajuste o offset do playback para 0.",
                )
            )
    return issues


def validate_lyrics_vs_audio(matches) -> list[TimelineIssue]:
    """Detect cues whose lyrics were not found in the audio."""
    issues: list[TimelineIssue] = []
    for match in matches or []:
        idx = getattr(match, "cue_index", None)
        if idx is None:
            continue
        line = idx + 1
        status = getattr(match, "status", "")
        coverage = float(getattr(match, "coverage", 0.0) or 0.0)
        similarity = float(getattr(match, "similarity", 0.0) or 0.0)
        if status == "weak" or coverage < 0.4:
            issues.append(TimelineIssue("warning", "lyrics_not_found", line,
                "Linha %d: letra possivelmente nao cantada no audio "
                "(cobertura %.0f%%, similaridade %.0f%%)." % (line, coverage*100, similarity*100)))
        elif status == "changed" and similarity < 0.78:
            issues.append(TimelineIssue("info", "lyrics_changed", line,
                "Linha %d: texto cantado difere da letra fornecida "
                "(similaridade %.0f%%)." % (line, similarity*100)))
    return issues


def compute_confidence_scores(matches, cue_count: int) -> list[float]:
    """Return per-cue confidence (0..1) from transcript matches.

    Combines similarity and coverage; weak/changed matches are penalized.
    Missing cues default to 0.5.
    """

    scores = [0.5] * cue_count
    for match in matches or []:
        idx = getattr(match, "cue_index", None)
        if idx is None or idx < 0 or idx >= cue_count:
            continue
        similarity = float(getattr(match, "similarity", 0.0) or 0.0)
        coverage = float(getattr(match, "coverage", 0.0) or 0.0)
        status = getattr(match, "status", "")
        score = similarity * max(0.5, coverage)
        if status == "weak":
            score *= 0.6
        elif status == "changed":
            score *= 0.8
        elif status in ("match",):
            score = max(score, 0.7)
        scores[idx] = max(0.0, min(1.0, score))
    return scores


def has_errors(issues: Iterable[TimelineIssue]) -> bool:
    return any(issue.is_error for issue in issues)


def format_issues(issues: Sequence[TimelineIssue]) -> str:
    if not issues:
        return "Timeline OK: nenhum problema encontrado."
    lines = []
    for issue in issues:
        prefix = {"error": "ERRO", "warning": "AVISO", "info": "INFO"}.get(
            issue.severity, issue.severity.upper()
        )
        location = f"L{issue.line}" if issue.line is not None else "global"
        lines.append(f"[{prefix}] {location}: {issue.message}")
    return "\n".join(lines)
