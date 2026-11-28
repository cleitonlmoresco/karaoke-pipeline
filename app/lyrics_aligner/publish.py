from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .burn_lock import burn_guard
from .models import Cue
from .pipeline import align_to_srt
from .subtitles import AssStyle, render_ass, validate_ass_style


_ALIGNMENT_MANIFEST_KEYS = (
    "max_chars",
    "auto_segment",
    "language",
    "separate",
    "partial_range",
    "model_name",
    "lead_ms",
    "offset_seconds",
    "range_start_seconds",
    "range_end_seconds",
    "timeline_anchor",
    "end_pad_ms",
    "min_duration_ms",
    "max_duration_ms",
    "min_gap_ms",
)


@dataclass(frozen=True)
class PublishResult:
    output_dir: Path
    srt_path: Path
    ass_path: Path
    manifest_path: Path
    video_path: Path | None
    cues: tuple[Cue, ...]


def default_output_dir(media_path: str | Path) -> Path:
    """Return the conventional package folder beside the source media."""
    media_path = Path(media_path)
    return media_path.parent / f"{media_path.stem}_lyrics"


def find_ffmpeg(explicit: str | Path | None = None) -> Path:
    """Locate FFmpeg, including WinGet installs that are absent from PATH."""
    if explicit is not None:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return candidate.resolve()
        found = shutil.which(str(explicit))
        if found:
            return Path(found).resolve()
        raise FileNotFoundError(f"FFmpeg executable not found: {explicit}")

    for variable in ("VILM_FFMPEG", "FFMPEG"):
        value = os.environ.get(variable)
        if value:
            candidate = Path(value).expanduser()
            if candidate.is_file():
                return candidate.resolve()
            found = shutil.which(value)
            if found:
                return Path(found).resolve()

    found = shutil.which("ffmpeg")
    if found:
        return Path(found).resolve()

    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data)
        candidates.append(root / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")
        packages = root / "Microsoft" / "WinGet" / "Packages"
        if packages.is_dir():
            candidates.extend(packages.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"))
    candidates.extend(
        (
            Path("/opt/homebrew/bin/ffmpeg"),
            Path("/usr/local/bin/ffmpeg"),
            Path("/usr/bin/ffmpeg"),
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(
        "FFmpeg was not found. Install it and add it to PATH, or pass --ffmpeg "
        "with the full executable path. On Windows: winget install Gyan.FFmpeg"
    )


def _ffmpeg_color(value: str) -> str:
    text = str(value or "").strip().removeprefix("#")
    if len(text) != 6:
        raise ValueError("FFmpeg colors must use #RRGGBB")
    try:
        int(text, 16)
    except ValueError as exc:
        raise ValueError("FFmpeg colors must use #RRGGBB") from exc
    return text


def build_burn_command(
    ffmpeg: str | Path,
    video_path: str | Path,
    ass_name: str,
    output_path: str | Path,
    *,
    preset: str = "medium",
    crf: int = 18,
    width: int | None = None,
    height: int | None = None,
    pad_color: str = "#000000",
    threads: int | None = None,
    fps: int | None = None,
) -> list[str]:
    """Build the FFmpeg command used to burn an ASS file into a new MP4."""
    if Path(ass_name).name != ass_name:
        raise ValueError("The ASS filter name must be a plain file name")
    if not 0 <= int(crf) <= 51:
        raise ValueError("CRF must be between 0 and 51")
    if fps is not None and not 1 <= int(fps) <= 120:
        raise ValueError("FPS must be between 1 and 120")
    if (width is None) != (height is None):
        raise ValueError("Output width and height must be provided together")
    filters: list[str] = []
    if width is not None and height is not None:
        if width <= 0 or height <= 0:
            raise ValueError("Output width and height must be positive")
        if width % 2 or height % 2:
            raise ValueError("Output width and height must be even")
        filters.append(
            f"scale={width}:{height}:force_original_aspect_ratio=decrease"
        )
        filters.append(
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            f":color=0x{_ffmpeg_color(pad_color)}"
        )
        filters.append("setsar=1")
    filters.append(f"ass={ass_name}")
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vf",
        ",".join(filters),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(int(crf)),
        "-pix_fmt",
        "yuv420p",
    ]
    if threads is not None and threads > 0:
        command += ["-threads", str(int(threads))]
    if fps is not None:
        command += ["-r", str(int(fps))]
    command += [
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return command


def burn_subtitles(
    video_path: str | Path,
    ass_path: str | Path,
    output_path: str | Path,
    *,
    ffmpeg: str | Path | None = None,
    preset: str = "medium",
    crf: int = 18,
    width: int | None = None,
    height: int | None = None,
    pad_color: str = "#000000",
    fps: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Render a new MP4 with the ASS subtitles burned into the picture."""
    video_path = Path(video_path).resolve()
    ass_path = Path(ass_path).resolve()
    output_path = Path(output_path).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(video_path)
    if not ass_path.is_file():
        raise FileNotFoundError(ass_path)
    if output_path == video_path:
        raise ValueError("The burned output must not overwrite the source video")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_path = find_ffmpeg(ffmpeg)
    command = build_burn_command(
        ffmpeg_path,
        video_path,
        ass_path.name,
        output_path,
        preset=preset,
        crf=crf,
        width=width,
        height=height,
        pad_color=pad_color,
        fps=fps,
    )
    report = progress or (lambda _message: None)
    report(f"Rendering final video with FFmpeg ({preset}, CRF {crf})…")
    completed = subprocess.run(
        command,
        cwd=ass_path.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        if "memory" in detail.casefold() or "memória" in detail.casefold():
            report("Pouca memória: tentando novamente em modo econômico…")
            retry_command = build_burn_command(
                ffmpeg_path,
                video_path,
                ass_path.name,
                output_path,
                preset="veryfast",
                crf=crf,
                width=width,
                height=height,
                pad_color=pad_color,
                fps=fps,
                threads=1,
            )
            retry = subprocess.run(
                retry_command,
                cwd=ass_path.parent,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if retry.returncode == 0:
                completed = retry
            else:
                detail += " | nova tentativa: " + (
                    retry.stderr.strip() or retry.stdout.strip()
                )
        if completed.returncode != 0:
            raise RuntimeError(
                f"FFmpeg failed with exit code {completed.returncode}: {detail}"
            )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg finished without creating a valid video file")
    return output_path


def build_manifest(
    *,
    media_path: str | Path,
    lyrics_path: str | Path | None,
    title: str,
    style: AssStyle,
    cues: Sequence[Cue],
    alignment: Mapping[str, Any],
    outputs: Mapping[str, str | None],
    burn: Mapping[str, Any],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build the JSON handoff document that describes the published package."""
    timestamp = created_at or (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return {
        "schema": "vilm-lyrics-publish/1",
        "created_at": timestamp,
        "title": title,
        "source": {
            "media": str(Path(media_path).resolve()),
            "lyrics": (
                str(Path(lyrics_path).resolve()) if lyrics_path is not None else None
            ),
        },
        "alignment": dict(alignment),
        "style": asdict(style),
        "cues": [
            {
                "index": index,
                "start": round(cue.start, 3),
                "end": round(cue.end, 3),
                "text": cue.text,
            }
            for index, cue in enumerate(cues, start=1)
        ],
        "outputs": dict(outputs),
        "burn": dict(burn),
    }


def write_manifest(path: str | Path, payload: Mapping[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def publish(
    media_path: str | Path,
    lyrics_text: str,
    output_dir: str | Path,
    *,
    lyrics_path: str | Path | None = None,
    title: str | None = None,
    style: AssStyle | None = None,
    burn: bool = False,
    ffmpeg: str | Path | None = None,
    preset: str = "medium",
    crf: int = 18,
    fps: int | None = None,
    align_options: Mapping[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> PublishResult:
    """Align lyrics and publish SRT, ASS, a manifest, and an optional burned MP4."""
    media_path = Path(media_path)
    if not media_path.is_file():
        raise FileNotFoundError(media_path)
    media_path = media_path.resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = output_dir / "lyrics.srt"
    ass_path = output_dir / "lyrics.ass"
    manifest_path = output_dir / "manifest.json"
    resolved_title = (title or media_path.stem).strip() or media_path.stem
    style = style or AssStyle()
    validate_ass_style(style)
    ffmpeg_path: Path | None = None
    if burn:
        if not 0 <= int(crf) <= 51:
            raise ValueError("CRF must be between 0 and 51")
        if fps is not None and not 1 <= int(fps) <= 120:
            raise ValueError("FPS must be between 1 and 120")
        ffmpeg_path = find_ffmpeg(ffmpeg)

    options = dict(align_options or {})
    if progress is not None:
        options.setdefault("progress", progress)
    cues = align_to_srt(media_path, lyrics_text, srt_path, **options)

    offset_seconds = float(options.get("offset_seconds", 0.0) or 0.0)
    range_start = max(0.0, float(options.get("range_start_seconds", 0.0) or 0.0))
    ass_path.write_text(
        render_ass(
            cues,
            title=resolved_title,
            offset_seconds=offset_seconds + range_start,
            style=style,
        ),
        encoding="utf-8",
    )

    alignment = {
        key: options.get(key) for key in _ALIGNMENT_MANIFEST_KEYS if key in options
    }
    alignment["effective_offset_seconds"] = offset_seconds + range_start
    alignment["cue_count"] = len(cues)
    outputs: dict[str, str | None] = {
        "srt": srt_path.name,
        "ass": ass_path.name,
        "video": None,
    }
    burn_info: dict[str, Any] = {
        "requested": bool(burn),
        "completed": False,
        "ffmpeg": None,
        "preset": preset if burn else None,
        "crf": int(crf) if burn else None,
        "error": None,
    }
    if burn:
        burn_info["ffmpeg"] = str(ffmpeg_path)

    payload = build_manifest(
        media_path=media_path,
        lyrics_path=lyrics_path,
        title=resolved_title,
        style=style,
        cues=cues,
        alignment=alignment,
        outputs=outputs,
        burn=burn_info,
    )
    write_manifest(manifest_path, payload)

    video_path: Path | None = None
    if burn:
        video_path = output_dir / "video_with_lyrics.mp4"
        try:
            # Mesma protecao da GUI: nenhum burn paralelo no mesmo destino.
            with burn_guard(output_dir, progress=progress):
                burn_subtitles(
                    media_path,
                    ass_path,
                    video_path,
                    ffmpeg=burn_info["ffmpeg"],
                    preset=preset,
                    crf=crf,
                    fps=fps,
                    progress=progress,
                )
        except Exception as exc:
            burn_info["error"] = str(exc)
            payload = build_manifest(
                media_path=media_path,
                lyrics_path=lyrics_path,
                title=resolved_title,
                style=style,
                cues=cues,
                alignment=alignment,
                outputs=outputs,
                burn=burn_info,
            )
            write_manifest(manifest_path, payload)
            raise
        burn_info["completed"] = True
        outputs["video"] = video_path.name
        payload = build_manifest(
            media_path=media_path,
            lyrics_path=lyrics_path,
            title=resolved_title,
            style=style,
            cues=cues,
            alignment=alignment,
            outputs=outputs,
            burn=burn_info,
        )
        write_manifest(manifest_path, payload)

    return PublishResult(
        output_dir=output_dir,
        srt_path=srt_path,
        ass_path=ass_path,
        manifest_path=manifest_path,
        video_path=video_path,
        cues=tuple(cues),
    )