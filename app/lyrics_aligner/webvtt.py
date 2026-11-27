from __future__ import annotations

from collections.abc import Sequence

from .models import Cue
from .srt import format_timestamp


def format_vtt_timestamp(seconds: float) -> str:
    """Format seconds as WebVTT ``HH:MM:SS.mmm`` (dot millisecond separator)."""
    return format_timestamp(seconds).replace(",", ".")


def render_webvtt(
    cues: Sequence[Cue],
    *,
    offset_seconds: float = 0.0,
    title: str | None = None,
) -> str:
    """Render visible cues as WebVTT for browser preview tracks."""
    blocks: list[str] = ["WEBVTT"]
    if title:
        blocks.append(f"NOTE {title}")
    blocks.append("")
    for index, cue in enumerate(cues, start=1):
        start = format_vtt_timestamp(cue.start + offset_seconds)
        end = format_vtt_timestamp(cue.end + offset_seconds)
        blocks.append(f"{index}\n{start} --> {end}\n{cue.text}\n")
    return "\n".join(blocks)