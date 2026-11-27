"""Multi-format export helpers (Bloco G2)."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .models import Cue
from .srt import render_srt
from .subtitles import render_ass, AssStyle
from .webvtt import render_webvtt as render_vtt  # real implementation


def _ensure_vtt(cues: Sequence[Cue], offset: float = 0.0) -> str:
    try:
        from .webvtt import render_vtt
        return render_vtt(cues, offset_seconds=offset)
    except Exception:
        # Fallback minimal VTT
        lines = ["WEBVTT", ""]
        for c in cues:
            def fmt(t):
                h = int(t // 3600)
                m = int((t % 3600) // 60)
                s = t % 60
                return f"{h:02d}:{m:02d}:{s:06.3f}"
            lines.append(f"{fmt(c.start + offset)} --> {fmt(c.end + offset)}")
            lines.append(c.text)
            lines.append("")
        return "\n".join(lines)


def export_all(cues: Sequence[Cue], output_dir: Path, title: str = "Vilm", offset: float = 0.0) -> list[Path]:
    """Export cues to SRT, VTT, ASS, JSON and TXT. Returns list of created files."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    # SRT
    srt_path = output_dir / "lyrics.srt"
    srt_path.write_text(render_srt(cues, offset_seconds=offset), encoding="utf-8-sig")
    created.append(srt_path)

    # VTT
    vtt_path = output_dir / "lyrics.vtt"
    vtt_path.write_text(_ensure_vtt(cues, offset), encoding="utf-8")
    created.append(vtt_path)

    # ASS
    ass_path = output_dir / "lyrics.ass"
    ass_path.write_text(render_ass(cues, title=title, offset_seconds=offset), encoding="utf-8")
    created.append(ass_path)

    # JSON
    import json
    json_path = output_dir / "lyrics.json"
    payload = [{"index": i, "start": round(c.start, 3), "end": round(c.end, 3), "text": c.text} for i, c in enumerate(cues, 1)]
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    created.append(json_path)

    # TXT (timestamps)
    txt_path = output_dir / "lyrics.txt"
    txt_lines = []
    for i, c in enumerate(cues, 1):
        txt_lines.append(f"[{i}] {c.start:.2f} - {c.end:.2f} | {c.text}")
    txt_path.write_text("\n".join(txt_lines) + "\n", encoding="utf-8")
    created.append(txt_path)

    return created
