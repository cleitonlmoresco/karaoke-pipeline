"""Compare two cue lists (A/B) for regression/quality checks (Bloco F2)."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence


def compare_versions(cues_a: Sequence, cues_b: Sequence) -> str:
    """Compare two cue lists and report per-cue differences."""
    lines = ["# Comparação A/B de versões", ""]
    lines.append(f"Versão A: {len(cues_a)} cues")
    lines.append(f"Versão B: {len(cues_b)} cues")
    lines.append("")

    count = max(len(cues_a), len(cues_b))
    changed = 0
    for i in range(count):
        a = cues_a[i] if i < len(cues_a) else None
        b = cues_b[i] if i < len(cues_b) else None
        if a is None:
            lines.append(f"{i+1:3}  [apenas B] {getattr(b, 'text', b)}")
            changed += 1
            continue
        if b is None:
            lines.append(f"{i+1:3}  [apenas A] {getattr(a, 'text', a)}")
            changed += 1
            continue
        ta = a.text if hasattr(a, "text") else str(a)
        tb = b.text if hasattr(b, "text") else str(b)
        sa, ea = (a.start, a.end) if hasattr(a, "start") else (0.0, 0.0)
        sb, eb = (b.start, b.end) if hasattr(b, "start") else (0.0, 0.0)
        diff_s = abs(sa - sb)
        diff_e = abs(ea - eb)
        marker = ""
        if ta != tb or diff_s > 0.01 or diff_e > 0.01:
            marker = "  <--"
            changed += 1
        text = ta if ta == tb else f"{ta} -> {tb}"
        lines.append(f"{i+1:3}  A:{sa:7.2f}-{ea:7.2f}  B:{sb:7.2f}-{eb:7.2f}  {text}{marker}")
    lines.append("")
    lines.append(f"Total: {count} cues, {changed} alteradas.")
    return "\n".join(lines)


def load_cues_from_srt(path: str | Path) -> list:
    """Load cues from an SRT file for comparison."""
    import re
    text = Path(path).read_text(encoding="utf-8-sig")
    pattern = re.compile(
        r"(\d+)\n(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\n(.*?)(?=\n\d+\n|\Z)",
        re.DOTALL,
    )
    cues = []
    for m in pattern.finditer(text):
        def to_sec(h, mm, s, ms):
            return int(h) * 3600 + int(mm) * 60 + int(s) + int(ms) / 1000.0
        start = to_sec(m.group(2), m.group(3), m.group(4), m.group(5))
        end = to_sec(m.group(6), m.group(7), m.group(8), m.group(9))
        body = m.group(10).strip().replace("\n", " ")
        if body:
            from lyrics_aligner.models import Cue
            cues.append(Cue(text=body, start=start, end=end))
    return cues
