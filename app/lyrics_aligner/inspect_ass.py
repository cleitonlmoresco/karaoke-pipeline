"""Inspect an ASS file's Dialogue lines within a time range."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def parse_ass_dialogues(path: str | Path) -> list[tuple[float, float, str]]:
    """Return list of (start_sec, end_sec, text) for Dialogue lines."""
    result = []
    for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        def to_sec(t: str) -> float:
            # H:MM:SS.cc
            h, m, s = t.strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        try:
            start = to_sec(parts[1])
            end = to_sec(parts[2])
        except ValueError:
            continue
        text = parts[9]
        # strip override tags
        text = re.sub(r"\{[^}]*\}", "", text).replace("\\N", " | ")
        result.append((start, end, text.strip()))
    return result


def inspect_ass(path: str | Path, start: float = 0.0, end: float | None = None) -> list[tuple[float, float, str]]:
    dialogues = parse_ass_dialogues(path)
    if end is None:
        end = float("inf")
    return [(s, e, t) for (s, e, t) in dialogues if s < end and e > start]


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect ASS Dialogue lines in a time range.")
    parser.add_argument("ass_path", type=Path)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--end", type=float, default=None)
    args = parser.parse_args()

    rows = inspect_ass(args.ass_path, args.start, args.end)
    print(f"{'START':>8} {'END':>8}  TEXT")
    print("-" * 60)
    for s, e, t in rows:
        print(f"{s:8.2f} {e:8.2f}  {t}")
    print(f"\nTotal: {len(rows)} linhas")


if __name__ == "__main__":
    main()
