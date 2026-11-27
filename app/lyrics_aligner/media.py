from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any


def media_duration(path: str | Path) -> float:
    path = Path(path)
    if path.suffix.casefold() == ".wav":
        try:
            import soundfile
        except ImportError as exc:
            raise RuntimeError("soundfile is required to inspect WAV files") from exc
        info = soundfile.info(str(path))
        if info.samplerate <= 0:
            raise ValueError(f"Could not determine media duration: {path}")
        return info.frames / float(info.samplerate)
    try:
        import av
    except ImportError as exc:
        raise RuntimeError("PyAV is required to inspect media files") from exc
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError(f"No audio stream found in {path}")
        stream = container.streams.audio[0]
        if stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
        if container.duration is not None:
            return float(container.duration / av.time_base)
    raise ValueError(f"Could not determine media duration: {path}")


def waveform_peaks(path: str | Path, bins: int = 720) -> tuple[float, list[float]]:
    """Return normalized mono peaks without retaining decoded media in memory."""
    if bins < 8:
        raise ValueError("bins must be at least 8")
    try:
        import av
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("PyAV and NumPy are required to draw waveforms") from exc

    path = Path(path)
    duration = media_duration(path)
    if duration <= 0:
        raise ValueError("Media duration must be positive")
    sample_rate = 8000
    total_samples = max(1, int(math.ceil(duration * sample_rate)))
    peaks = np.zeros(bins, dtype=np.float32)
    sample_cursor = 0

    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError(f"No audio stream found in {path}")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=sample_rate)

        def consume(frame) -> None:
            nonlocal sample_cursor
            values = np.abs(frame.to_ndarray().reshape(-1)).astype(np.float32, copy=False)
            if not len(values):
                return
            positions = np.arange(sample_cursor, sample_cursor + len(values), dtype=np.int64)
            indices = np.minimum(bins - 1, positions * bins // total_samples)
            np.maximum.at(peaks, indices, values)
            sample_cursor += len(values)

        for decoded in container.decode(stream):
            for converted in resampler.resample(decoded):
                consume(converted)
        for converted in resampler.resample(None):
            consume(converted)

    scale = float(np.percentile(peaks, 98)) if np.any(peaks) else 0.0
    if scale > 0:
        peaks = np.clip(peaks / scale, 0.0, 1.0)
    return duration, [float(value) for value in peaks]


def extract_audio_range(
    input_path: str | Path,
    output_path: str | Path,
    start_seconds: float,
    end_seconds: float | None,
    *,
    sample_rate: int = 48000,
) -> Path:
    """Decode only the requested media interval to stereo PCM WAV."""
    try:
        import av
        import soundfile
    except ImportError as exc:
        raise RuntimeError("PyAV and soundfile are required to extract audio") from exc

    input_path = Path(input_path).resolve()
    output_path = Path(output_path).resolve()
    duration = media_duration(input_path)
    start = max(0.0, float(start_seconds))
    end = duration if end_seconds is None else min(duration, float(end_seconds))
    if end <= start:
        raise ValueError("The selected range must have a positive duration")

    first_sample = int(round(start * sample_rate))
    last_sample = int(round(end * sample_rate))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cursor = 0
    wrote = 0

    with av.open(str(input_path)) as container:
        if not container.streams.audio:
            raise ValueError(f"No audio stream found in {input_path}")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=sample_rate)
        with soundfile.SoundFile(
            str(output_path),
            mode="w",
            samplerate=sample_rate,
            channels=2,
            subtype="PCM_24",
        ) as destination:
            def consume(frame) -> bool:
                nonlocal cursor, wrote
                samples = frame.to_ndarray().T
                frame_start = cursor
                frame_end = cursor + len(samples)
                cursor = frame_end
                if frame_end <= first_sample:
                    return True
                if frame_start >= last_sample:
                    return False
                left = max(0, first_sample - frame_start)
                right = min(len(samples), last_sample - frame_start)
                if right > left:
                    destination.write(samples[left:right])
                    wrote += right - left
                return frame_end < last_sample

            keep_going = True
            for decoded in container.decode(stream):
                for converted in resampler.resample(decoded):
                    keep_going = consume(converted)
                    if not keep_going:
                        break
                if not keep_going:
                    break
            if keep_going:
                for converted in resampler.resample(None):
                    if not consume(converted):
                        break

    if wrote <= 0:
        output_path.unlink(missing_ok=True)
        raise ValueError("No decodable audio was found in the selected range")
    return output_path


# Media probing for the Tk frontend.  Kept here (instead of importing the
# legacy webapp module) so the burn pipeline can decide whether it needs to
# synthesize a background video from an audio-only source.
_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mts", ".m2ts",
    ".wmv", ".flv", ".f4v", ".mpg", ".mpeg", ".m2v", ".vob", ".ts",
    ".mxf", ".ogv", ".3gp", ".3g2", ".asf", ".rm", ".rmvb", ".dv",
}


def _find_ffprobe() -> Path | None:
    for variable in ("VILM_FFPROBE", "FFPROBE"):
        value = os.environ.get(variable)
        if value:
            candidate = Path(value).expanduser()
            if candidate.is_file():
                return candidate.resolve()
    found = shutil.which("ffprobe")
    if found:
        return Path(found).resolve()
    try:
        from .publish import find_ffmpeg

        ffmpeg_path = find_ffmpeg()
    except (ImportError, FileNotFoundError):
        ffmpeg_path = None
    if ffmpeg_path is not None:
        sibling = ffmpeg_path.with_name(
            "ffprobe.exe" if os.name == "nt" else "ffprobe"
        )
        if sibling.is_file():
            return sibling.resolve()
    return None


def probe_media(path: str | Path) -> dict[str, Any]:
    """Return duration/video/audio information without importing webapp."""
    path = Path(path)
    info: dict[str, Any] = {
        "duration": None,
        "has_video": path.suffix.casefold() in _VIDEO_EXTENSIONS,
        "has_audio": True,
        "width": None,
        "height": None,
        "video_codec": None,
        "audio_codec": None,
    }
    ffprobe = _find_ffprobe()
    if ffprobe is not None:
        completed = subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=index,codec_type,codec_name,width,height:stream_disposition=attached_pic",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode == 0:
            try:
                payload = json.loads(completed.stdout or "{}")
            except ValueError:
                payload = {}
            fmt = payload.get("format") or {}
            if fmt.get("duration") is not None:
                info["duration"] = float(fmt["duration"])
            video = audio = None
            for stream in payload.get("streams") or []:
                codec_type = stream.get("codec_type")
                disposition = stream.get("disposition") or {}
                # Capas de album (attached_pic) nao sao video real.
                is_cover = bool(disposition.get("attached_pic"))
                if codec_type == "video" and not is_cover and video is None:
                    video = stream
                elif codec_type == "audio" and audio is None:
                    audio = stream
            info["has_video"] = video is not None
            info["has_audio"] = audio is not None
            if video is not None:
                info["width"] = video.get("width")
                info["height"] = video.get("height")
                info["video_codec"] = video.get("codec_name")
            if audio is not None:
                info["audio_codec"] = audio.get("codec_name")
    if info["duration"] is None and path.suffix.casefold() == ".wav":
        try:
            with wave.open(str(path), "rb") as wav:
                info["duration"] = wav.getnframes() / float(wav.getframerate())
        except (OSError, wave.Error):
            pass
    return info
