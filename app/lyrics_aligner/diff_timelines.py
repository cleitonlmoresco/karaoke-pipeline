"""Compare two timeline files (tempos.json or ASS) and report differences."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_tempos(path: Path) -> list[tuple[str, float, float]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    track = data.get("cantado") or data.get("playback_cantado") or []
    result = []
    for entry in track:
        if isinstance(entry, (list, tuple)):
            text = str(entry[0]) if entry else ""
            words = entry[1] if len(entry) > 1 else []
        elif isinstance(entry, dict):
            text = str(entry.get("text", ""))
            words = entry.get("palavras") or entry.get("words") or []
        else:
            continue
        if not words:
            result.append((text, 0.0, 0.0))
            continue
        try:
            start = float(words[0][1])
            end = float(words[-1][2])
        except (TypeError, ValueError, IndexError):
            start = end = 0.0
        result.append((text, start, end))
    return result


def diff_timelines(path_a: Path, path_b: Path) -> str:
    a = _load_tempos(path_a)
    b = _load_tempos(path_b)
    lines = ["# Diff de timelines", "", f"A: {path_a.name}", f"B: {path_b.name}", ""]
    lines.append(f"{'#':>3} {'A start':>8} {'A end':>8} {'B start':>8} {'B end':>8}  texto")
    lines.append("-" * 70)
    count = max(len(a), len(b))
    changed = 0
    for i in range(count):
        ta, sa, ea = a[i] if i < len(a) else ("(ausente)", 0.0, 0.0)
        tb, sb, eb = b[i] if i < len(b) else ("(ausente)", 0.0, 0.0)
        marker = ""
        if ta != tb or abs(sa - sb) > 0.01 or abs(ea - eb) > 0.01:
            marker = "  <--"
            changed += 1
        text = ta if ta == tb else f"{ta} -> {tb}"
        lines.append(f"{i+1:3} {sa:8.2f} {ea:8.2f} {sb:8.2f} {eb:8.2f}  {text}{marker}")
    lines.append("")
    lines.append(f"Total: {count} cues, {changed} alteradas.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two tempos.json files.")
    parser.add_argument("file_a", type=Path)
    parser.add_argument("file_b", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args()

    result = diff_timelines(args.file_a, args.file_b)
    if args.output:
        args.output.write_text(result, encoding="utf-8")
        print(f"Salvo em {args.output}")
    else:
        print(result)


if __name__ == "__main__":
    main()
