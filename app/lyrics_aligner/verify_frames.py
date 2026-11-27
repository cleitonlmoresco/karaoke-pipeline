"""Extract frames at specified timestamps and generate an HTML report."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def extract_frames(
    video_path: Path,
    timestamps: list[float],
    output_dir: Path,
    ffmpeg: str | None = None,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg or "ffmpeg"
    result = []
    for i, ts in enumerate(timestamps):
        out = output_dir / f"frame_{i:03d}_{ts:.2f}s.jpg"
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-ss", f"{ts:.3f}", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "2", str(out),
        ]
        subprocess.run(cmd, check=False, capture_output=True)
        if out.is_file():
            result.append(out)
    return result


def generate_html(frames: list[Path], timestamps: list[float], output_html: Path) -> None:
    rows = []
    for frame, ts in zip(frames, timestamps):
        rows.append(
            f'<div style="display:inline-block;margin:8px;text-align:center;">'
            f'<img src="{frame.name}" style="max-width:320px;border:1px solid #ccc;"><br>'
            f'<span>{ts:.2f}s</span></div>'
        )
    html = (
        "<html><head><meta charset='utf-8'><title>Frames</title></head><body>"
        "<h2>Verificação de frames</h2>" + "".join(rows) + "</body></html>"
    )
    output_html.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract frames and generate HTML report.")
    parser.add_argument("video_path", type=Path)
    parser.add_argument("timestamps", nargs="+", type=float)
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("frames"))
    parser.add_argument("--ffmpeg", type=str, default=None)
    args = parser.parse_args()

    frames = extract_frames(args.video_path, args.timestamps, args.output_dir, args.ffmpeg)
    html_path = args.output_dir / "report.html"
    generate_html(frames, args.timestamps, html_path)
    print(f"Extraídos {len(frames)} frames. Relatório: {html_path}")


if __name__ == "__main__":
    main()
