from __future__ import annotations

import re
from collections.abc import Sequence

from .models import Cue

_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def format_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _timestamp_seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis) / 1000.0
    )


def parse_srt(text: str) -> list[Cue]:
    """Read SRT cues back so an existing alignment can be reused."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text):
        match = _TIMESTAMP_RE.search(block)
        if match is None:
            continue
        start = _timestamp_seconds(*match.groups()[:4])
        end = _timestamp_seconds(*match.groups()[4:])
        content = block[match.end() :].strip("\n").strip()
        if not content or not content.strip("\u2800").strip():
            continue
        cues.append(Cue(content, start, end))
    return cues


def render_srt(
    cues: Sequence[Cue],
    offset_seconds: float = 0.0,
    *,
    timeline_anchor: bool = False,
) -> str:
    blocks: list[str] = []
    first_cue_index = 1
    if timeline_anchor and cues:
        earliest_start = min(cue.start + offset_seconds for cue in cues)
        if earliest_start > 0.6:
            # Resolve positions imported SRT clips relative to their first cue.
            # U+2800 looks blank but is a real symbol, so Resolve keeps the cue.
            anchor_end = min(0.5, earliest_start)
            blocks.append(
                "1\n"
                f"00:00:00,000 --> {format_timestamp(anchor_end)}\n"
                "\u2800"
            )
            first_cue_index = 2
    for index, cue in enumerate(cues, start=first_cue_index):
        start = format_timestamp(cue.start + offset_seconds)
        end = format_timestamp(cue.end + offset_seconds)
        blocks.append(f"{index}\n{start} --> {end}\n{cue.text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")
