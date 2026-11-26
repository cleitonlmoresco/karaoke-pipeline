from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import traceback
import unicodedata
import uuid
import wave
import webbrowser
from pathlib import Path
from typing import Any

from .karaoke import (
    KaraokeStyle,
    make_background_video,
    render_karaoke_ass,
    stanza_boundaries_from_lyrics,
)
from .karaoke_jobs import load_profiles, save_profiles
from .lyrics import strip_credits_and_title
from .models import Cue
from .pipeline import align_to_srt, demo_to_srt
from .platform_paths import music_dir
from .publish import build_manifest, burn_subtitles, find_ffmpeg, write_manifest
from .srt import parse_srt, render_srt
from .subtitles import AssStyle, ass_color, render_ass
from .sync import build_audio_time_map
from .webvtt import render_webvtt

DEFAULT_PORT = 8765
_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v",
    ".mts",
    ".m2ts",
    ".wmv",
    ".flv",
    ".f4v",
    ".mpg",
    ".mpeg",
    ".m2v",
    ".vob",
    ".ts",
    ".mxf",
    ".ogv",
    ".3gp",
    ".3g2",
    ".asf",
    ".rm",
    ".rmvb",
    ".dv",
}
_MEDIA_EXTENSIONS = _VIDEO_EXTENSIONS | {
    ".mp3",
    ".mp2",
    ".mpga",
    ".wav",
    ".m4a",
    ".m4b",
    ".aac",
    ".flac",
    ".alac",
    ".ogg",
    ".oga",
    ".opus",
    ".spx",
    ".wma",
    ".aiff",
    ".aif",
    ".aifc",
    ".ape",
    ".wv",
    ".tta",
    ".dsf",
    ".dff",
    ".amr",
    ".au",
    ".snd",
    ".ac3",
    ".eac3",
    ".dts",
    ".mka",
    ".ra",
    ".caf",
}
_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".avif",
}
_BURN_PRESETS = {    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
}
_SAFE_OUTPUT_NAMES = {
    "video_with_lyrics.mp4",
    "video_cantado.mp4",
    "video_playback.mp4",
    "lyrics.srt",
    "lyrics.ass",
    "lyrics.karaoke.ass",
    "lyrics_playback.srt",
    "lyrics_playback.ass",
    "lyrics_playback.karaoke.ass",
    "preview.vtt",
    "preview_playback.vtt",
    "manifest.json",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "alignment": {
        "mode": "auto",
        "language": "auto",
        "model": "small",
        "separate": True,
        "max_chars": 30,
        "lead_ms": 600,
        "end_pad_ms": 500,
        "min_duration_ms": 500,
        "max_duration_ms": 30000,
        "min_gap_ms": 80,
        "offset_seconds": 0.0,
        "playback_offset_seconds": 0.0,
        "range_start": 0.0,
        "range_end": None,
    },
    "karaoke": {
        "enabled": True,
        "normal_color": "#FFFFFF",
        "active_color": "#FFFFFF",
        "outline_color": "#000000",
        "back_color": "#00000080",
        "font_name": "Arial, Segoe UI, Tahoma, Verdana, Georgia, DejaVu Sans",
        "font_size": 90,
        "bold": True,
        "outline": 3.0,
        "shadow": 1.0,
        "alignment": 5,
        "margin_h": 80,
        "margin_v": 80,
        "lines_before": 0,
        "lines_after": 0,
        "lines_per_slide": 3,
        "stanza_mode": "time",
        "transition": "fade",
        "transition_ms": 400,
    },
    "background": {
        "type": "color",
        "color": "#0B1D3A",
        "image_filename": None,
        "fit": "contain",
    },
    "output": {"width": 1920, "height": 1080},
    "burn": {"preset": "medium", "crf": 18},
    "demo": False,
}

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_QUEUE: "queue.Queue[str]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()

# Diretório de projetos em uso. Pode ser sobrescrito pelo usuário via
# POST /api/projects_root sem reiniciar o servidor.
_PROJECTS_ROOT_OVERRIDE: str | None = None
_DEFAULT_PROJECTS_ROOT: str | None = None
_CONFIG_FILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VilmPipeline" / "webapp-config.json"


def _load_saved_projects_root() -> str | None:
    try:
        payload = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = payload.get("projects_root")
    return str(value) if value else None


def _save_projects_root(value: str | None) -> None:
    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {}
        if _CONFIG_FILE.is_file():
            try:
                payload = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            except ValueError:
                payload = {}
        payload["projects_root"] = value
        _CONFIG_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            threading.Thread(
                target=_worker_loop, name="vilm-webapp-worker", daemon=True
            ).start()
            _worker_started = True


def _worker_loop() -> None:
    while True:
        job_id = _QUEUE.get()
        try:
            _run_job(job_id)
        except Exception:  # keep the worker alive
            traceback.print_exc()
        finally:
            _QUEUE.task_done()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _current_projects_root() -> Path:
    """Return the projects root currently in use (honoring user override)."""
    if _PROJECTS_ROOT_OVERRIDE:
        return Path(_PROJECTS_ROOT_OVERRIDE).resolve()
    if _DEFAULT_PROJECTS_ROOT is not None:
        return Path(_DEFAULT_PROJECTS_ROOT)
    return resolved_root


def set_projects_root(value: str | None) -> Path:
    """Set (or clear) the user-chosen projects root override."""
    global _PROJECTS_ROOT_OVERRIDE
    _PROJECTS_ROOT_OVERRIDE = value
    _save_projects_root(value)
    root = _current_projects_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _log(job: dict[str, Any], message: str) -> None:
    job["progress"].append({"t": time.time(), "m": message})
    if len(job["progress"]) > 500:
        job["progress"] = job["progress"][-500:]


def sanitize_project_name(name: str) -> str:
    """Folder-safe project name such as 'Eliane Fernandes - Adorador'."""
    invalid = '<>:"/\\|?*'
    cleaned = "".join(
        character if character not in invalid and ord(character) >= 32 else " "
        for character in name
    )
    cleaned = " ".join(cleaned.split()).rstrip(". ")
    if not cleaned:
        cleaned = "Projeto sem nome"
    return cleaned[:80]


def _resolve_project_file(project_dir: Path, relative: str) -> Path | None:
    """Resolve a user-supplied relative path strictly inside the project."""
    if not relative or "\x00" in relative:
        return None
    candidate = (project_dir / relative).resolve()
    try:
        candidate.relative_to(project_dir.resolve())
    except ValueError:
        return None
    return candidate


def _project_media_files(project_dir: Path) -> list[str]:
    """List video/audio files inside a project folder (depth 2)."""
    excluded = {"saida", "trabalho", "fundo"}
    results: list[Path] = []

    def visit(directory: Path, depth: int) -> None:
        if depth > 2:
            return
        for entry in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if entry.name.casefold() in excluded:
                    continue
                visit(entry, depth + 1)
            elif entry.is_file() and entry.suffix.casefold() in _MEDIA_EXTENSIONS:
                results.append(entry.relative_to(project_dir))

    visit(project_dir, 0)
    return [str(path) for path in results]


def _lyric_key(text: str) -> str:
    """Fold case, accents, punctuation and spacing for lyric comparison."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _reuse_matches_lyrics(
    lyrics_text: str, cues: Any, title_hint: str
) -> bool:
    """Check whether a saved SRT still represents the current lyric text."""
    cleaned, _ = strip_credits_and_title(lyrics_text, title_hint=title_hint)
    expected = _lyric_key(cleaned)
    saved = _lyric_key(" ".join(str(cue.text) for cue in cues))
    return bool(expected) and expected == saved


def _stage_upload(file_storage: Any, directory: Path, extension: str) -> Path:
    """Save an upload under a temporary name without touching old files."""
    directory.mkdir(parents=True, exist_ok=True)
    staged = directory / f".upload-{uuid.uuid4().hex}{extension}"
    try:
        file_storage.save(str(staged))
    except Exception:
        try:
            staged.unlink()
        except OSError:
            pass
        raise
    return staged


def _commit_upload(
    staged: Path,
    stem: str,
    pattern: str,
    *,
    keep: tuple[Path | None, ...] = (),
) -> Path:
    """Move a staged upload into place, then remove the files it replaces."""
    directory = staged.parent
    target = directory / f"{stem}{staged.suffix}"
    if target.exists():
        target = directory / f"{stem}-{uuid.uuid4().hex[:8]}{staged.suffix}"
    staged.replace(target)

    keep_resolved: set[Path] = set()
    for path in keep:
        if path is None:
            continue
        try:
            keep_resolved.add(path.resolve())
        except OSError:
            pass
    for old in directory.glob(pattern):
        if not old.is_file() or old.name == target.name:
            continue
        if old.name.startswith(".upload-"):
            continue
        try:
            if old.resolve() in keep_resolved:
                continue
            old.unlink()
        except OSError:
            pass
    return target


def _alignment_looks_degenerate(cues: Any) -> bool:
    """Detect saved alignments with zero-length or out-of-order cues.

    Older versions could collapse the end of a song into a single timestamp
    when Whisper guessed the wrong language. Reusing that SRT would preserve
    the damaged lyrics, so the job should align again instead.
    """
    previous_start = -1.0
    for cue in cues:
        if cue.end - cue.start <= 0.05:
            return True
        if cue.start < previous_start - 0.05:
            return True
        previous_start = cue.start
    return False


def _merge_settings(defaults: dict[str, Any], incoming: Any) -> dict[str, Any]:
    # Deep-copy the defaults: normalize_settings edits the nested groups in
    # place, and sharing them would leak one project's settings into the
    # defaults used by the next job.
    if not isinstance(incoming, dict):
        return copy.deepcopy(defaults)
    merged = copy.deepcopy(defaults)
    for key, value in incoming.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_settings(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _require_int(settings: dict[str, Any], group: str, key: str, low: int, high: int, label: str) -> int:
    try:
        value = int(settings[group][key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} precisa ser um número inteiro.") from exc
    if not low <= value <= high:
        raise ValueError(f"{label} deve ficar entre {low} e {high}.")
    return value


def _require_float(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} precisa ser um número.") from exc


def _validated_color(value: Any, label: str, *, allow_alpha: bool = True) -> str:
    text = str(value or "").strip()
    try:
        ass_color(text)
    except ValueError as exc:
        raise ValueError(f"{label} inválida. Use #RRGGBB.") from exc
    if not allow_alpha and len(text.removeprefix("#")) != 6:
        raise ValueError(f"{label} inválida. Use #RRGGBB, sem transparência.")
    return text


def normalize_settings(raw: Any) -> dict[str, Any]:
    settings = _merge_settings(DEFAULT_SETTINGS, raw)
    align = settings["alignment"]
    if align["mode"] not in ("auto", "manual"):
        raise ValueError("O modo de alinhamento deve ser 'auto' ou 'manual'.")
    align["language"] = str(align["language"] or "auto").strip()[:30] or "auto"
    align["model"] = str(align["model"] or "small").strip()[:30] or "small"
    align["separate"] = bool(align["separate"])
    _require_int(settings, "alignment", "max_chars", 10, 100, "Máximo de caracteres")
    _require_int(settings, "alignment", "lead_ms", 0, 2000, "Início antecipado")
    _require_int(settings, "alignment", "end_pad_ms", 0, 5000, "Permanência final")
    _require_int(settings, "alignment", "min_duration_ms", 100, 5000, "Duração mínima")
    _require_int(settings, "alignment", "max_duration_ms", 500, 60000, "Duração máxima")
    _require_int(settings, "alignment", "min_gap_ms", 0, 1000, "Intervalo mínimo")
    align["offset_seconds"] = _require_float(align["offset_seconds"], "Deslocamento")
    align["playback_offset_seconds"] = max(
        -60.0,
        min(60.0, _require_float(align.get("playback_offset_seconds", 0.0), "Deslocamento do playback")),
    )
    align["range_start"] = max(0.0, _require_float(align["range_start"], "Início do trecho"))
    align["range_end"] = (
        None
        if align["range_end"] in (None, "", "null")
        else _require_float(align["range_end"], "Fim do trecho")
    )
    if align["range_end"] is not None and align["range_end"] <= align["range_start"]:
        raise ValueError("O fim do trecho deve ser maior que o início.")

    karaoke = settings["karaoke"]
    karaoke["enabled"] = bool(karaoke["enabled"])
    karaoke["normal_color"] = _validated_color(karaoke["normal_color"], "Cor normal")
    karaoke["active_color"] = _validated_color(karaoke["active_color"], "Cor ativa")
    karaoke["outline_color"] = _validated_color(
        karaoke.get("outline_color"), "Cor do contorno"
    )
    karaoke["back_color"] = _validated_color(karaoke.get("back_color"), "Cor da sombra")
    karaoke["font_name"] = str(karaoke["font_name"] or "Arial").strip()[:200] or "Arial"
    _require_int(settings, "karaoke", "font_size", 8, 300, "Tamanho da fonte")
    karaoke["bold"] = bool(karaoke["bold"])
    karaoke["outline"] = _require_float(karaoke["outline"], "Contorno")
    karaoke["shadow"] = _require_float(karaoke["shadow"], "Sombra")
    _require_int(settings, "karaoke", "alignment", 1, 9, "Alinhamento")
    _require_int(settings, "karaoke", "margin_h", 0, 1000, "Margem horizontal")
    _require_int(settings, "karaoke", "margin_v", 0, 1000, "Margem vertical")
    _require_int(settings, "karaoke", "lines_before", 0, 5, "Linhas antes")
    _require_int(settings, "karaoke", "lines_after", 0, 5, "Linhas depois")
    _require_int(settings, "karaoke", "lines_per_slide", 0, 5, "Linhas por tela")
    karaoke["stanza_mode"] = str(karaoke.get("stanza_mode") or "time").strip().lower()
    if karaoke["stanza_mode"] not in ("time", "stanzas"):
        raise ValueError("A interpretação de estrofes deve ser 'time' ou 'stanzas'.")
    karaoke["transition"] = str(karaoke.get("transition") or "none").strip().lower()
    if karaoke["transition"] not in ("none", "scroll", "fade", "up", "down"):
        raise ValueError("Transição inválida: use none, scroll, fade, up ou down.")
    _require_int(settings, "karaoke", "transition_ms", 100, 2000, "Duração da transição")

    background = settings["background"]
    if background["type"] not in ("color", "image"):
        raise ValueError("O tipo de fundo deve ser 'color' ou 'image'.")
    background["color"] = _validated_color(
        background["color"], "Cor do fundo", allow_alpha=False
    )
    background["fit"] = str(background.get("fit") or "contain").strip().lower()
    if background["fit"] not in ("contain", "cover"):
        raise ValueError("O encaixe da imagem deve ser 'contain' ou 'cover'.")
    background["image_filename"] = (
        Path(str(background["image_filename"])).name
        if background.get("image_filename")
        else None
    )

    output = settings["output"]
    _require_int(settings, "output", "width", 320, 7680, "Largura da saída")
    _require_int(settings, "output", "height", 240, 4320, "Altura da saída")
    if output["width"] % 2 or output["height"] % 2:
        raise ValueError(
            "Largura e altura da saída devem ser números pares (ex.: 1920×1080)."
        )

    burn = settings["burn"]
    if burn["preset"] not in _BURN_PRESETS:
        raise ValueError("Preset do FFmpeg inválido.")
    _require_int(settings, "burn", "crf", 0, 51, "CRF")

    settings["demo"] = bool(settings["demo"])
    return settings


def _read_debug_units(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    units = payload.get("global_units")
    return units if isinstance(units, list) else []


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
        ffmpeg_path = find_ffmpeg()
    except FileNotFoundError:
        ffmpeg_path = None
    if ffmpeg_path is not None:
        sibling = ffmpeg_path.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sibling.is_file():
            return sibling.resolve()
    return None


def probe_media(path: Path) -> dict[str, Any]:
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
                # Attached pictures are album covers, not real video. Encoding
                # them as H.264 fails because they are a single still frame.
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


def _write_project_meta(project_dir: Path, name: str, **fields: Any) -> None:
    meta_path = project_dir / "projeto.json"
    payload: dict[str, Any] = {
        "name": name,
        "created_at": _now(),
        "updated_at": _now(),
    }
    if meta_path.is_file():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            payload = {"name": name, "created_at": _now()}
    payload.update(fields)
    payload["updated_at"] = _now()
    meta_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "project_id": job["project_id"],
        "state": job["state"],
        "started_at": job["progress"][0]["t"] if job["progress"] else None,
        "now": time.time(),
        "progress": job["progress"][-300:],
        "cues": job["cues"],
        "outputs": job["outputs"],
        "media_info": job["media_info"],
        "playback_info": job.get("playback_info"),
        "error": job["error"],
    }


def _run_job(job_id: str) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        return
    job["state"] = "running"
    _log(job, "Iniciando o pipeline…")
    project_dir = Path(job["project_dir"])
    saida_dir = project_dir / "saida"
    saida_dir.mkdir(parents=True, exist_ok=True)
    work_dir = project_dir / "trabalho" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        settings = job["settings"]
        media_path = Path(job["media_path"])
        playback_path = (
            Path(job["playback_path"]) if job.get("playback_path") else None
        )
        lyrics_text = job.get("lyrics_text") or (project_dir / "letra.txt").read_text(
            encoding="utf-8-sig"
        )
        align = settings["alignment"]
        karaoke = settings["karaoke"]
        output = settings["output"]
        out_width = int(output["width"])
        out_height = int(output["height"])
        # Typography is configured at 1080p; scale it with the output height so
        # the same settings keep the same visual size at other resolutions.
        style_scale = out_height / 1080.0
        title = media_path.stem
        _log(job, f"Resolução de saída: {out_width}×{out_height}.")
        background_color = settings["background"]["color"]

        srt_path = saida_dir / "lyrics.srt"
        units_path = saida_dir / "alignment-units.json"
        debug_path = work_dir / "alignment-debug.json"
        units: list[dict[str, Any]] = []
        reuse_alignment = bool(job.get("reuse_alignment")) and srt_path.is_file()
        if reuse_alignment:
            cues = parse_srt(srt_path.read_text(encoding="utf-8-sig"))
            if not cues:
                raise ValueError(
                    "O alinhamento existente (lyrics.srt) está vazio ou inválido. "
                    "Desmarque 'Reaproveitar alinhamento' para alinhar de novo."
                )
            if not _reuse_matches_lyrics(lyrics_text, cues, job["project_name"]):
                reuse_alignment = False
                _log(
                    job,
                    "A letra mudou desde o último alinhamento; refazendo para "
                    "não usar tempos de outro texto.",
                )
            if reuse_alignment and _alignment_looks_degenerate(cues):
                reuse_alignment = False
                _log(
                    job,
                    "O alinhamento salvo tem tempos inválidos; refazendo o "
                    "alinhamento para corrigir as linhas.",
                )
        if reuse_alignment:
            offset = 0.0
            units = _read_debug_units(units_path)
            _log(
                job,
                f"Reaproveitando o alinhamento existente: {len(cues)} linhas "
                "(sem re-alinhar).",
            )
        elif settings["demo"]:
            if media_path.suffix.casefold() != ".wav":
                raise ValueError("No modo demonstração, use um arquivo WAV.")
            _log(job, "Modo demonstração: distribuindo as linhas igualmente…")
            cues = demo_to_srt(
                media_path, lyrics_text, srt_path, max_chars=align["max_chars"]
            )
            offset = 0.0
        else:
            missing = []
            for module in ("av", "soundfile", "stable_whisper"):
                try:
                    importlib.import_module(module)
                except ImportError:
                    missing.append(module)
            if missing:
                raise RuntimeError(
                    "Faltam dependências de IA ("
                    + ", ".join(missing)
                    + '). Rode: python -m pip install -e ".[align,separate]" '
                    "ou ative o modo demonstração com um arquivo WAV."
                )
            messages: list[str] = []
            cues = align_to_srt(
                media_path,
                lyrics_text,
                srt_path,
                max_chars=align["max_chars"],
                auto_segment=align["mode"] == "auto",
                language=align["language"],
                separate=align["separate"],
                partial_range=bool(align["range_start"] or align["range_end"]),
                model_name=align["model"],
                lead_ms=align["lead_ms"],
                offset_seconds=align["offset_seconds"],
                range_start_seconds=align["range_start"],
                range_end_seconds=align["range_end"],
                timeline_anchor=False,
                end_pad_ms=align["end_pad_ms"],
                min_duration_ms=align["min_duration_ms"],
                max_duration_ms=align["max_duration_ms"],
                min_gap_ms=align["min_gap_ms"],
                debug_json=debug_path,
                messages=messages,
                progress=lambda message: _log(job, message),
                work_dir=work_dir,
                title_hint=job["project_name"],
            )
            offset = float(align["offset_seconds"]) + max(0.0, float(align["range_start"]))
            for message in messages:
                _log(job, f"Nota: {message}")
            units = _read_debug_units(debug_path)
        if not reuse_alignment:
            units_path.write_text(
                json.dumps({"global_units": units}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if reuse_alignment:
            _log(job, f"{len(cues)} linhas reaproveitadas do alinhamento anterior.")
        else:
            _log(job, f"{len(cues)} linhas alinhadas.")

        base_style = AssStyle(
            font_name=karaoke["font_name"],
            font_size=max(1, round(karaoke["font_size"] * style_scale)),
            primary_color=karaoke["normal_color"],
            outline_color=karaoke["outline_color"],
            back_color=karaoke["back_color"],
            bold=karaoke["bold"],
            outline=karaoke["outline"] * style_scale,
            shadow=karaoke["shadow"] * style_scale,
            alignment=karaoke["alignment"],
            margin_l=max(0, round(karaoke["margin_h"] * style_scale)),
            margin_r=max(0, round(karaoke["margin_h"] * style_scale)),
            margin_v=max(0, round(karaoke["margin_v"] * style_scale)),
        )
        (saida_dir / "lyrics.ass").write_text(
            render_ass(
                cues,
                title=title,
                offset_seconds=offset,
                style=base_style,
                play_res_x=out_width,
                play_res_y=out_height,
            ),
            encoding="utf-8",
        )
        karaoke_ass: Path | None = None
        karaoke_style: KaraokeStyle | None = None
        stanza_boundaries = (
            stanza_boundaries_from_lyrics(lyrics_text, cues)
            if karaoke.get("stanza_mode") == "stanzas"
            else frozenset()
        )
        if karaoke["enabled"]:
            karaoke_style = KaraokeStyle(
                font_name=karaoke["font_name"],
                font_size=max(1, round(karaoke["font_size"] * style_scale)),
                normal_color=karaoke["normal_color"],
                active_color=karaoke["active_color"],
                outline_color=karaoke["outline_color"],
                back_color=karaoke["back_color"],
                bold=karaoke["bold"],
                outline=karaoke["outline"] * style_scale,
                shadow=karaoke["shadow"] * style_scale,
                alignment=karaoke["alignment"],
                margin_l=max(0, round(karaoke["margin_h"] * style_scale)),
                margin_r=max(0, round(karaoke["margin_h"] * style_scale)),
                margin_v=max(0, round(karaoke["margin_v"] * style_scale)),
                lines_before=karaoke["lines_before"],
                lines_after=karaoke["lines_after"],
                lines_per_slide=karaoke["lines_per_slide"],
                transition=karaoke["transition"],
                transition_ms=karaoke["transition_ms"],
                active_font_size=max(
                    0, round(karaoke.get("active_font_size", 0) * style_scale)
                ),
                scroll_pause_seconds=float(
                    karaoke.get("scroll_pause_seconds", 2.0)
                ),
                stanza_boundaries=stanza_boundaries,
            )
            karaoke_ass = saida_dir / "lyrics.karaoke.ass"
            karaoke_ass.write_text(
                render_karaoke_ass(
                    cues,
                    title=title,
                    offset_seconds=offset,
                    style=karaoke_style,
                    units=units,
                    play_res_x=out_width,
                    play_res_y=out_height,
                ),
                encoding="utf-8",
            )
        (saida_dir / "preview.vtt").write_text(
            render_webvtt(cues, offset_seconds=offset, title=title),
            encoding="utf-8",
        )
        job["cues"] = [
            {
                "index": index,
                "start": round(cue.start + offset, 3),
                "end": round(cue.end + offset, 3),
                "text": cue.text,
            }
            for index, cue in enumerate(cues, start=1)
        ]

        playback_cues: list[Cue] | None = None
        playback_ass: Path | None = None
        playback_karaoke_ass: Path | None = None
        sync_info: dict[str, Any] | None = None
        if playback_path is not None and (align["range_start"] or align["range_end"]):
            _log(
                job,
                "O playback foi ignorado porque o alinhamento usa um trecho "
                "In/Out do áudio cantado.",
            )
            playback_path = None
        if playback_path is not None:
            _log(job, "Sincronizando o playback com o hino cantado…")
            time_map = build_audio_time_map(
                media_path,
                playback_path,
                anchor_times=[cue.start for cue in cues],
            )
            playback_duration = (job.get("playback_info") or {}).get("duration")
            if playback_duration is None:
                playback_duration = probe_media(playback_path).get("duration")
            playback_cues = time_map.map_cues(
                cues, target_duration=playback_duration
            )
            manual_offset = float(align.get("offset_seconds") or 0.0) + float(
                align.get("playback_offset_seconds") or 0.0
            )
            if manual_offset:
                playback_cues = [
                    Cue(
                        text=cue.text,
                        start=max(0.0, cue.start + manual_offset),
                        end=max(0.2, cue.end + manual_offset),
                    )
                    for cue in playback_cues
                ]
            _log(
                job,
                "Playback sincronizado"
                f" ({time_map.method}): deslocamento médio "
                f"{time_map.offset_seconds:+.2f} s, confiança "
                f"{time_map.confidence:.0%}.",
            )
            if time_map.confidence < 0.35:
                _log(
                    job,
                    "Atenção: a sincronização do playback ficou incerta. "
                    "Confira o vídeo e, se preciso, ajuste o deslocamento.",
                )
            playback_ass = saida_dir / "lyrics_playback.ass"
            playback_ass.write_text(
                render_ass(
                    playback_cues,
                    title=f"{title} (playback)",
                    style=base_style,
                    play_res_x=out_width,
                    play_res_y=out_height,
                ),
                encoding="utf-8",
            )
            if karaoke_style is not None:
                playback_karaoke_ass = saida_dir / "lyrics_playback.karaoke.ass"
                playback_karaoke_ass.write_text(
                    render_karaoke_ass(
                        playback_cues,
                        title=f"{title} (playback)",
                        style=karaoke_style,
                        play_res_x=out_width,
                        play_res_y=out_height,
                    ),
                    encoding="utf-8",
                )
            (saida_dir / "lyrics_playback.srt").write_text(
                render_srt(playback_cues),
                encoding="utf-8-sig",
            )
            (saida_dir / "preview_playback.vtt").write_text(
                render_webvtt(
                    playback_cues, title=f"{title} (playback)"
                ),
                encoding="utf-8",
            )
            job["playback_cues"] = [
                {
                    "index": index,
                    "start": round(cue.start, 3),
                    "end": round(cue.end, 3),
                    "text": cue.text,
                }
                for index, cue in enumerate(playback_cues, start=1)
            ]
            sync_info = {
                "offset_seconds": round(time_map.offset_seconds, 3),
                "confidence": round(time_map.confidence, 3),
                "method": time_map.method,
                "manual_offset": round(manual_offset, 3),
            }

        burn = settings["burn"]
        chosen_ass = karaoke_ass or (saida_dir / "lyrics.ass")
        if job["media_info"].get("has_video"):
            burn_source = media_path
        else:
            background = settings["background"]
            image_path = None
            if background.get("image_filename"):
                image_path = project_dir / "fundo" / background["image_filename"]
            _log(job, "Criando o vídeo de fundo…")
            burn_source = make_background_video(
                media_path,
                work_dir / "background.mp4",
                color=background["color"],
                image=image_path,
                width=out_width,
                height=out_height,
                preset=burn["preset"],
                crf=int(burn["crf"]),
                fit=background.get("fit") or "contain",
            )

        video_out = saida_dir / "video_with_lyrics.mp4"
        burn_subtitles(
            burn_source,
            chosen_ass,
            video_out,
            preset=burn["preset"],
            crf=int(burn["crf"]),
            width=out_width,
            height=out_height,
            pad_color=background_color,
            progress=lambda message: _log(job, message),
        )
        video_cantado = saida_dir / "video_cantado.mp4"
        shutil.copyfile(video_out, video_cantado)

        video_playback: Path | None = None
        if playback_path is not None and playback_cues is not None:
            chosen_playback_ass = playback_karaoke_ass or playback_ass
            if chosen_playback_ass is None:
                raise RuntimeError("Não foi possível gerar as legendas do playback.")
            playback_info = job.get("playback_info") or probe_media(playback_path)
            if playback_info.get("has_video"):
                playback_source = playback_path
            else:
                background = settings["background"]
                image_path = None
                if background.get("image_filename"):
                    image_path = project_dir / "fundo" / background["image_filename"]
                _log(job, "Criando o vídeo de fundo do playback…")
                playback_source = make_background_video(
                    playback_path,
                    work_dir / "background_playback.mp4",
                    color=background["color"],
                    image=image_path,
                    width=out_width,
                    height=out_height,
                    preset=burn["preset"],
                    crf=int(burn["crf"]),
                    fit=background.get("fit") or "contain",
                )
            video_playback = saida_dir / "video_playback.mp4"
            _log(job, "Gravando o karaokê do playback…")
            burn_subtitles(
                playback_source,
                chosen_playback_ass,
                video_playback,
                preset=burn["preset"],
                crf=int(burn["crf"]),
                width=out_width,
                height=out_height,
                pad_color=background_color,
                progress=lambda message: _log(job, message),
            )

        payload = build_manifest(
            media_path=media_path,
            lyrics_path=project_dir / "letra.txt",
            title=title,
            style=base_style,
            cues=cues,
            alignment={
                "mode": align["mode"],
                "language": align["language"],
                "model": align["model"],
                "cue_count": len(cues),
                "effective_offset_seconds": offset,
                "playback_sync": sync_info,
            },
            outputs={
                "srt": "lyrics.srt",
                "ass": "lyrics.ass",
                "karaoke_ass": karaoke_ass.name if karaoke_ass else None,
                "video": "video_with_lyrics.mp4",
                "video_cantado": "video_cantado.mp4",
                "video_playback": video_playback.name if video_playback else None,
                "playback_ass": playback_ass.name if playback_ass else None,
                "playback_karaoke_ass": (
                    playback_karaoke_ass.name if playback_karaoke_ass else None
                ),
                "playback_srt": "lyrics_playback.srt" if playback_cues else None,
                "playback_vtt": "preview_playback.vtt" if playback_cues else None,
                "vtt": "preview.vtt",
            },
            burn={
                "requested": True,
                "completed": True,
                "preset": burn["preset"],
                "crf": int(burn["crf"]),
                "width": out_width,
                "height": out_height,
            },
        )
        write_manifest(saida_dir / "manifest.json", payload)
        job["outputs"] = {
            "video": "video_with_lyrics.mp4",
            "video_cantado": "video_cantado.mp4",
            "video_playback": video_playback.name if video_playback else None,
            "srt": "lyrics.srt",
            "ass": "lyrics.ass",
            "karaoke_ass": karaoke_ass.name if karaoke_ass else None,
            "playback_ass": playback_ass.name if playback_ass else None,
            "playback_karaoke_ass": (
                playback_karaoke_ass.name if playback_karaoke_ass else None
            ),
            "playback_srt": "lyrics_playback.srt" if playback_cues else None,
            "playback_vtt": "preview_playback.vtt" if playback_cues else None,
            "vtt": "preview.vtt",
            "manifest": "manifest.json",
        }
        try:
            media_rel = str(media_path.relative_to(project_dir))
        except ValueError:
            media_rel = media_path.name
        try:
            playback_rel = (
                str(playback_path.relative_to(project_dir))
                if playback_path is not None
                else None
            )
        except ValueError:
            playback_rel = playback_path.name if playback_path else None
        _write_project_meta(
            project_dir,
            job["project_name"],
            media=media_rel,
            playback=playback_rel,
            settings=settings,
            outputs=job["outputs"],
        )
        job["state"] = "done"
        _log(job, "Concluído.")
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        _log(job, f"Falhou: {exc}")
        _log(job, traceback.format_exc(limit=6))


def create_app(
    projects_root: str | Path | None = None,
) -> Any:
    try:
        from flask import Flask, abort, jsonify, request, send_file
    except ImportError as exc:
        raise RuntimeError(
            "Flask é necessário para o app web. Instale com "
            "pip install 'lyrics-aligner[web]'"
        ) from exc

    saved_root = _load_saved_projects_root() if projects_root is None else None
    resolved_root = Path(projects_root or saved_root) if (projects_root or saved_root) else music_dir()
    resolved_root = resolved_root.resolve()
    resolved_root.mkdir(parents=True, exist_ok=True)
    global _DEFAULT_PROJECTS_ROOT
    _DEFAULT_PROJECTS_ROOT = str(resolved_root)
    _ensure_worker()

    app = Flask(__name__)
    app.config["PROJECTS_ROOT"] = resolved_root

    @app.get("/")
    def index() -> str:
        return INDEX_HTML

    @app.get("/pipeline")
    def pipeline_page() -> str:
        return INDEX_HTML

    @app.get("/configuracoes")
    def settings_page() -> str:
        return INDEX_HTML

    @app.get("/api/info")
    def info() -> Any:
        return jsonify(
            {
                "projects_root": str(_current_projects_root()),
                "app": "vilm-webapp",
                "version": "1.0",
            }
        )

    @app.post("/api/projects_root")
    def set_projects_root_api() -> Any:
        """Muda a pasta de projetos em tempo de execução (sem reiniciar)."""
        data = request.get_json(silent=True) or {}
        new_root = (data.get("projects_root") or "").strip()
        try:
            root = set_projects_root(new_root or None)
        except (OSError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"projects_root": str(root)})

    @app.get("/api/profiles")
    def get_profiles() -> Any:
        """Lista os perfis de karaoke e qual esta ativo."""
        return jsonify(load_profiles())

    @app.post("/api/profiles/<name>/activate")
    def activate_profile(name: str) -> Any:
        """Ativa um perfil: aplica as configuracoes e a pasta de projetos."""
        payload = load_profiles()
        profile = payload.get("profiles", {}).get(name)
        if profile is None:
            abort(404)
        payload["active"] = name
        save_profiles(payload)
        projects_root = profile.get("projects_root")
        if projects_root:
            try:
                set_projects_root(str(projects_root))
            except (OSError, ValueError):
                pass
        return jsonify({"active": name, "profile": profile,
                        "projects_root": str(_current_projects_root())})

    @app.post("/api/profiles/<name>")
    def save_profile_settings(name: str) -> Any:
        """Salva as configuracoes atuais da tela dentro de um perfil."""
        payload = load_profiles()
        if name not in payload.get("profiles", {}):
            abort(404)
        data = request.get_json(silent=True) or {}
        try:
            settings = normalize_settings(data.get("settings") or {})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        payload["profiles"][name]["settings"] = settings
        save_profiles(payload)
        return jsonify({"name": name, "settings": settings})

    @app.get("/api/projects")
    def list_projects() -> Any:
        items: list[dict[str, Any]] = []
        for entry in sorted(_current_projects_root().iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            has_media = bool(_project_media_files(entry))
            has_saida = (entry / "saida").is_dir()
            meta_path = entry / "projeto.json"
            meta: dict[str, Any] = {}
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    meta = {}
            if not (meta_path.is_file() or has_media or has_saida):
                continue
            background: dict[str, Any] = {}
            settings_path = entry / "configuracoes.json"
            if settings_path.is_file():
                try:
                    saved_settings = json.loads(
                        settings_path.read_text(encoding="utf-8-sig")
                    )
                    background = saved_settings.get("background") or {}
                except (OSError, ValueError):
                    background = {}
            items.append(
                {
                    "id": entry.name,
                    "name": meta.get("name", entry.name),
                    "updated_at": meta.get("updated_at"),
                    "has_video": (entry / "saida" / "video_with_lyrics.mp4").is_file()
                    or (entry / "saida" / "video_cantado.mp4").is_file(),
                    "media": meta.get("media"),
                    "playback": meta.get("playback"),
                    "background": {
                        "type": background.get("type"),
                        "image_filename": background.get("image_filename"),
                    },
                }
            )
        return jsonify({"projects": items})

    def _project_dir_or_404(project_id: str) -> Path:
        project_dir = (_current_projects_root() / project_id).resolve()
        try:
            project_dir.relative_to(_current_projects_root())
        except ValueError:
            abort(404)
        if not project_dir.is_dir():
            abort(404)
        return project_dir

    @app.get("/api/projects/<project_id>/load")
    def load_project(project_id: str) -> Any:
        project_dir = _project_dir_or_404(project_id)
        lyrics = ""
        lyrics_path = project_dir / "letra.txt"
        if lyrics_path.is_file():
            lyrics = lyrics_path.read_text(encoding="utf-8-sig")
        settings: dict[str, Any] = {}
        settings_path = project_dir / "configuracoes.json"
        if settings_path.is_file():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                settings = {}
        meta: dict[str, Any] = {}
        meta_path = project_dir / "projeto.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                meta = {}
        saida_dir = project_dir / "saida"
        media_files = _project_media_files(project_dir)
        media = meta.get("media")
        playback = meta.get("playback")
        if media and (project_dir / media).is_file():
            normalized = str(Path(media))
            if normalized not in media_files:
                media_files.insert(0, normalized)
        if playback and (project_dir / playback).is_file():
            normalized = str(Path(playback))
            if normalized not in media_files:
                media_files.append(normalized)
        return jsonify(
            {
                "id": project_id,
                "name": meta.get("name", project_id),
                "lyrics": lyrics,
                "settings": settings,
                "media": media,
                "playback": playback,
                "media_files": media_files,
                "has_alignment": (saida_dir / "lyrics.srt").is_file(),
                "outputs": {
                    "video": (saida_dir / "video_with_lyrics.mp4").is_file(),
                    "video_cantado": (saida_dir / "video_cantado.mp4").is_file(),
                    "video_playback": (saida_dir / "video_playback.mp4").is_file(),
                    "srt": (saida_dir / "lyrics.srt").is_file(),
                    "ass": (saida_dir / "lyrics.ass").is_file(),
                    "karaoke_ass": (saida_dir / "lyrics.karaoke.ass").is_file(),
                },
            }
        )

    @app.post("/api/projects/<project_id>/reset")
    def reset_project(project_id: str) -> Any:
        """Apaga todos os arquivos gerados de um projeto para forçar re-alinhamento completo."""
        project_dir = _project_dir_or_404(project_id)
        saida_dir = project_dir / "saida"
        trabalho_dir = project_dir / "trabalho"
        removed: list[str] = []

        # Remove a pasta de saída (vídeos, SRT, ASS, manifest, etc.)
        if saida_dir.is_dir():
            for child in saida_dir.iterdir():
                try:
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                        removed.append(child.name)
                    elif child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                        removed.append(child.name + "/")
                except OSError:
                    pass

        # Remove arquivos de trabalho temporários (alignment-units.json, debug)
        if trabalho_dir.is_dir():
            for child in trabalho_dir.iterdir():
                try:
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                        removed.append("trabalho/" + child.name)
                    elif child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                        removed.append("trabalho/" + child.name + "/")
                except OSError:
                    pass

        return jsonify({"message": f"Projeto '{project_id}' resetado. {len(removed)} arquivo(s) removido(s).", "removed": removed})

    @app.get("/api/projects/<project_id>/cues")
    def get_cues(project_id: str) -> Any:
        """Retorna as legendas atuais do projeto (manual ou SRT) para edição."""
        project_dir = _project_dir_or_404(project_id)
        saida_dir = project_dir / "saida"

        manual_path = saida_dir / "lyrics_manual.srt"
        srt_path = saida_dir / "lyrics.srt"

        source_path = manual_path if manual_path.is_file() else srt_path
        if not source_path.is_file():
            return jsonify({"cues": [], "source": None})

        cues = parse_srt(source_path.read_text(encoding="utf-8-sig"))
        return jsonify(
            {
                "source": "manual" if manual_path.is_file() else "srt",
                "cues": [
                    {
                        "index": index,
                        "start": round(cue.start, 3),
                        "end": round(cue.end, 3),
                        "text": cue.text,
                    }
                    for index, cue in enumerate(cues, start=1)
                ],
            }
        )

    @app.post("/api/projects/<project_id>/edit-cues")
    def edit_cues(project_id: str) -> Any:
        """Salva legendas editadas manualmente (timestamps + texto) para re-renderização."""
        project_dir = _project_dir_or_404(project_id)
        saida_dir = project_dir / "saida"
        saida_dir.mkdir(parents=True, exist_ok=True)

        data = request.get_json(silent=True) or {}
        cues = data.get("cues")
        if not isinstance(cues, list):
            return jsonify({"error": "Lista de cues é obrigatória."}), 400

        # Valida e normaliza cada cue
        normalized: list[Cue] = []
        for index, item in enumerate(cues):
            try:
                start = float(item["start"])
                end = float(item["end"])
                text = str(item.get("text", "")).strip()
            except (KeyError, TypeError, ValueError):
                return jsonify({"error": f"Cue {index + 1} inválido."}), 400
            if end <= start:
                return jsonify({"error": f"Cue {index + 1}: fim deve ser maior que início."}), 400
            normalized.append(Cue(text=text, start=start, end=end))

        # Ordena por início e remove sobreposições
        normalized.sort(key=lambda c: c.start)
        for i in range(len(normalized) - 1):
            if normalized[i].end > normalized[i + 1].start - 0.01:
                normalized[i] = Cue(
                    text=normalized[i].text,
                    start=normalized[i].start,
                    end=max(normalized[i].start + 0.01, normalized[i + 1].start - 0.01),
                )

        # Salva como SRT manual
        manual_path = saida_dir / "lyrics_manual.srt"
        manual_path.write_text(
            render_srt(normalized), encoding="utf-8-sig"
        )

        return jsonify(
            {
                "message": f"{len(normalized)} legendas salvas em letras manuais.",
                "cues": [
                    {
                        "index": i + 1,
                        "start": round(c.start, 3),
                        "end": round(c.end, 3),
                        "text": c.text,
                    }
                    for i, c in enumerate(normalized)
                ],
            }
        )

    @app.post("/api/projects/<project_id>/re-render")
    def re_render(project_id: str) -> Any:
        """Re-renderiza o vídeo usando as legendas manuais (ou SRT se não houver manual)."""
        project_dir = _project_dir_or_404(project_id)
        saida_dir = project_dir / "saida"
        work_dir = project_dir / "trabalho"
        work_dir.mkdir(parents=True, exist_ok=True)

        meta_path = project_dir / "projeto.json"
        if not meta_path.is_file():
            return jsonify({"error": "Projeto não encontrado."}), 404
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))

        media_name = meta.get("media")
        if not media_name:
            return jsonify({"error": "Projeto sem mídia definida."}), 400
        media_path = project_dir / media_name
        if not media_path.is_file():
            return jsonify({"error": f"Mídia não encontrada: {media_name}"}), 404

        settings_path = project_dir / "configuracoes.json"
        if not settings_path.is_file():
            return jsonify({"error": "Configurações não encontradas."}), 404
        settings = normalize_settings(json.loads(settings_path.read_text(encoding="utf-8-sig")))

        # Escolhe as legendas: manual tem precedência sobre SRT
        manual_path = saida_dir / "lyrics_manual.srt"
        srt_path = saida_dir / "lyrics.srt"
        ass_source = manual_path if manual_path.is_file() else srt_path
        if not ass_source.is_file():
            return jsonify({"error": "Não há legendas para renderizar."}), 404

        align = settings["alignment"]
        karaoke = settings["karaoke"]
        output = settings["output"]
        burn = settings["burn"]
        out_width = int(output["width"])
        out_height = int(output["height"])
        style_scale = out_height / 1080.0
        background_color = settings["background"]["color"]

        # Lê as cues do SRT fonte
        cues = parse_srt(ass_source.read_text(encoding="utf-8-sig"))
        if not cues:
            return jsonify({"error": "Nenhuma legenda encontrada."}), 404

        # Gera o ASS
        base_style = AssStyle(
            font_name=karaoke["font_name"],
            font_size=max(1, round(karaoke["font_size"] * style_scale)),
            primary_color=karaoke["normal_color"],
            outline_color=karaoke["outline_color"],
            back_color=karaoke["back_color"],
            bold=karaoke["bold"],
            outline=karaoke["outline"] * style_scale,
            shadow=karaoke["shadow"] * style_scale,
            alignment=karaoke["alignment"],
            margin_l=max(0, round(karaoke["margin_h"] * style_scale)),
            margin_r=max(0, round(karaoke["margin_h"] * style_scale)),
            margin_v=max(0, round(karaoke["margin_v"] * style_scale)),
        )
        ass_path = saida_dir / "lyrics_editado.ass"
        ass_path.write_text(
            render_ass(
                cues,
                title=media_path.stem,
                offset_seconds=0.0,
                style=base_style,
                play_res_x=out_width,
                play_res_y=out_height,
            ),
            encoding="utf-8",
        )

        # Cria vídeo de fundo se necessário. probe_media ignora capas de álbum,
        # que não podem ser recodificadas como vídeo.
        if probe_media(media_path).get("has_video"):
            burn_source = media_path
        else:
            background = settings["background"]
            image_path = None
            if background.get("image_filename"):
                image_path = project_dir / "fundo" / background["image_filename"]
            burn_source = make_background_video(
                media_path,
                work_dir / "background.mp4",
                color=background["color"],
                image=image_path,
                width=out_width,
                height=out_height,
                preset=burn["preset"],
                crf=int(burn["crf"]),
                fit=background.get("fit") or "contain",
            )

        # Queima legendas no vídeo
        video_out = saida_dir / "video_cantado.mp4"
        burn_subtitles(
            burn_source,
            ass_path,
            video_out,
            preset=burn["preset"],
            crf=int(burn["crf"]),
            width=out_width,
            height=out_height,
            pad_color=background_color,
        )

        # Também gera o karaoke.ass se habilitado
        if karaoke.get("enabled"):
            lyrics_path = project_dir / "letra.txt"
            lyrics_source = (
                lyrics_path.read_text(encoding="utf-8-sig")
                if lyrics_path.is_file() else ""
            )
            re_render_boundaries = (
                stanza_boundaries_from_lyrics(lyrics_source, cues)
                if karaoke.get("stanza_mode") == "stanzas"
                else frozenset()
            )
            karaoke_style = KaraokeStyle(
                font_name=karaoke["font_name"],
                font_size=max(1, round(karaoke["font_size"] * style_scale)),
                normal_color=karaoke["normal_color"],
                active_color=karaoke["active_color"],
                outline_color=karaoke["outline_color"],
                back_color=karaoke["back_color"],
                bold=karaoke["bold"],
                outline=karaoke["outline"] * style_scale,
                shadow=karaoke["shadow"] * style_scale,
                alignment=karaoke["alignment"],
                margin_l=max(0, round(karaoke["margin_h"] * style_scale)),
                margin_r=max(0, round(karaoke["margin_h"] * style_scale)),
                margin_v=max(0, round(karaoke["margin_v"] * style_scale)),
                lines_before=karaoke.get("lines_before", 0),
                lines_after=karaoke.get("lines_after", 0),
                lines_per_slide=karaoke.get("lines_per_slide", 3),
                transition=karaoke.get("transition", "none"),
                transition_ms=karaoke.get("transition_ms", 400),
                active_font_size=max(
                    0, round(karaoke.get("active_font_size", 0) * style_scale)
                ),
                scroll_pause_seconds=float(
                    karaoke.get("scroll_pause_seconds", 2.0)
                ),
                stanza_boundaries=re_render_boundaries,
            )
            karaoke_ass_path = saida_dir / "lyrics_editado.karaoke.ass"
            karaoke_ass_path.write_text(
                render_karaoke_ass(
                    cues,
                    title=media_path.stem,
                    offset_seconds=0.0,
                    style=karaoke_style,
                    play_res_x=out_width,
                    play_res_y=out_height,
                ),
                encoding="utf-8",
            )

        return jsonify(
            {
                "message": "Vídeo re-renderizado com legendas editadas.",
                "video": "video_cantado.mp4",
                "ass": "lyrics_editado.ass",
                "cues_count": len(cues),
            }
        )

    @app.post("/api/projects/<project_id>/preview")
    def preview_video(project_id: str) -> Any:
        """Gera uma prévia rápida em baixa resolução com as legendas atuais."""
        project_dir = _project_dir_or_404(project_id)
        saida_dir = project_dir / "saida"
        work_dir = project_dir / "trabalho"
        work_dir.mkdir(parents=True, exist_ok=True)
        saida_dir.mkdir(parents=True, exist_ok=True)

        meta_path = project_dir / "projeto.json"
        if not meta_path.is_file():
            return jsonify({"error": "Projeto não encontrado."}), 404
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        media_name = meta.get("media")
        if not media_name:
            return jsonify({"error": "Projeto sem mídia definida."}), 400
        media_path = project_dir / media_name
        if not media_path.is_file():
            return jsonify({"error": f"Mídia não encontrada: {media_name}"}), 404

        settings_path = project_dir / "configuracoes.json"
        if not settings_path.is_file():
            return jsonify({"error": "Configurações não encontradas."}), 404
        settings = normalize_settings(
            json.loads(settings_path.read_text(encoding="utf-8-sig"))
        )

        # Cues enviados pela interface (edições ainda não salvas) têm prioridade.
        data = request.get_json(silent=True) or {}
        payload_cues = data.get("cues")
        if isinstance(payload_cues, list) and payload_cues:
            normalized: list[Cue] = []
            for index, item in enumerate(payload_cues):
                try:
                    start = float(item["start"])
                    end = float(item["end"])
                    text = str(item.get("text", "")).strip()
                except (KeyError, TypeError, ValueError):
                    return jsonify({"error": f"Cue {index + 1} inválido."}), 400
                if end <= start:
                    return jsonify({"error": f"Cue {index + 1}: fim deve ser maior que início."}), 400
                normalized.append(Cue(text=text, start=start, end=end))
            normalized.sort(key=lambda cue: cue.start)
            cues = normalized
            ass_source = saida_dir / "lyrics_preview.srt"
            ass_source.write_text(render_srt(cues), encoding="utf-8-sig")
        else:
            manual_path = saida_dir / "lyrics_manual.srt"
            srt_path = saida_dir / "lyrics.srt"
            ass_source = manual_path if manual_path.is_file() else srt_path
            if not ass_source.is_file():
                return jsonify({"error": "Não há legendas para a prévia."}), 404
            cues = parse_srt(ass_source.read_text(encoding="utf-8-sig"))
            if not cues:
                return jsonify({"error": "Nenhuma legenda encontrada."}), 404

        output = settings["output"]
        out_width = int(output["width"])
        out_height = int(output["height"])
        # Prévia com o mesmo formato da saída, limitada a 640 px no maior lado.
        if out_width >= out_height:
            preview_width = 640
            preview_height = max(2, round(640 * out_height / out_width / 2) * 2)
        else:
            preview_height = 640
            preview_width = max(2, round(640 * out_width / out_height / 2) * 2)
        style_scale = preview_height / 1080.0
        background_color = settings["background"]["color"]
        karaoke = settings["karaoke"]

        base_style = AssStyle(
            font_name=karaoke["font_name"],
            font_size=max(1, round(karaoke["font_size"] * style_scale)),
            primary_color=karaoke["normal_color"],
            outline_color=karaoke["outline_color"],
            back_color=karaoke["back_color"],
            bold=karaoke["bold"],
            outline=karaoke["outline"] * style_scale,
            shadow=karaoke["shadow"] * style_scale,
            alignment=karaoke["alignment"],
            margin_l=max(0, round(karaoke["margin_h"] * style_scale)),
            margin_r=max(0, round(karaoke["margin_h"] * style_scale)),
            margin_v=max(0, round(karaoke["margin_v"] * style_scale)),
        )
        ass_path = saida_dir / "lyrics_preview.ass"
        ass_path.write_text(
            render_ass(
                cues,
                title=media_path.stem,
                offset_seconds=0.0,
                style=base_style,
                play_res_x=preview_width,
                play_res_y=preview_height,
            ),
            encoding="utf-8",
        )

        if probe_media(media_path).get("has_video"):
            burn_source = media_path
        else:
            background = settings["background"]
            image_path = None
            if background.get("image_filename"):
                image_path = project_dir / "fundo" / background["image_filename"]
            burn_source = make_background_video(
                media_path,
                work_dir / "background_preview.mp4",
                color=background["color"],
                image=image_path,
                width=preview_width,
                height=preview_height,
                preset="ultrafast",
                crf=32,
                fit=background.get("fit") or "contain",
            )

        preview_out = saida_dir / "preview_karaoke.mp4"
        burn_subtitles(
            burn_source,
            ass_path,
            preview_out,
            preset="ultrafast",
            crf=32,
            width=preview_width,
            height=preview_height,
            pad_color=background_color,
        )
        return jsonify(
            {
                "message": "Prévia gerada.",
                "video": "preview_karaoke.mp4",
                "width": preview_width,
                "height": preview_height,
                "cues_count": len(cues),
            }
        )

    @app.post("/api/jobs")
    def create_job() -> Any:
        lyrics_text = (request.form.get("lyrics_text") or "").strip()
        lyrics_file = request.files.get("lyrics_file")
        if lyrics_file is not None and lyrics_file.filename:
            lyrics_text = lyrics_file.read().decode("utf-8-sig").strip()
        if not lyrics_text:
            return jsonify({"error": "Cole ou carregue a letra do hino."}), 400

        try:
            settings = normalize_settings(json.loads(request.form.get("settings") or "{}"))
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400

        media = request.files.get("media")
        media_source = (request.form.get("media_source") or "").strip()
        reuse_alignment = (request.form.get("reuse_alignment") or "").strip().lower() in (
            "1",
            "true",
            "on",
            "yes",
        )
        project_input = (request.form.get("project") or "").strip()
        if not project_input and media is not None and media.filename:
            project_input = Path(media.filename).stem.replace("_", " ").strip()
        project_id = sanitize_project_name(project_input or "Projeto sem nome")
        project_dir = _current_projects_root() / project_id
        saida_dir = project_dir / "saida"
        (project_dir / "fundo").mkdir(parents=True, exist_ok=True)
        saida_dir.mkdir(parents=True, exist_ok=True)

        staged_files: list[Path] = []

        def fail(message: str) -> Any:
            for staged in staged_files:
                try:
                    staged.unlink()
                except OSError:
                    pass
            return jsonify({"error": message}), 400

        media_path: Path
        staged_media: Path | None = None
        if media_source.startswith("existing:"):
            relative = media_source[len("existing:") :].strip()
            candidate = _resolve_project_file(project_dir, relative)
            if candidate is None or not candidate.is_file():
                return fail("Arquivo de mídia do projeto não encontrado.")
            if candidate.suffix.casefold() not in _MEDIA_EXTENSIONS:
                return fail("O arquivo escolhido não é um vídeo ou áudio.")
            media_path = candidate
        elif media is not None and media.filename:
            extension = Path(media.filename).suffix.casefold()
            if extension not in _MEDIA_EXTENSIONS:
                return fail(
                    "O arquivo de mídia não é um vídeo ou áudio suportado."
                )
            midia_dir = project_dir / "midia"
            staged_media = _stage_upload(media, midia_dir, extension)
            staged_files.append(staged_media)
            media_path = staged_media
        else:
            return fail(
                "Escolha um arquivo de mídia: um arquivo do projeto "
                "ou envie um novo."
            )

        playback = request.files.get("playback")
        playback_source = (request.form.get("playback_source") or "").strip()
        playback_uploaded = playback is not None and bool(playback.filename)
        playback_path: Path | None = None
        staged_playback: Path | None = None
        if playback_source.startswith("existing:"):
            relative = playback_source[len("existing:") :].strip()
            candidate = _resolve_project_file(project_dir, relative)
            if candidate is None or not candidate.is_file():
                return fail("Arquivo de playback do projeto não encontrado.")
            if candidate.suffix.casefold() not in _MEDIA_EXTENSIONS:
                return fail("O playback escolhido não é um vídeo ou áudio.")
            playback_path = candidate
        elif playback_uploaded:
            extension = Path(playback.filename).suffix.casefold()
            if extension not in _MEDIA_EXTENSIONS:
                return fail(
                    "O arquivo de playback não é um vídeo ou áudio suportado."
                )
            midia_dir = project_dir / "midia"
            staged_playback = _stage_upload(playback, midia_dir, extension)
            staged_files.append(staged_playback)
            playback_path = staged_playback
        elif playback_source == "remove":
            playback_path = None
        elif not playback_source:
            meta_path = project_dir / "projeto.json"
            previous_playback = ""
            if meta_path.is_file():
                try:
                    previous_playback = str(
                        json.loads(meta_path.read_text(encoding="utf-8-sig")).get(
                            "playback"
                        )
                        or ""
                    )
                except (OSError, ValueError):
                    previous_playback = ""
            if previous_playback:
                candidate = _resolve_project_file(project_dir, previous_playback)
                if candidate is not None and candidate.is_file():
                    playback_path = candidate
        else:
            return fail("Seleção de playback inválida.")
        if playback_path is not None and playback_path.resolve() == media_path.resolve():
            return fail(
                "Escolha arquivos diferentes para o cantado e o playback."
            )

        background_image = request.files.get("background_image")
        background = settings["background"]
        if not background.get("image_filename"):
            previous_path = project_dir / "configuracoes.json"
            if previous_path.is_file():
                try:
                    previous = json.loads(previous_path.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    previous = {}
                previous_image = (previous.get("background") or {}).get(
                    "image_filename"
                )
                if previous_image and (previous.get("background") or {}).get("type") == "image":
                    background["image_filename"] = previous_image
                    background["type"] = "image"
        staged_image: Path | None = None
        if background_image is not None and background_image.filename:
            image_ext = Path(background_image.filename).suffix.casefold()
            if image_ext not in _IMAGE_EXTENSIONS:
                return fail(
                    "Formato de imagem não suportado. Use PNG, JPG, WEBP, BMP ou GIF."
                )
            fundo_dir = project_dir / "fundo"
            staged_image = _stage_upload(background_image, fundo_dir, image_ext)
            staged_files.append(staged_image)
            background["image_filename"] = staged_image.name
            background["type"] = "image"
        if background["type"] == "image":
            image_name = Path(str(background.get("image_filename") or "")).name
            image_path = (
                _resolve_project_file(project_dir, f"fundo/{image_name}")
                if image_name
                else None
            )
            if image_path is None or not image_path.is_file():
                return fail(
                    "A imagem de fundo selecionada não foi encontrada. "
                    "Escolha a imagem novamente ou use cor sólida."
                )
            if staged_image is None:
                background["image_filename"] = image_path.name

        # Only now replace the old files. Until this point, a validation error
        # leaves the previously saved media, playback and background intact.
        if staged_media is not None:
            media_path = _commit_upload(
                staged_media, "cantado", "cantado.*", keep=(playback_path,)
            )
        if staged_playback is not None:
            playback_path = _commit_upload(
                staged_playback, "playback", "playback.*", keep=(media_path,)
            )
        if staged_image is not None:
            image_path = _commit_upload(staged_image, "fundo", "fundo.*")
            background["image_filename"] = image_path.name

        (project_dir / "letra.txt").write_text(lyrics_text, encoding="utf-8-sig")
        settings_path = project_dir / "configuracoes.json"
        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        job_id = uuid.uuid4().hex[:12]
        job: dict[str, Any] = {
            "id": job_id,
            "project_id": project_id,
            "project_name": project_input.strip() or project_id,
            "project_dir": str(project_dir),
            "media_path": str(media_path),
            "playback_path": str(playback_path) if playback_path else None,
            "lyrics_text": lyrics_text,
            "settings": settings,
            "reuse_alignment": reuse_alignment,
            "state": "queued",
            "progress": [],
            "cues": [],
            "outputs": {},
            "media_info": probe_media(media_path),
            "playback_info": probe_media(playback_path) if playback_path else None,
            "error": None,
            "created_at": _now(),
        }
        with _JOBS_LOCK:
            _JOBS[job_id] = job
        _QUEUE.put(job_id)
        return jsonify({"job_id": job_id, "job": _public_job(job)}), 202

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id: str) -> Any:
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
        if job is None:
            return jsonify({"error": "Trabalho não encontrado."}), 404
        return jsonify(_public_job(job))

    @app.get("/api/projects/<project_id>/files/<name>")
    def project_file(project_id: str, name: str) -> Any:
        project_dir = _project_dir_or_404(project_id)
        if name not in _SAFE_OUTPUT_NAMES:
            abort(404)
        path = project_dir / "saida" / name
        if not path.is_file():
            abort(404)
        download = name.endswith((".srt", ".ass", ".json"))
        return send_file(path, as_attachment=download, conditional=True)

    @app.get("/api/projects/<project_id>/media")
    def project_media(project_id: str) -> Any:
        midia_dir = _project_dir_or_404(project_id) / "midia"
        if not midia_dir.is_dir():
            abort(404)
        candidates = sorted(midia_dir.glob("media.*"))
        if not candidates:
            abort(404)
        return send_file(candidates[0], conditional=True)

    @app.get("/api/projects/<project_id>/background")
    def project_background(project_id: str) -> Any:
        project_dir = _project_dir_or_404(project_id)
        name = Path((request.args.get("name") or "").strip()).name
        if name:
            path = _resolve_project_file(project_dir, f"fundo/{name}")
        else:
            candidates = sorted((project_dir / "fundo").glob("fundo.*"))
            path = candidates[-1] if candidates else None
        if path is None or not path.is_file():
            abort(404)
        return send_file(path, conditional=True)

    @app.get("/api/projects/<project_id>/media-files")
    def project_media_files(project_id: str) -> Any:
        project_dir = _project_dir_or_404(project_id)
        return jsonify({"files": _project_media_files(project_dir)})

    @app.get("/api/projects/<project_id>/media-source")
    def project_media_source(project_id: str) -> Any:
        project_dir = _project_dir_or_404(project_id)
        relative = request.args.get("path", "")
        path = _resolve_project_file(project_dir, relative)
        if path is None or not path.is_file():
            abort(404)
        if path.suffix.casefold() not in _MEDIA_EXTENSIONS:
            abort(404)
        return send_file(path, conditional=True)

    @app.get("/api/projects/<project_id>/open")
    def open_project(project_id: str) -> Any:
        project_dir = _project_dir_or_404(project_id)
        try:
            if os.name == "nt":
                os.startfile(str(project_dir))  # type: ignore[attr-defined]
            else:
                webbrowser.open(project_dir.as_uri())
        except OSError as exc:
            return jsonify({"error": f"Não foi possível abrir a pasta: {exc}"}), 500
        return jsonify({"ok": True})

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vilm-webapp")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--projects-root", type=Path, default=None)
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="open the web page automatically after the server starts",
    )
    args = parser.parse_args(argv)
    app = create_app(projects_root=args.projects_root)
    url = f"http://{args.host}:{args.port}/pipeline"
    print(f"Vilm Lyrics Aligner (web): {url}")
    print(f"Pasta de projetos: {app.config['PROJECTS_ROOT']}")
    if args.open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
    return 0


INDEX_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vilm Lyrics Aligner — Pipeline</title>
<style>
:root{
  --bg:#0e1116;--panel:#171c24;--panel2:#1e2530;--border:#2a3342;--text:#e8edf4;
  --muted:#9aa7b8;--accent:#ffd54a;--accent2:#2f81f7;--ok:#3fb950;--bad:#f85149;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 "Segoe UI",system-ui,sans-serif}
header{display:flex;align-items:center;gap:14px;padding:14px 22px;background:var(--panel);border-bottom:1px solid var(--border)}
header h1{font-size:18px;margin:0}
header .sub{color:var(--muted);font-size:13px}
.tabs{display:flex;gap:6px;padding:0 22px;background:var(--panel);border-bottom:1px solid var(--border)}
.tabs button{background:none;border:none;color:var(--muted);padding:12px 18px;font-size:15px;cursor:pointer;border-bottom:2px solid transparent}
.tabs button.active{color:var(--text);border-bottom-color:var(--accent)}
main{max-width:1060px;margin:0 auto;padding:22px}
section{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px;margin-bottom:18px}
section h2{font-size:15px;margin:0 0 12px;color:var(--accent)}
label{display:block;color:var(--muted);font-size:13px;margin:10px 0 4px}
input[type=text],input[type=number],select,textarea{width:100%;background:var(--panel2);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:8px 10px;font-size:14px}
input[type=color]{width:52px;height:34px;border:1px solid var(--border);border-radius:6px;background:var(--panel2);padding:2px}
textarea{min-height:150px;resize:vertical;font-family:Consolas,monospace}
button.primary{background:var(--accent);color:#1a1400;border:none;border-radius:8px;padding:11px 22px;font-size:15px;font-weight:600;cursor:pointer}
button.primary:disabled{opacity:.45;cursor:not-allowed}
button.secondary{background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer}
button.link{background:none;border:none;color:var(--accent2);cursor:pointer;font-size:13px;padding:2px}
.row{display:flex;gap:14px;flex-wrap:wrap}
.row>div{flex:1;min-width:220px}
.drop{border:2px dashed var(--border);border-radius:10px;padding:26px;text-align:center;color:var(--muted);cursor:pointer;background:var(--panel2)}
.drop.over{border-color:var(--accent);color:var(--accent)}
.hidden{display:none}
video,audio{max-width:100%;border-radius:8px;background:#000}
.bgthumb{display:block;max-width:100%;max-height:150px;border:1px solid var(--border);border-radius:8px;object-fit:contain;background:var(--panel2)}
.bgthumb.hidden{display:none}
.progress{height:8px;background:var(--panel2);border-radius:6px;overflow:hidden;margin:12px 0}
.progress .bar{height:100%;width:0;background:var(--accent2);transition:width .4s}
.progress.running .bar{width:100%;animation:pulse 1.4s infinite}
@keyframes pulse{0%{opacity:.4}50%{opacity:1}100%{opacity:.4}}
#stateLabel{font-weight:600}
#stateLabel.ok{color:var(--ok)} #stateLabel.bad{color:var(--bad)}
#logBox{background:#0a0d12;border:1px solid var(--border);border-radius:8px;padding:10px;font:12px/1.5 Consolas,monospace;max-height:180px;overflow:auto;white-space:pre-wrap;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
#editCueBody tr.playing{background:#22314a}
#editCueBody tr.selected{outline:1px solid var(--accent)}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:0 18px}
.inline{display:flex;align-items:center;gap:10px}
.inline label{margin:0;color:var(--text)}
.previewwrap{margin-top:10px}
.previewframe{position:relative;width:100%;aspect-ratio:16/9;border:1px solid var(--border);border-radius:10px;overflow:hidden;display:flex;flex-direction:column;background:#0b1d3a;padding:0}
.previewframe .block{width:100%}
.previewframe .pline{white-space:normal;overflow-wrap:break-word;margin:0}
.previewbadge{position:absolute;top:8px;left:10px;font:11px/1.4 Consolas,monospace;color:rgba(255,255,255,.6);background:rgba(0,0,0,.4);padding:2px 8px;border-radius:6px;letter-spacing:.4px;pointer-events:none}
.previewcaption{color:var(--muted);font-size:12px;margin-top:8px;line-height:1.5;flex:1}
.previewbar{display:flex;align-items:flex-start;gap:12px;margin-top:8px}
.previewbar button{padding:6px 12px;font-size:13px;white-space:nowrap}
#resultLinks{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}
#resultLinks a{color:var(--accent2);font-size:14px}
.foot{color:var(--muted);font-size:12px;text-align:center;padding:14px}
.badge{display:inline-block;background:var(--panel2);border:1px solid var(--border);border-radius:20px;padding:2px 10px;font-size:12px;color:var(--muted)}
</style>
</head>
<body>
<header>
  <h1>🎤 Vilm Lyrics Aligner</h1>
  <span class="sub">Pipeline: cantado + playback opcional + letra → 1 ou 2 MP4 karaokê</span>
</header>
<div class="tabs">
  <button id="tabPipelineBtn" class="active" onclick="showTab('pipeline')">Pipeline</button>
  <button id="tabConfigBtn" onclick="showTab('config')">Configurações</button>
</div>
<main>
  <div id="tabPipeline">
    <section>
      <h2>1. Projeto (cada hino é uma pasta)</h2>
      <label for="existingProjectSelect">Abrir um projeto já gerado para alterar e gerar de novo</label>
      <select id="existingProjectSelect"><option value="">— novo projeto —</option></select>
      <label for="projectName" style="margin-top:12px">Nome do projeto — "Cantor - Hino"</label>
      <input type="text" id="projectName" list="projectList" placeholder="Ex.: Eliane Fernandes - Adorador">
      <datalist id="projectList"></datalist>
      <div class="inline" style="margin-top:10px">
        <input type="checkbox" id="reuseAlignment">
        <label for="reuseAlignment" style="margin:0">Reaproveitar o alinhamento já gerado (não re-alinhar)</label>
      </div>
      <div class="foot" style="text-align:left" id="reuseHint"></div>
      <div class="foot" style="text-align:left" id="projectsRootLabel"></div>
      <div class="inline" style="margin-top:10px">
        <input type="text" id="projectsRootInput" placeholder="Caminho da pasta de projetos" style="flex:1;padding:6px 8px">
        <input type="file" id="projectsRootPicker" webkitdirectory directory multiple hidden>
        <button class="secondary" onclick="document.getElementById('projectsRootPicker').click()">Escolher pasta…</button>
        <button class="secondary" onclick="changeProjectsRoot()">Aplicar</button>
      </div>
      <div class="inline" style="margin-top:10px">
        <button class="secondary" onclick="confirmResetProject()">🗑 Resetar projeto (apaga arquivos gerados e força re-alinhamento)</button>
      </div>
    </section>
    <section>
      <h2>2. Músicas</h2>
      <label for="projectMediaSelect">Música cantada (obrigatória) — arquivo do projeto ou envie um novo</label>
      <select id="projectMediaSelect"><option value="upload">⬆ Enviar novo arquivo…</option></select>
      <div class="drop" id="mediaDrop" onclick="document.getElementById('mediaInput').click()">
        Arraste o hino cantado aqui, ou clique para escolher
      </div>
      <input type="file" id="mediaInput" class="hidden" accept="video/*,audio/*,.mp4,.mov,.mkv,.webm,.avi,.m4v,.mts,.m2ts,.wmv,.flv,.f4v,.mpg,.mpeg,.m2v,.vob,.ts,.mxf,.ogv,.3gp,.3g2,.asf,.rm,.rmvb,.dv,.mp3,.mp2,.mpga,.wav,.m4a,.m4b,.aac,.flac,.alac,.ogg,.oga,.opus,.spx,.wma,.aiff,.aif,.aifc,.ape,.wv,.tta,.dsf,.dff,.amr,.au,.snd,.ac3,.eac3,.dts,.mka,.ra,.caf">
      <div id="mediaInfo" class="badge" style="margin-top:10px"></div>
      <div id="mediaPreviewWrap" class="hidden" style="margin-top:12px">
        <video id="mediaVideo" controls></video>
        <audio id="mediaAudio" controls></audio>
      </div>

      <label for="playbackSelect" style="margin-top:18px">Música playback (opcional) — gera um segundo karaokê sincronizado</label>
      <select id="playbackSelect">
        <option value="">— sem playback —</option>
        <option value="upload">⬆ Enviar novo arquivo…</option>
      </select>
      <div class="drop" id="playbackDrop" onclick="document.getElementById('playbackInput').click()">
        Arraste o playback aqui, ou clique para escolher
      </div>
      <input type="file" id="playbackInput" class="hidden" accept="video/*,audio/*,.mp4,.mov,.mkv,.webm,.avi,.m4v,.mts,.m2ts,.wmv,.flv,.f4v,.mpg,.mpeg,.m2v,.vob,.ts,.mxf,.ogv,.3gp,.3g2,.asf,.rm,.rmvb,.dv,.mp3,.mp2,.mpga,.wav,.m4a,.m4b,.aac,.flac,.alac,.ogg,.oga,.opus,.spx,.wma,.aiff,.aif,.aifc,.ape,.wv,.tta,.dsf,.dff,.amr,.au,.snd,.ac3,.eac3,.dts,.mka,.ra,.caf">
      <div id="playbackInfo" class="badge" style="margin-top:10px"></div>
      <div id="playbackPreviewWrap" class="hidden" style="margin-top:12px">
        <audio id="playbackAudio" controls></audio>
      </div>
    </section>
    <section>
      <h2>3. Letra do hino</h2>
      <div class="inline" style="margin-bottom:6px">
        <textarea id="lyricsText" placeholder="Cole a letra completa aqui, uma linha por verso…"></textarea>
      </div>
      <div class="inline" style="justify-content:space-between">
        <button class="secondary" onclick="document.getElementById('lyricsFile').click()">Carregar TXT…</button>
        <input type="file" id="lyricsFile" class="hidden" accept=".txt,text/plain">
        <span class="badge" id="lyricsCount"></span>
      </div>
    </section>
    <section>
      <h2>4. Gerar o MP4 karaokê</h2>
      <button class="primary" id="runButton" onclick="startJob()" disabled>🎬 Gerar MP4 karaokê</button>
      <div class="foot" style="text-align:left">Com playback selecionado, serão gerados o vídeo cantado e o vídeo playback sincronizado.</div>
      <div class="progress" id="progressBar"><div class="bar"></div></div>
      <div id="stateLabel">Aguardando mídia e letra…</div>
      <div id="logBox" class="hidden"></div>
    </section>
    <section id="editCueWrap" class="hidden">
      <h2>✏ Verificar e editar legendas</h2>
      <p style="color:var(--muted);font-size:14px;margin-bottom:10px">
        Toque o cantado e o playback juntos, clique numa linha para pular até ela e use os botões
        para marcar início/fim com o tempo atual do vídeo.
      </p>
      <div id="editPlayersWrap" class="hidden" style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
        <div>
          <div class="foot" style="text-align:left"><b>Cantado</b> — <span id="editTimeCantado">0:00</span></div>
          <video id="editVideo" controls preload="metadata" style="width:100%"></video>
        </div>
        <div id="editPlaybackCol">
          <div class="foot" style="text-align:left"><b>Playback</b> — <span id="editTimePlayback">0:00</span></div>
          <video id="editVideoPlayback" controls preload="metadata" style="width:100%"></video>
          <div class="foot" style="text-align:left" id="editPlaybackHint"></div>
        </div>
      </div>
      <div class="inline" id="editPlayerBar" style="margin-bottom:10px;gap:10px;flex-wrap:wrap">
        <button class="secondary" type="button" onclick="editPlayPause()">▶ Tocar / Pausar</button>
        <button class="secondary" type="button" onclick="seekEditToCue(-1)">⏮ Linha anterior</button>
        <button class="secondary" type="button" onclick="seekEditToCue(1)">⏭ Próxima linha</button>
        <label class="inline" style="margin:0"><input type="checkbox" id="editSyncPlayback" checked> Sincronizar playback</label>
        <label class="inline" style="margin:0"><input type="checkbox" id="editPlaybackAudio"> Som do playback</label>
      </div>
      <div style="overflow-x:auto">
        <table id="editCueTable">
          <thead>
            <tr>
              <th style="width:40px">#</th>
              <th style="width:110px">Início (s)</th>
              <th style="width:110px">Fim (s)</th>
              <th>Texto da linha</th>
              <th style="width:50px">Ação</th>
            </tr>
          </thead>
          <tbody id="editCueBody"></tbody>
        </table>
      </div>
      <div class="inline" style="margin-top:10px;gap:10px;flex-wrap:wrap">
        <button class="secondary" type="button" onclick="setCueTime('start')">⏱ Início = tempo atual</button>
        <button class="secondary" type="button" onclick="setCueTime('end')">⏱ Fim = tempo atual</button>
        <button class="secondary" type="button" onclick="nudgeCue('start',-0.1)">Início −0,1s</button>
        <button class="secondary" type="button" onclick="nudgeCue('start',0.1)">Início +0,1s</button>
        <button class="secondary" type="button" onclick="nudgeCue('end',-0.1)">Fim −0,1s</button>
        <button class="secondary" type="button" onclick="nudgeCue('end',0.1)">Fim +0,1s</button>
      </div>
      <div class="inline" style="margin-top:10px;gap:10px;flex-wrap:wrap">
        <button class="secondary" type="button" onclick="addEditCueRow()">➕ Adicionar linha</button>
        <button class="secondary" type="button" onclick="removeSelectedEditCue()">➖ Remover selecionada</button>
        <button class="primary" type="button" onclick="saveEditCues()">💾 Salvar edições</button>
        <button class="secondary" type="button" onclick="previewCues()">🎥 Prévia rápida (640×360)</button>
        <button class="primary" type="button" onclick="reRenderCues()">🎬 Re-renderizar vídeo com estas legendas</button>
      </div>
      <div id="editCueStatus" class="foot" style="margin-top:8px"></div>
    </section>
    <section id="resultWrap" class="hidden">
      <h2>5. Resultado</h2>
      <h3>Karaokê cantado</h3>
      <video id="resultVideo" controls></video>
      <div id="resultPlaybackWrap" class="hidden" style="margin-top:16px">
        <h3>Karaokê playback</h3>
        <video id="resultPlayback" controls></video>
      </div>
      <div id="resultLinks"></div>
      <div style="margin-top:12px">
        <button class="secondary" id="openFolderBtn" onclick="openProjectFolder()">📂 Abrir pasta do projeto</button>
      </div>
      <h2 style="margin-top:18px">Tempos das linhas</h2>
      <table id="cueTable"><thead><tr><th>#</th><th>Início</th><th>Fim</th><th>Linha</th></tr></thead><tbody></tbody></table>
    </section>
  </div>

  <div id="tabConfig" class="hidden">
    <section>
      <h2>Perfil de karaokê (pre-set)</h2>
      <div class="inline">
        <select id="profileSelect" onchange="activateProfile()" style="max-width:360px"></select>
        <button class="secondary" onclick="saveCurrentProfile()">Salvar configuração atual neste perfil</button>
      </div>
      <p class="previewcaption" id="profileInfo"></p>
    </section>
    <section>
      <h2>Alinhamento (Vilm)</h2>
      <div class="grid">
        <div><label>Modo</label><select id="cfg_mode"><option value="auto">Automático (recomendado)</option><option value="manual">Manual (linha = legenda)</option></select></div>
        <div><label>Idioma</label><select id="cfg_language"><option value="auto">auto (detectar)</option><option value="pt">Português</option><option value="en">Inglês</option><option value="es">Espanhol</option><option value="ko">Coreano</option><option value="ja">Japonês</option></select></div>
        <div><label>Modelo Whisper</label><select id="cfg_model"><option value="small">small</option><option value="base">base</option><option value="medium">medium</option><option value="tiny">tiny</option></select></div>
        <div><label>Separar vocal/instrumental</label><input type="checkbox" id="cfg_separate" checked></div>
        <div><label>Máx. caracteres por linha</label><input type="number" id="cfg_maxChars" min="10" max="100"></div>
        <div><label>Início antecipado (ms)</label><input type="number" id="cfg_leadMs" min="0" max="2000"></div>
        <div><label>Permanência final (ms)</label><input type="number" id="cfg_endPadMs" min="0" max="5000"></div>
        <div><label>Duração mínima (ms)</label><input type="number" id="cfg_minDur" min="100" max="5000"></div>
        <div><label>Duração máxima (ms)</label><input type="number" id="cfg_maxDur" min="500" max="60000"></div>
        <div><label>Intervalo mínimo (ms)</label><input type="number" id="cfg_minGap" min="0" max="1000"></div>
        <div><label>Deslocamento (s)</label><input type="number" id="cfg_offset" step="0.1"></div>
        <div><label>Deslocamento do playback (s) — ajuste fino da sincronia</label><input type="number" id="cfg_playbackOffset" step="0.1" min="-60" max="60"></div>
        <div><label>Início do trecho (s)</label><input type="number" id="cfg_rangeStart" min="0" step="0.1"></div>
        <div><label>Fim do trecho (s, vazio = até o fim)</label><input type="number" id="cfg_rangeEnd" min="0" step="0.1"></div>
      </div>
    </section>
    <section>
      <h2>Estilo do karaokê</h2>
      <div class="grid">
        <div><label>Karaokê progressivo</label><input type="checkbox" id="cfg_karaoke" checked></div>
        <div><label>Cor normal</label><input type="color" id="cfg_normalColor"></div>
        <div><label>Cor ativa (sendo cantada)</label><input type="color" id="cfg_activeColor"></div>
        <div><label>Cor do contorno</label><input type="color" id="cfg_outlineColor"></div>
        <div><label>Fonte (cascata de fallback)</label>
          <select id="cfg_font">
            <option value="Arial, Segoe UI, Tahoma, Verdana, Georgia, DejaVu Sans">Arial (padrão)</option>
            <option value="Segoe UI, Arial, DejaVu Sans">Segoe UI</option>
            <option value="Times New Roman, Georgia, Cambria, DejaVu Serif">Times New Roman</option>
            <option value="Comic Sans MS, Segoe Print, DejaVu Sans">Comic Sans MS</option>
            <option value="Impact, Arial Black, DejaVu Sans">Impact</option>
            <option value="Courier New, Consolas, DejaVu Sans Mono">Courier New</option>
            <option value="Calibri, Candara, Segoe UI">Calibri</option>
            <option value="custom">Personalizada…</option>
          </select>
          <input type="text" id="cfg_font_custom" placeholder="Fonte1, Fonte2, Fonte3" style="margin-top:6px" class="hidden">
        </div>
        <div><label>Tamanho da fonte</label><input type="number" id="cfg_fontSize" min="8" max="300"></div>
        <div><label>Negrito</label><input type="checkbox" id="cfg_bold"></div>
        <div><label>Contorno</label><input type="number" id="cfg_outline" step="0.5" min="0" max="20"></div>
        <div><label>Sombra</label><input type="number" id="cfg_shadow" step="0.5" min="0" max="20"></div>
        <div><label>Alinhamento (teclado numérico)</label><select id="cfg_alignment">
          <option value="1">1 - inferior esquerda</option><option value="2">2 - inferior centro</option><option value="3">3 - inferior direita</option>
          <option value="4">4 - meio esquerda</option><option value="5">5 - centro</option><option value="6">6 - meio direita</option>
          <option value="7">7 - superior esquerda</option><option value="8">8 - superior centro</option><option value="9">9 - superior direita</option>
        </select></div>
        <div><label>Margem horizontal</label><input type="number" id="cfg_marginH" min="0" max="1000"></div>
        <div><label>Margem vertical</label><input type="number" id="cfg_marginV" min="0" max="1000"></div>
        <div><label>Linhas antes</label><input type="number" id="cfg_linesBefore" min="0" max="5"></div>
        <div><label>Linhas depois</label><input type="number" id="cfg_linesAfter" min="0" max="5"></div>
        <div><label>Linhas por tela (0 = antes/depois; frases rápidas podem ocupar mais, com fonte menor)</label><input type="number" id="cfg_linesPerSlide" min="0" max="5"></div>
        <div><label>Interpretação de versos / estrofes</label><select id="cfg_stanzaMode">
          <option value="time">Por tempo — versos até 6s no mesmo slide</option>
          <option value="stanzas">Por estrofes — linhas em branco da letra separam slides (6s vale dentro da estrofe)</option>
        </select></div>
        <div><label>Transição entre telas</label><select id="cfg_transition">
          <option value="scroll">Rolagem (recomendado)</option>
          <option value="fade">Suave (fade)</option>
          <option value="up">Subir uma linha</option>
          <option value="down">Descer uma linha</option>
          <option value="none">Sem efeito</option>
        </select></div>
        <div><label>Duração da transição (ms)</label><input type="number" id="cfg_transitionMs" min="100" max="2000" step="50"></div>
      </div>
      <div class="previewwrap">
        <div class="previewframe" id="previewFrame">
          <span class="previewbadge" id="previewBadge">Prévia 1920×1080</span>
          <div class="block" id="previewLines"></div>
        </div>
        <div class="previewbar">
          <div class="previewcaption" id="previewCaption"></div>
          <button class="secondary" id="previewPlay" onclick="playPreviewTransition()">▶ Testar transição</button>
        </div>
      </div>
    </section>
    <section>
      <h2>Fundo para áudio (quando não há vídeo)</h2>
      <div class="grid">
        <div><label>Tipo de fundo</label><select id="bg_type"><option value="color">Cor sólida</option><option value="image">Imagem</option></select></div>
        <div><label>Encaixe da imagem</label><select id="bg_fit"><option value="contain">Ajustar (barras na cor do fundo)</option><option value="cover">Preencher (corta as bordas)</option></select></div>
        <div><label>Cor do fundo</label><input type="color" id="bg_color"></div>
        <div><label>Imagem de fundo</label><input type="file" id="bg_image" accept="image/*"></div>
        <div><label>Prévia da imagem</label><img id="bg_thumb" class="bgthumb hidden" alt="Prévia da imagem de fundo"></div>
      </div>
    </section>
    <section>
      <h2>Saída (FFmpeg)</h2>
      <div class="grid">
        <div><label>Resolução pronta</label><select id="out_preset">
          <option value="1920x1080">1920×1080 — Full HD (padrão)</option>
          <option value="1280x720">1280×720 — HD</option>
          <option value="3840x2160">3840×2160 — 4K UHD</option>
          <option value="1080x1920">1080×1920 — vertical (Reels/Shorts)</option>
          <option value="1080x1080">1080×1080 — quadrado</option>
          <option value="custom">Personalizada</option>
        </select></div>
        <div><label>Largura da saída (px)</label><input type="number" id="out_width" min="320" max="7680" step="2"></div>
        <div><label>Altura da saída (px)</label><input type="number" id="out_height" min="240" max="4320" step="2"></div>
        <div><label>Preset x264</label><select id="burn_preset">
          <option value="medium">medium (padrão)</option><option value="veryfast">veryfast (rápido)</option><option value="faster">faster</option><option value="fast">fast</option>
          <option value="slow">slow</option><option value="slower">slower</option><option value="ultrafast">ultrafast</option><option value="superfast">superfast</option><option value="veryslow">veryslow</option>
        </select></div>
        <div><label>Qualidade (CRF, menor = melhor)</label><input type="number" id="burn_crf" min="0" max="51"></div>
        <div><label>Modo demonstração (sem IA, requer WAV)</label><input type="checkbox" id="cfg_demo"></div>
      </div>
    </section>
    <section>
      <button class="primary" onclick="saveSettings()">💾 Salvar configurações</button>
      <button class="secondary" onclick="resetSettings()" style="margin-left:8px">Restaurar padrão</button>
    </section>
  </div>
</main>
<div class="foot">Vilm Lyrics Aligner — a letra fornecida é sempre a fonte do texto. Processamento local.</div>

<script>
const DEFAULT_SETTINGS = {
  alignment:{mode:"auto",language:"auto",model:"small",separate:true,max_chars:30,lead_ms:600,end_pad_ms:500,min_duration_ms:500,max_duration_ms:30000,min_gap_ms:80,offset_seconds:0,playback_offset_seconds:0,range_start:0,range_end:null},
  karaoke:{enabled:true,normal_color:"#FFFFFF",active_color:"#FFFFFF",outline_color:"#000000",back_color:"#00000080",font_name:"Arial, Segoe UI, Tahoma, Verdana, Georgia, DejaVu Sans",font_size:90,bold:true,outline:3,shadow:1,alignment:5,margin_h:80,margin_v:80,lines_before:0,lines_after:0,lines_per_slide:3,stanza_mode:"time",transition:"fade",transition_ms:400},
  background:{type:"color",color:"#0B1D3A",image_filename:null,fit:"contain"},
  output:{width:1920,height:1080},
  burn:{preset:"medium",crf:18},
  demo:false
};
let mediaFile=null, playbackFile=null, bgImageFile=null, bgImageName=null, bgImageUrl=null, jobId=null, pollTimer=null, logCount=0, projectId=null, selectedProjectId=null;
const MEDIA_VIDEO_RE=/\\.(mp4|mov|mkv|webm|avi|m4v|mts|m2ts|wmv|flv|f4v|mpg|mpeg|m2v|vob|ts|mxf|ogv|3gp|3g2|asf|rm|rmvb|dv)$/i;

function showTab(which, updateUrl){
  if(updateUrl===undefined) updateUrl=true;
  document.getElementById('tabPipeline').classList.toggle('hidden', which!=='pipeline');
  document.getElementById('tabConfig').classList.toggle('hidden', which!=='config');
  document.getElementById('tabPipelineBtn').classList.toggle('active', which==='pipeline');
  document.getElementById('tabConfigBtn').classList.toggle('active', which==='config');
  if(updateUrl){
    const path=which==='config'?'/configuracoes':'/pipeline';
    if(location.pathname!==path) history.pushState({tab:which},'',path);
  }
  if(which==='config') requestAnimationFrame(updateStylePreview);
}
function tabFromLocation(){
  let path=location.pathname;
  while(path.length>1&&path.endsWith('/')) path=path.slice(0,-1);
  if(location.hash==='#config'||path==='/configuracoes'||path==='/config') return 'config';
  return 'pipeline';
}
window.addEventListener('popstate',()=>showTab(tabFromLocation(),false));
window.addEventListener('hashchange',()=>showTab(tabFromLocation(),false));
function loadSettings(){
  try{
    const saved=JSON.parse(localStorage.getItem('vilm-webapp-settings-v4')||'{}');
    const s=Object.assign({},DEFAULT_SETTINGS,saved);
    s.alignment=Object.assign({},DEFAULT_SETTINGS.alignment,saved.alignment||{});
    s.karaoke=Object.assign({},DEFAULT_SETTINGS.karaoke,saved.karaoke||{});
    s.background=Object.assign({},DEFAULT_SETTINGS.background,saved.background||{});
    s.output=Object.assign({},DEFAULT_SETTINGS.output,saved.output||{});
    s.burn=Object.assign({},DEFAULT_SETTINGS.burn,saved.burn||{});
    return s;
  }catch(e){ return DEFAULT_SETTINGS; }
}
function saveSettings(){
  const s=collectSettings();
  localStorage.setItem('vilm-webapp-settings-v4', JSON.stringify(s));
  applySettings(s);
  alert('Configurações salvas e aplicadas nesta sessão.');
}
function resetSettings(){ localStorage.removeItem('vilm-webapp-settings-v4'); applySettings(DEFAULT_SETTINGS); }
function collectSettings(){
  const num=(id,f)=>{ const v=parseFloat(document.getElementById(id).value); return isNaN(v)?f:v; };
  return {
    alignment:{
      mode:document.getElementById('cfg_mode').value,
      language:document.getElementById('cfg_language').value,
      model:document.getElementById('cfg_model').value,
      separate:document.getElementById('cfg_separate').checked,
      max_chars:num('cfg_maxChars',30), lead_ms:num('cfg_leadMs',0), end_pad_ms:num('cfg_endPadMs',500),
      min_duration_ms:num('cfg_minDur',500), max_duration_ms:num('cfg_maxDur',10000), min_gap_ms:num('cfg_minGap',80),
      offset_seconds:num('cfg_offset',0), playback_offset_seconds:num('cfg_playbackOffset',0), range_start:num('cfg_rangeStart',0),
      range_end:document.getElementById('cfg_rangeEnd').value===''?null:num('cfg_rangeEnd',0)
    },
    karaoke:{
      enabled:document.getElementById('cfg_karaoke').checked,
      normal_color:document.getElementById('cfg_normalColor').value,
      active_color:document.getElementById('cfg_activeColor').value,
      outline_color:document.getElementById('cfg_outlineColor').value,
      back_color:(loadSettings().karaoke.back_color||"#00000080"),
      font_name:(document.getElementById('cfg_font').value==='custom'
        ? (document.getElementById('cfg_font_custom').value.trim()||'Arial')
        : document.getElementById('cfg_font').value),
      font_size:num('cfg_fontSize',60), bold:document.getElementById('cfg_bold').checked,
      outline:num('cfg_outline',3), shadow:num('cfg_shadow',1),
      alignment:num('cfg_alignment',2), margin_h:num('cfg_marginH',80), margin_v:num('cfg_marginV',80),
      lines_before:num('cfg_linesBefore',0), lines_after:num('cfg_linesAfter',0),
      lines_per_slide:num('cfg_linesPerSlide',3),
      stanza_mode:document.getElementById('cfg_stanzaMode').value,
      transition:document.getElementById('cfg_transition').value,
      transition_ms:num('cfg_transitionMs',400)
    },
    background:{type:document.getElementById('bg_type').value,color:document.getElementById('bg_color').value,image_filename:bgImageName,fit:document.getElementById('bg_fit').value},
    output:{width:num('out_width',1920),height:num('out_height',1080)},
    burn:{preset:document.getElementById('burn_preset').value,crf:num('burn_crf',18)},
    demo:document.getElementById('cfg_demo').checked
  };
}
function setBgThumb(url){
  const thumb=document.getElementById('bg_thumb');
  if(!thumb) return;
  if(url){ thumb.src=url; thumb.classList.remove('hidden'); }
  else { thumb.removeAttribute('src'); thumb.classList.add('hidden'); }
}
function setBackgroundImageState(project,name){
  if(bgImageUrl&&bgImageUrl.startsWith('blob:')) URL.revokeObjectURL(bgImageUrl);
  bgImageFile=null;
  bgImageName=name||null;
  bgImageUrl=(project&&name)
    ? '/api/projects/'+encodeURIComponent(project)+'/background?name='+encodeURIComponent(name)
    : null;
  setBgThumb(bgImageUrl);
  updateStylePreview();
}
function setBackgroundFile(file){
  if(bgImageUrl&&bgImageUrl.startsWith('blob:')) URL.revokeObjectURL(bgImageUrl);
  bgImageFile=file||null;
  bgImageName=null;
  bgImageUrl=file?URL.createObjectURL(file):null;
  if(file) document.getElementById('bg_type').value='image';
  setBgThumb(bgImageUrl);
  updateStylePreview();
}
function applySettings(s){
  const a=s.alignment,k=s.karaoke,b=s.background,u=s.burn;
  set('cfg_mode',a.mode);set('cfg_language',a.language);set('cfg_model',a.model);document.getElementById('cfg_separate').checked=!!a.separate;
  set('cfg_maxChars',a.max_chars);set('cfg_leadMs',a.lead_ms);set('cfg_endPadMs',a.end_pad_ms);set('cfg_minDur',a.min_duration_ms);
  set('cfg_maxDur',a.max_duration_ms);set('cfg_minGap',a.min_gap_ms);set('cfg_offset',a.offset_seconds);set('cfg_playbackOffset',a.playback_offset_seconds||0);
  set('cfg_rangeStart',a.range_start);document.getElementById('cfg_rangeEnd').value=(a.range_end==null?'':a.range_end);
  document.getElementById('cfg_karaoke').checked=!!k.enabled;set('cfg_normalColor',k.normal_color);set('cfg_activeColor',k.active_color);
  set('cfg_outlineColor',k.outline_color);{
    const fontSel=document.getElementById('cfg_font');
    const fontCustom=document.getElementById('cfg_font_custom');
    const known=[...fontSel.options].some(o=>o.value===k.font_name);
    if(known){ fontSel.value=k.font_name; fontCustom.value=''; }
    else { fontSel.value='custom'; fontCustom.value=k.font_name||''; }
    syncFontSelection();
  }set('cfg_fontSize',k.font_size);
  document.getElementById('cfg_bold').checked=!!k.bold;set('cfg_outline',k.outline);set('cfg_shadow',k.shadow);
  set('cfg_alignment',k.alignment);set('cfg_marginH',k.margin_h);set('cfg_marginV',k.margin_v);
  set('cfg_linesBefore',k.lines_before);set('cfg_linesAfter',k.lines_after);set('cfg_linesPerSlide',k.lines_per_slide);
  set('cfg_stanzaMode',k.stanza_mode||'time');set('cfg_transition',k.transition);set('cfg_transitionMs',k.transition_ms);
  set('bg_type',b.type);set('bg_color',b.color);set('bg_fit',b.fit||'contain');set('burn_preset',u.preset);set('burn_crf',u.crf);
  const o=Object.assign({},DEFAULT_SETTINGS.output,s.output||{});
  set('out_width',o.width); set('out_height',o.height);
  syncOutputPreset();
  // Preserva um arquivo de fundo recém-selecionado pelo usuário. Sem isso,
  // salvar as configurações descartava a imagem pendente e o job falhava
  // com "A imagem de fundo selecionada não foi encontrada".
  const pendingBgFile = bgImageFile;
  bgImageFile=null;
  bgImageName=b.image_filename||null;
  if(bgImageUrl&&bgImageUrl.startsWith('blob:')) URL.revokeObjectURL(bgImageUrl);
  bgImageUrl=null;
  setBgThumb(null);
  if(pendingBgFile && !b.image_filename){
    bgImageFile = pendingBgFile;
    bgImageUrl = URL.createObjectURL(pendingBgFile);
    setBgThumb(bgImageUrl);
  }
  document.getElementById('cfg_demo').checked=!!s.demo;
  updateStylePreview();
}
function set(id,v){ const el=document.getElementById(id); if(el) el.value=v; }
const OUTPUT_PRESETS={
  '1920x1080':[1920,1080],
  '1280x720':[1280,720],
  '3840x2160':[3840,2160],
  '1080x1920':[1080,1920],
  '1080x1080':[1080,1080]
};
function syncOutputPreset(){
  const w=Math.round(parseFloat(document.getElementById('out_width').value)||1920);
  const h=Math.round(parseFloat(document.getElementById('out_height').value)||1080);
  const key=`${w}x${h}`;
  document.getElementById('out_preset').value=OUTPUT_PRESETS[key]?key:'custom';
}
document.getElementById('out_preset').addEventListener('change',e=>{
  const preset=OUTPUT_PRESETS[e.target.value];
  if(!preset) return;
  document.getElementById('out_width').value=preset[0];
  document.getElementById('out_height').value=preset[1];
  updateStylePreview();
});
document.getElementById('out_width').addEventListener('input',()=>{syncOutputPreset();updateStylePreview();});
document.getElementById('out_height').addEventListener('input',()=>{syncOutputPreset();updateStylePreview();});
const PREVIEW_LINES=[
  "Mesmo que não haja fruto na videira",
  "Mesmo que não haja flores no jardim",
  "Até mesmo que a figueira não floresça",
  "Nem chuva na terra e nem a erva cresça",
  "Há um Deus no céu olhando para mim"
];
const PREVIEW_LINES_B=[
  "Eu louvarei ao senhor",
  "Na alegria ou na dor, cantarei",
  "Ele é o meu salvador",
  "Ele é o meu senhor, ele é rei",
  "Eu louvarei ao senhor"
];
let previewOverride=null, previewAnimating=false;
function escapeHtml(text){
  return String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function updateStylePreview(){
  const s=collectSettings(), k=s.karaoke, o=s.output||{width:1920,height:1080};
  const frame=document.getElementById('previewFrame');
  const block=document.getElementById('previewLines');
  const caption=document.getElementById('previewCaption');
  if(!frame||!block||!caption) return;
  const outW=Math.max(2,Math.round(o.width)||1920);
  const outH=Math.max(2,Math.round(o.height)||1080);
  const badge=document.getElementById('previewBadge');
  if(badge) badge.textContent=`Prévia ${outW}×${outH}`;
  frame.style.aspectRatio=`${outW} / ${outH}`;
  frame.style.maxWidth=`${Math.round(560*outW/outH)}px`;
  frame.style.marginLeft='auto';
  frame.style.marginRight='auto';
  let n = k.lines_per_slide>0 ? k.lines_per_slide : (k.lines_before+1+k.lines_after);
  n = Math.max(1, Math.min(5, Math.round(n)||1));
  const lines = (previewOverride||PREVIEW_LINES).slice(0,n);
  const activeIndex = Math.floor((lines.length-1)/2);
  const width = frame.clientWidth || 960;
  const styleScale = outH/1080;
  const scale = width/outW;
  const fontSize = Math.max(10, k.font_size*styleScale*scale);
  const marginH = Math.max(0, k.margin_h*styleScale*scale);
  const marginV = Math.max(0, k.margin_v*styleScale*scale);
  const align = parseInt(k.alignment,10)||2;
  const vertical = align<=3 ? 'flex-end' : (align<=6 ? 'center' : 'flex-start');
  const horizontal = [1,4,7].includes(align) ? 'flex-start' : ([3,6,9].includes(align) ? 'flex-end' : 'center');
  frame.style.justifyContent=vertical;
  frame.style.alignItems=horizontal;
  frame.style.padding=`${marginV}px ${marginH}px`;
  if(s.background.type==='image'&&bgImageUrl){
    frame.style.background=document.getElementById('bg_color').value||'#0b1d3a';
    frame.style.backgroundImage=`url("${bgImageUrl}")`;
    frame.style.backgroundSize=(s.background.fit||'contain')==='cover'?'cover':'contain';
    frame.style.backgroundPosition='center';
    frame.style.backgroundRepeat='no-repeat';
  }else{
    frame.style.background=document.getElementById('bg_color').value||'#0b1d3a';
    frame.style.backgroundImage='none';
  }
  block.style.fontFamily=`"${k.font_name}", Arial, sans-serif`;
  block.style.fontWeight=k.bold?700:400;
  block.style.fontSize=`${fontSize}px`;
  block.style.lineHeight='1.18';
  block.style.textAlign=horizontal==='flex-start'?'left':(horizontal==='flex-end'?'right':'center');
  const stroke=Math.max(0,k.outline*styleScale*scale);
  const shadow=Math.max(0,k.shadow*styleScale*scale);
  const base=`-webkit-text-stroke:${stroke}px ${k.outline_color};text-shadow:${shadow}px ${shadow}px 0 ${k.outline_color}`;
  block.innerHTML=lines.map((text,i)=>{
    if(i===activeIndex){
      let cut=0, acc=0;
      for(const word of text.split(' ')){
        if(acc+word.length>text.length*0.55) break;
        acc+=word.length+1; cut=acc;
      }
      const head=escapeHtml(text.slice(0,cut));
      const tail=escapeHtml(text.slice(cut));
      return `<div class="pline" style="${base}"><span style="color:${k.active_color}">${head}</span><span style="color:${k.normal_color}">${tail}</span></div>`;
    }
    const color=i<activeIndex?k.active_color:k.normal_color;
    return `<div class="pline" style="${base};color:${color}">${escapeHtml(text)}</div>`;
  }).join('');
  const posName={1:'inferior esquerda',2:'inferior centro',3:'inferior direita',4:'meio esquerda',5:'centro',6:'meio direita',7:'superior esquerda',8:'superior centro',9:'superior direita'}[align]||'centro';
  const mode = k.lines_per_slide>0
    ? `${n} linha${n>1?'s':''} por tela`
    : `${k.lines_before} antes + atual + ${k.lines_after} depois`;
  const transitionName={scroll:'rolagem',fade:'fade',up:'subir',down:'descer',none:'sem efeito'}[k.transition]||k.transition;
  caption.textContent=`${outW}×${outH} · ${mode} · até ${s.alignment.max_chars} caracteres por linha · ${k.font_name} ${Math.round(k.font_size)} px (base 1080p) · ${posName} · margens ${Math.round(k.margin_h)}×${Math.round(k.margin_v)} · contorno ${k.outline} · sombra ${k.shadow} · transição ${transitionName} (${k.transition_ms} ms)`;
}
function playPreviewTransition(){
  const s=collectSettings(), k=s.karaoke;
  if(previewAnimating) return;
  if(k.lines_per_slide<=0) return alert('Ative "Linhas por tela" (1 a 5) para testar a transição.');
  if(k.transition==='none') return alert('Escolha uma transição diferente de "Sem efeito".');
  const frame=document.getElementById('previewFrame');
  const block=document.getElementById('previewLines');
  if(!frame||!block) return;
  const ms=Math.max(100,Math.min(2000,k.transition_ms));
  const lineHeight=block.offsetHeight/Math.max(1,block.children.length);
  const shift=k.transition==='scroll'?Math.max(lineHeight*2,frame.clientHeight*0.55):lineHeight;
  const down=k.transition==='down';
  previewAnimating=true;
  block.style.transition=`transform ${ms}ms ease, opacity ${ms}ms ease`;
  block.style.transform=k.transition==='fade'?'translateY(0)':`translateY(${down?shift:-shift}px)`;
  block.style.opacity='0';
  setTimeout(()=>{
    previewOverride=previewOverride===PREVIEW_LINES_B?PREVIEW_LINES:PREVIEW_LINES_B;
    updateStylePreview();
    block.style.transition='none';
    block.style.transform=k.transition==='fade'?'translateY(0)':`translateY(${down?-shift:shift}px)`;
    block.style.opacity='0';
    void block.offsetWidth;
    block.style.transition=`transform ${ms}ms ease, opacity ${ms}ms ease`;
    block.style.transform='translateY(0)';
    block.style.opacity='1';
    setTimeout(()=>{ previewAnimating=false; block.style.transition=''; }, ms+60);
  }, ms);
}
document.querySelectorAll('#tabConfig input, #tabConfig select').forEach(el=>{
  el.addEventListener('input',updateStylePreview);
  el.addEventListener('change',updateStylePreview);
});
window.addEventListener('resize',updateStylePreview);

const drop=document.getElementById('mediaDrop');
drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('over');});
drop.addEventListener('dragleave',()=>drop.classList.remove('over'));
drop.addEventListener('drop',e=>{e.preventDefault();drop.classList.remove('over');if(e.dataTransfer.files.length)chooseMedia(e.dataTransfer.files[0]);});
document.getElementById('mediaInput').addEventListener('change',e=>{if(e.target.files.length)chooseMedia(e.target.files[0]);});
function chooseMedia(file){
  mediaFile=file;
  document.getElementById('projectMediaSelect').value='upload';
  const url=URL.createObjectURL(file);
  const isVideo=file.type.startsWith('video/')||MEDIA_VIDEO_RE.test(file.name);
  document.getElementById('mediaPreviewWrap').classList.remove('hidden');
  document.getElementById('mediaVideo').classList.toggle('hidden',!isVideo);
  document.getElementById('mediaAudio').classList.toggle('hidden',isVideo);
  if(isVideo){document.getElementById('mediaVideo').src=url;}
  else{document.getElementById('mediaAudio').src=url;}
  document.getElementById('mediaInfo').textContent=`${file.name} · ${(file.size/1048576).toFixed(1)} MB`;
  const nameInput=document.getElementById('projectName');
  if(!nameInput.value.trim()) nameInput.value=file.name.replace(/\\.[^.]+$/,'').replace(/_/g,' ');
  refreshRun();
}
function clearPlaybackPreview(){
  playbackFile=null;
  const wrap=document.getElementById('playbackPreviewWrap');
  if(wrap) wrap.classList.add('hidden');
  const audio=document.getElementById('playbackAudio');
  if(audio){ audio.removeAttribute('src'); audio.load(); }
  const info=document.getElementById('playbackInfo');
  if(info) info.textContent='';
}
function choosePlayback(file){
  playbackFile=file;
  document.getElementById('playbackSelect').value='upload';
  const audio=document.getElementById('playbackAudio');
  audio.src=URL.createObjectURL(file);
  document.getElementById('playbackPreviewWrap').classList.remove('hidden');
  document.getElementById('playbackInfo').textContent=`${file.name} · ${(file.size/1048576).toFixed(1)} MB`;
}
function previewExistingPlayback(rel){
  playbackFile=null;
  if(!rel||!selectedProjectId){ clearPlaybackPreview(); return; }
  const audio=document.getElementById('playbackAudio');
  audio.src='/api/projects/'+encodeURIComponent(selectedProjectId)+'/media-source?path='+encodeURIComponent(rel);
  document.getElementById('playbackPreviewWrap').classList.remove('hidden');
  document.getElementById('playbackInfo').textContent=rel;
}
const playbackDrop=document.getElementById('playbackDrop');
playbackDrop.addEventListener('dragover',e=>{e.preventDefault();playbackDrop.classList.add('over');});
playbackDrop.addEventListener('dragleave',()=>playbackDrop.classList.remove('over'));
playbackDrop.addEventListener('drop',e=>{e.preventDefault();playbackDrop.classList.remove('over');if(e.dataTransfer.files.length)choosePlayback(e.dataTransfer.files[0]);});
document.getElementById('playbackInput').addEventListener('change',e=>{if(e.target.files.length)choosePlayback(e.target.files[0]);});
document.getElementById('playbackSelect').addEventListener('change',e=>{
  const value=e.target.value;
  if(value===''||value==='upload'){ clearPlaybackPreview(); return; }
  if(value.startsWith('existing:')) previewExistingPlayback(value.slice(9));
});
document.getElementById('bg_image').addEventListener('change',e=>{ if(e.target.files.length) setBackgroundFile(e.target.files[0]); });
document.getElementById('projectMediaSelect').addEventListener('change',e=>{ if(e.target.value.startsWith('existing:')) previewExisting(e.target.value.slice(9)); else refreshRun(); });
document.getElementById('existingProjectSelect').addEventListener('change',e=>{ if(e.target.value) openProject(e.target.value); });
document.getElementById('projectName').addEventListener('input',scheduleProjectMedia);
document.getElementById('lyricsFile').addEventListener('change',e=>{
  const file=e.target.files[0]; if(!file) return;
  const reader=new FileReader();
  reader.onload=()=>{document.getElementById('lyricsText').value=reader.result;refreshLyricsCount();refreshRun();};
  reader.readAsText(file,'utf-8');
});
document.getElementById('lyricsText').addEventListener('input',()=>{refreshLyricsCount();refreshRun();});
function refreshLyricsCount(){
  const text=document.getElementById('lyricsText').value;
  const lines=text.split('\\n').filter(l=>l.trim()).length;
  document.getElementById('lyricsCount').textContent=`${lines} linhas · ${text.length} caracteres`;
}

async function loadEditCues(projectId){
  const wrap=document.getElementById('editCueWrap');
  const body=document.getElementById('editCueBody');
  const status=document.getElementById('editCueStatus');
  if(!wrap||!body) return;
  try{
    const res=await fetch('/api/projects/'+encodeURIComponent(projectId)+'/cues');
    if(!res.ok){ wrap.classList.add('hidden'); return; }
    const data=await res.json();
    const cues=data.cues||[];
    body.innerHTML='';
    cues.forEach((cue,i)=>{
      const tr=document.createElement('tr');
      tr.innerHTML=
        '<td style="cursor:pointer" onclick="seekToCueRow(this)" title="Pular para esta linha">'+(i+1)+' ▶</td>'+
        '<td><input type="number" step="0.001" value="'+cue.start+'" class="cueStart"></td>'+
        '<td><input type="number" step="0.001" value="'+cue.end+'" class="cueEnd"></td>'+
        '<td><input type="text" value="'+escapeHtml(cue.text)+'" class="cueText"></td>'+
        '<td><button type="button" class="secondary" style="padding:2px 6px;font-size:12px" onclick="removeEditCueRow(this)">✕</button></td>';
      body.appendChild(tr);
    });
    wrap.classList.remove('hidden');
    status.textContent=cues.length+' linha(s) carregada(s) de '+(data.source||'srt');
  }catch(e){
    wrap.classList.add('hidden');
    status.textContent='';
  }
}

function escapeHtml(t){ return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function addEditCueRow(){
  const body=document.getElementById('editCueBody');
  if(!body) return;
  const tr=document.createElement('tr');
  const n=body.rows.length+1;
  tr.innerHTML=
    '<td>'+n+'</td>'+
    '<td><input type="number" step="0.001" value="0" class="cueStart"></td>'+
    '<td><input type="number" step="0.001" value="5" class="cueEnd"></td>'+
    '<td><input type="text" value="Nova linha" class="cueText"></td>'+
    '<td><button type="button" class="secondary" style="padding:2px 6px;font-size:12px" onclick="removeEditCueRow(this)">✕</button></td>';
  body.appendChild(tr);
  renumberEditCues();
}

function removeEditCueRow(btn){
  const tr=btn.closest('tr');
  if(tr) tr.remove();
  renumberEditCues();
}

function removeSelectedEditCue(){
  const sel=document.getElementById('editCueTable').querySelector('tr.selected');
  if(sel){ sel.remove(); renumberEditCues(); }
  else alert('Selecione uma linha clicando nela primeiro.');
}

function renumberEditCues(){
  const body=document.getElementById('editCueBody');
  if(!body) return;
  [...body.rows].forEach((tr,i)=>{
    tr.cells[0].textContent=(i+1)+' ▶';
  });
}

async function saveEditCues(){
  const body=document.getElementById('editCueBody');
  const projectId=document.getElementById('projectName').value.trim()||selectedProjectId;
  if(!projectId) return alert('Informe o nome do projeto.');
  const cues=collectEditCues();
  if(!cues) return;
  const status=document.getElementById('editCueStatus');
  status.textContent='Salvando...';
  try{
    const res=await fetch('/api/projects/'+encodeURIComponent(projectId)+'/edit-cues',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({cues})
    });
    const json=await res.json();
    if(!res.ok) return alert('Erro ao salvar: '+(json.error||''));
    status.textContent=json.message||'Legendas salvas.';
    await loadEditCues(projectId);
  }catch(err){ status.textContent='Erro ao salvar.'; alert(err.message); }
}

async function reRenderCues(){
  const projectId=document.getElementById('projectName').value.trim()||selectedProjectId;
  if(!projectId) return alert('Informe o nome do projeto.');
  if(!confirm('Isso re-renderizará o vídeo usando as legendas editadas. Continuar?')) return;
  const status=document.getElementById('editCueStatus');
  status.textContent='Re-renderizando vídeo...';
  try{
    const res=await fetch('/api/projects/'+encodeURIComponent(projectId)+'/re-render',{method:'POST'});
    const json=await res.json();
    if(!res.ok) return alert('Erro ao re-renderizar: '+(json.error||''));
    status.textContent=json.message;
    alert('Vídeo re-renderizado com sucesso! Abra a pasta do projeto para conferir.');
  }catch(err){ status.textContent='Erro ao re-renderizar.'; alert(err.message); }
}

// Funções restauradas do pipeline
function jobRunning(){ return jobId!==null; }
function refreshRun(){
  const sel=document.getElementById('projectMediaSelect');
  const hasMedia=!!mediaFile||(sel.value!==''&&sel.value!=='upload');
  const ready=hasMedia&&document.getElementById('lyricsText').value.trim().length>0&&document.getElementById('projectName').value.trim().length>0&&!jobRunning();
  document.getElementById('runButton').disabled=!ready;
}
function scheduleProjectMedia(){ clearTimeout(projectMediaTimer); projectMediaTimer=setTimeout(loadProjectMedia,400); refreshRun(); }
async function loadProjects(){
  try{
    const info=await (await fetch('/api/info')).json();
    document.getElementById('projectsRootLabel').textContent='Pasta dos projetos: '+info.projects_root;
    document.getElementById('projectsRootInput').value=info.projects_root;
    const data=await (await fetch('/api/projects')).json();
    const list=document.getElementById('projectList');
    list.innerHTML='';
    const picker=document.getElementById('existingProjectSelect');
    const current=picker.value;
    picker.innerHTML='<option value="">— novo projeto —</option>';
    (data.projects||[]).forEach(p=>{
      const opt=document.createElement('option'); opt.value=p.name; list.appendChild(opt);
      const item=document.createElement('option'); item.value=p.id; item.textContent=p.name+(p.has_video?' ✅':'');
      picker.appendChild(item);
    });
    if(current) picker.value=current;
  }catch(e){}
}
async function changeProjectsRoot(){
  const input=document.getElementById('projectsRootInput');
  const value=input.value.trim();
  try{
    const res=await fetch('/api/projects_root',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({projects_root:value||null}),
    });
    const json=await res.json();
    if(!res.ok) return alert('Não foi possível mudar a pasta: '+(json.error||''));
    document.getElementById('projectsRootLabel').textContent='Pasta dos projetos: '+json.projects_root;
    input.value=json.projects_root;
    await loadProjects();
  }catch(err){ alert('Erro ao mudar a pasta de projetos: '+err.message); }
}
async function confirmResetProject(){
  const name=document.getElementById('projectName').value.trim();
  if(!name) return alert('Informe o nome do projeto antes de resetar.');
  if(!confirm('Tem certeza? Isso apagará TODOS os arquivos gerados deste projeto (vídeos, SRT, ASS, alinhamento) e forçará um re-alinhamento completo na próxima gravação.')) return;
  try{
    const res=await fetch('/api/projects/'+encodeURIComponent(name)+'/reset',{method:'POST'});
    const json=await res.json();
    if(!res.ok) return alert('Erro ao resetar: '+(json.error||''));
    alert(json.message||'Projeto resetado com sucesso. Os arquivos antigos foram apagados.');
    await loadProjects();
  }catch(err){ alert('Erro ao resetar o projeto: '+err.message); }
}
document.getElementById('projectsRootPicker').addEventListener('change',function(){
  if(this.files&&this.files.length){
    const p=this.files[0].webkitRelativePath.split('/')[0]||this.files[0].path;
    document.getElementById('projectsRootInput').value=p;
  }
});
async function loadProjectMedia(){
  const name=document.getElementById('projectName').value.trim();
  const sel=document.getElementById('projectMediaSelect');
  const playbackSel=document.getElementById('playbackSelect');
  const previous=sel.value;
  const previousPlayback=playbackSel.value;
  const prevProject=selectedProjectId;
  selectedProjectId=null;
  const limpar=()=>{
    sel.innerHTML='<option value="upload">⬆ Enviar novo arquivo…</option>';
    playbackSel.innerHTML='<option value="">— sem playback —</option><option value="upload">⬆ Enviar novo arquivo…</option>';
    const wrap=document.getElementById('mediaPreviewWrap');
    if(wrap) wrap.classList.add('hidden');
    document.getElementById('mediaInfo').textContent='';
    clearPlaybackPreview();
  };
  if(!name){ limpar(); refreshRun(); return; }
  try{
    const norm=s=>s.toLowerCase().replace(/[\\s]+/g,' ').trim();
    const data=await (await fetch('/api/projects')).json();
    const proj=(data.projects||[]).find(p=>norm(p.name)===norm(name));
    if(!proj){ limpar(); refreshRun(); return; }
    selectedProjectId=proj.id;
    if(prevProject!==proj.id&&!bgImageFile){
      const opened=proj.background||{};
      if(opened.type==='image'&&opened.image_filename){
        document.getElementById('bg_type').value='image';
        setBackgroundImageState(proj.id,opened.image_filename);
      }else{
        setBackgroundImageState(null,null);
        if(opened.type==='color') document.getElementById('bg_type').value='color';
      }
    }
    const files=await (await fetch('/api/projects/'+encodeURIComponent(proj.id)+'/media-files')).json();
    sel.innerHTML='<option value="upload">⬆ Enviar novo arquivo…</option>';
    playbackSel.innerHTML='<option value="">— sem playback —</option><option value="upload">⬆ Enviar novo arquivo…</option>';
    (files.files||[]).forEach(f=>{
      const o=document.createElement('option'); o.value='existing:'+f; o.textContent='📁 '+f; sel.appendChild(o);
      const p=document.createElement('option'); p.value='existing:'+f; p.textContent='📁 '+f; playbackSel.appendChild(p);
    });
    if(previous.startsWith('existing:')&&[...sel.options].some(o=>o.value===previous)) sel.value=previous;
    else if(mediaFile) sel.value='upload';
    if(previousPlayback.startsWith('existing:')&&[...playbackSel.options].some(o=>o.value===previousPlayback)) playbackSel.value=previousPlayback;
    else if(playbackFile) playbackSel.value='upload';
    else if(prevProject!==proj.id&&proj.playback){
      const wantedPlayback=String(proj.playback).split(String.fromCharCode(92)).join('/');
      const saved=[...playbackSel.options].find(o=>o.value.startsWith('existing:')&&String(o.value.slice(9)).split(String.fromCharCode(92)).join('/')===wantedPlayback);
      if(saved){ playbackSel.value=saved.value; previewExistingPlayback(saved.value.slice(9)); }
    }
    if(prevProject!==proj.id&&!mediaFile){
      document.getElementById('mediaPreviewWrap').classList.add('hidden');
      document.getElementById('mediaInfo').textContent='';
    }
    if(prevProject!==proj.id&&!playbackFile&&!playbackSel.value.startsWith('existing:')){
      clearPlaybackPreview();
    }
  }catch(e){}
  refreshRun();
}
async function openProject(id){
  if(!id) return;
  try{
    const res=await fetch('/api/projects/'+encodeURIComponent(id)+'/load');
    const data=await res.json();
    if(!res.ok) throw new Error(data.error||'projeto não encontrado');
    document.getElementById('projectName').value=data.name||id;
    document.getElementById('lyricsText').value=data.lyrics||'';
    refreshLyricsCount();
    const base=loadSettings();
    const saved=data.settings||{};
    applySettings({
      alignment:Object.assign({},base.alignment,saved.alignment||{}),
      karaoke:Object.assign({},base.karaoke,saved.karaoke||{}),
      background:Object.assign({},base.background,saved.background||{}),
      output:Object.assign({},base.output,saved.output||{}),
      burn:Object.assign({},base.burn,saved.burn||{}),
      demo:!!saved.demo
    });
    const openedBackground=Object.assign({},base.background,saved.background||{});
    if(openedBackground.type==='image'&&openedBackground.image_filename){
      setBackgroundImageState(id,openedBackground.image_filename);
    }
    mediaFile=null;
    playbackFile=null;
    clearPlaybackPreview();
    projectId=id;
    await loadProjectMedia();
    const sel=document.getElementById('projectMediaSelect');
    const norm=p=>String(p||'').split(String.fromCharCode(92)).join('/');
    const wanted=norm(data.media);
    let opt=[...sel.options].find(o=>o.value.startsWith('existing:')&&norm(o.value.slice(9))===wanted);
    if(!opt) opt=[...sel.options].find(o=>o.value.startsWith('existing:'));
    if(opt){ sel.value=opt.value; previewExisting(opt.value.slice(9)); }
    else{ document.getElementById('mediaInfo').textContent='⚠ Envie a mídia novamente para regerar.'; }
    const playbackSel=document.getElementById('playbackSelect');
    const wantedPlayback=norm(data.playback);
    if(wantedPlayback){
      const playbackOpt=[...playbackSel.options].find(o=>o.value.startsWith('existing:')&&norm(o.value.slice(9))===wantedPlayback);
      if(playbackOpt){ playbackSel.value=playbackOpt.value; previewExistingPlayback(playbackOpt.value.slice(9)); }
      else{ playbackSel.value=''; clearPlaybackPreview(); }
    }else{ playbackSel.value=''; clearPlaybackPreview(); }
    const reuse=document.getElementById('reuseAlignment');
    reuse.checked=!!data.has_alignment;
    document.getElementById('reuseHint').textContent=data.has_alignment
      ? 'Alinhamento encontrado: as linhas e os tempos serão mantidos. Só o estilo e o vídeo serão refeitos.'
      : 'Este projeto ainda não tem alinhamento salvo; será necessário alinhar.';
    refreshRun();
    await loadEditCues(id);
    await loadEditPlayers(id, data.outputs);
  }catch(err){ alert('Não foi possível abrir o projeto: '+err.message); }
}
function previewExisting(rel){
  if(!selectedProjectId) return;
  const url='/api/projects/'+encodeURIComponent(selectedProjectId)+'/media-source?path='+encodeURIComponent(rel);
  const isVideo=fileTypeIsVideo(rel);
  document.getElementById('mediaPreviewWrap').classList.remove('hidden');
  document.getElementById('mediaVideo').classList.toggle('hidden',!isVideo);
  document.getElementById('mediaAudio').classList.toggle('hidden',isVideo);
  if(isVideo) document.getElementById('mediaVideo').src=url;
  else document.getElementById('mediaAudio').src=url;
  document.getElementById('mediaInfo').textContent='📁 '+rel;
  refreshRun();
}
function fileTypeIsVideo(rel){
  return MEDIA_VIDEO_RE.test(rel);
}

let editPlayersWired=false, editSyncLock=false, editPlaybackOffset=0, editActiveRow=null;

function fmtEditClock(seconds){
  seconds=Math.max(0, Number(seconds)||0);
  const m=Math.floor(seconds/60), s=Math.floor(seconds%60);
  return m+':'+String(s).padStart(2,'0');
}
function updateEditTimeLabels(){
  const v=document.getElementById('editVideo');
  const pv=document.getElementById('editVideoPlayback');
  const a=document.getElementById('editTimeCantado');
  const b=document.getElementById('editTimePlayback');
  if(a&&v) a.textContent=fmtEditClock(v.currentTime)+' / '+fmtEditClock(v.duration||0);
  if(b&&pv) b.textContent=(pv.style.display==='none')?'—':(fmtEditClock(pv.currentTime)+' / '+fmtEditClock(pv.duration||0));
}
function syncEditVideos(source){
  const v=document.getElementById('editVideo');
  const pv=document.getElementById('editVideoPlayback');
  if(!v||!pv||pv.style.display==='none') return;
  if(!document.getElementById('editSyncPlayback').checked) return;
  if(editSyncLock) return;
  editSyncLock=true;
  try{
    if(source==='cantado'){
      const target=Math.max(0, v.currentTime+editPlaybackOffset);
      if(Math.abs(pv.currentTime-target)>0.25) pv.currentTime=target;
    }else{
      const target=Math.max(0, pv.currentTime-editPlaybackOffset);
      if(Math.abs(v.currentTime-target)>0.25) v.currentTime=target;
    }
  }finally{ editSyncLock=false; }
}
function wireEditPlayers(){
  if(editPlayersWired) return;
  editPlayersWired=true;
  const v=document.getElementById('editVideo');
  const pv=document.getElementById('editVideoPlayback');
  v.addEventListener('play',()=>{ syncEditVideos('cantado'); if(pv.src) pv.play().catch(()=>{}); });
  pv.addEventListener('play',()=>{ syncEditVideos('playback'); if(v.src) v.play().catch(()=>{}); });
  v.addEventListener('pause',()=>{ if(!pv.paused) pv.pause(); });
  pv.addEventListener('pause',()=>{ if(!v.paused) v.pause(); });
  v.addEventListener('seeking',()=>syncEditVideos('cantado'));
  v.addEventListener('seeked',()=>syncEditVideos('cantado'));
  pv.addEventListener('seeking',()=>syncEditVideos('playback'));
  pv.addEventListener('seeked',()=>syncEditVideos('playback'));
  v.addEventListener('timeupdate',()=>{ updateEditTimeLabels(); highlightActiveEditCue(); });
  pv.addEventListener('timeupdate',updateEditTimeLabels);
  document.getElementById('editPlaybackAudio').addEventListener('change',e=>{ pv.muted=!e.target.checked; });
}
async function loadEditPlayers(projectId, outputs){
  const wrap=document.getElementById('editPlayersWrap');
  const v=document.getElementById('editVideo');
  const pv=document.getElementById('editVideoPlayback');
  const hint=document.getElementById('editPlaybackHint');
  if(!wrap||!v||!pv) return;
  const base='/api/projects/'+encodeURIComponent(projectId)+'/files/';
  editPlaybackOffset=0;
  const hasCantado=!!(outputs&&(outputs.video_cantado||outputs.video));
  if(!hasCantado){ wrap.classList.add('hidden'); return; }
  wrap.classList.remove('hidden');
  v.src=base+'video_cantado.mp4?t='+Date.now();
  if(outputs&&outputs.video_playback){
    pv.style.display='';
    pv.src=base+'video_playback.mp4?t='+Date.now();
    hint.textContent='';
  }else{
    pv.pause(); pv.removeAttribute('src'); pv.load(); pv.style.display='none';
    hint.textContent='Este projeto não tem vídeo de playback.';
  }
  try{
    const m=await (await fetch(base+'manifest.json?t='+Date.now())).json();
    const sync=(m.alignment||{}).playback_sync||{};
    editPlaybackOffset=Number(sync.offset_seconds)||0;
    if(sync.method&&outputs&&outputs.video_playback){
      hint.textContent='Sincronia: '+sync.method+' (desloc. '+(editPlaybackOffset>=0?'+':'')+editPlaybackOffset.toFixed(2)+'s)';
    }
  }catch(e){}
  pv.muted=!document.getElementById('editPlaybackAudio').checked;
  wireEditPlayers();
  updateEditTimeLabels();
}
function editPlayPause(){
  const v=document.getElementById('editVideo');
  if(!v) return;
  if(v.paused) v.play().catch(()=>{}); else v.pause();
}
function seekEditToTime(t){
  const v=document.getElementById('editVideo');
  const pv=document.getElementById('editVideoPlayback');
  if(!v) return;
  v.currentTime=Math.max(0, Number(t)||0);
  syncEditVideos('cantado');
  if(pv&&pv.style.display!=='none'&&!document.getElementById('editSyncPlayback').checked){
    pv.currentTime=Math.max(0,(Number(t)||0)+editPlaybackOffset);
  }
  updateEditTimeLabels();
}
function selectEditRow(tr){
  document.getElementById('editCueTable').querySelectorAll('tr').forEach(r=>r.classList.remove('selected'));
  if(tr) tr.classList.add('selected');
}
function seekToCueRow(td){
  const tr=td.closest('tr');
  if(!tr) return;
  selectEditRow(tr);
  const start=parseFloat(tr.querySelector('.cueStart').value)||0;
  seekEditToTime(start);
  tr.scrollIntoView({block:'center'});
}
function activeEditCueIndex(){
  const v=document.getElementById('editVideo');
  const t=v?Number(v.currentTime)||0:0;
  const rows=[...document.getElementById('editCueBody').rows];
  let inside=-1, lastStarted=-1;
  rows.forEach((tr,i)=>{
    const s=parseFloat(tr.querySelector('.cueStart').value)||0;
    const e=parseFloat(tr.querySelector('.cueEnd').value)||0;
    if(t>=s-0.15&&t<=e+0.6) inside=i;
    if(t>=s) lastStarted=i;
  });
  return inside>=0?inside:lastStarted;
}
function highlightActiveEditCue(){
  const idx=activeEditCueIndex();
  const rows=[...document.getElementById('editCueBody').rows];
  rows.forEach((tr,i)=>tr.classList.toggle('playing',i===idx));
  if(idx>=0 && rows[idx]!==editActiveRow){
    editActiveRow=rows[idx];
    const rect=rows[idx].getBoundingClientRect();
    if(rect.top<80||rect.bottom>window.innerHeight-80) rows[idx].scrollIntoView({block:'center'});
  }
}
function seekEditToCue(direction){
  const rows=[...document.getElementById('editCueBody').rows];
  if(!rows.length) return;
  let idx=activeEditCueIndex();
  if(idx<0) idx=0;
  idx=Math.max(0,Math.min(rows.length-1,idx+direction));
  selectEditRow(rows[idx]);
  const start=parseFloat(rows[idx].querySelector('.cueStart').value)||0;
  seekEditToTime(start);
  rows[idx].scrollIntoView({block:'center'});
}
function getSelectedEditRow(){
  const tr=document.getElementById('editCueBody').querySelector('tr.selected');
  return tr||null;
}
function setCueTime(field){
  const tr=getSelectedEditRow();
  if(!tr) return alert('Selecione uma linha na tabela primeiro.');
  const v=document.getElementById('editVideo');
  const t=Math.max(0, v?Number(v.currentTime)||0:0);
  const input=tr.querySelector(field==='start'?'.cueStart':'.cueEnd');
  input.value=t.toFixed(3);
  document.getElementById('editCueStatus').textContent='Tempo '+(field==='start'?'inicial':'final')+' definido para '+t.toFixed(2)+'s.';
}
function nudgeCue(field, delta){
  const tr=getSelectedEditRow();
  if(!tr) return alert('Selecione uma linha na tabela primeiro.');
  const input=tr.querySelector(field==='start'?'.cueStart':'.cueEnd');
  const value=Math.max(0,(parseFloat(input.value)||0)+delta);
  input.value=value.toFixed(3);
  document.getElementById('editCueStatus').textContent=(field==='start'?'Início':'Fim')+' ajustado para '+value.toFixed(3)+'s.';
}
function collectEditCues(){
  const body=document.getElementById('editCueBody');
  const cues=[];
  for(const tr of body.rows){
    const start=parseFloat(tr.querySelector('.cueStart').value);
    const end=parseFloat(tr.querySelector('.cueEnd').value);
    const text=tr.querySelector('.cueText').value.trim();
    if(isNaN(start)||isNaN(end)){ alert('Preencha os tempos corretamente na linha '+(tr.rowIndex+1)); return null; }
    if(end<=start){ alert('O fim deve ser maior que o início na linha '+(tr.rowIndex+1)); return null; }
    cues.push({start,end,text});
  }
  return cues;
}
async function previewCues(){
  const projectId=document.getElementById('projectName').value.trim()||selectedProjectId;
  if(!projectId) return alert('Informe o nome do projeto.');
  const cues=collectEditCues();
  if(!cues) return;
  const status=document.getElementById('editCueStatus');
  status.textContent='Gerando prévia rápida… isso pode levar alguns minutos.';
  try{
    const res=await fetch('/api/projects/'+encodeURIComponent(projectId)+'/preview',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({cues})
    });
    const json=await res.json();
    if(!res.ok){ status.textContent='Erro na prévia.'; return alert('Erro ao gerar prévia: '+(json.error||'')); }
    const base='/api/projects/'+encodeURIComponent(projectId)+'/files/';
    const v=document.getElementById('editVideo');
    v.src=base+(json.video||'preview_karaoke.mp4')+'?t='+Date.now();
    status.textContent='Prévia pronta: '+(json.width||'')+'×'+(json.height||'')+' ('+(json.cues_count||cues.length)+' linhas).';
  }catch(err){ status.textContent='Erro na prévia.'; alert(err.message); }
}

function syncFontSelection(){
  const sel=document.getElementById('cfg_font');
  const custom=document.getElementById('cfg_font_custom');
  if(!sel||!custom) return;
  custom.classList.toggle('hidden', sel.value!=='custom');
}
document.getElementById('cfg_font').addEventListener('change',syncFontSelection);

async function startJob(){
  const sel=document.getElementById('projectMediaSelect');
  const mediaSource=sel.value;
  if(!mediaFile&&!(mediaSource.startsWith('existing:'))) return alert('Escolha a mídia: um arquivo do projeto ou envie um novo.');
  const lyrics=document.getElementById('lyricsText').value.trim();
  if(!lyrics) return alert('Cole ou carregue a letra do hino.');
  const project=document.getElementById('projectName').value.trim();
  if(!project) return alert('Informe o nome do projeto (Cantor - Hino).');
  const fd=new FormData();
  if(mediaSource.startsWith('existing:')){ fd.append('media_source',mediaSource); }
  else { fd.append('media',mediaFile); }
  const playbackSource=document.getElementById('playbackSelect').value;
  if(playbackFile){ fd.append('playback',playbackFile); }
  else if(playbackSource.startsWith('existing:')){ fd.append('playback_source',playbackSource); }
  else if(playbackSource===''&&selectedProjectId){ fd.append('playback_source','remove'); }
  fd.append('lyrics_text',lyrics);
  fd.append('project',project);
  fd.append('reuse_alignment',document.getElementById('reuseAlignment').checked?'1':'0');
  fd.append('settings',JSON.stringify(collectSettings()));
  if(bgImageFile) fd.append('background_image',bgImageFile);
  document.getElementById('runButton').disabled=true;
  document.getElementById('stateLabel').textContent='Enviando…';
  document.getElementById('stateLabel').className='';
  document.getElementById('logBox').classList.remove('hidden');
  document.getElementById('logBox').textContent='';
  logCount=0;
  const bar=document.getElementById('progressBar'); bar.classList.add('running');
  document.getElementById('resultWrap').classList.add('hidden');
  try{
    const res=await fetch('/api/jobs',{method:'POST',body:fd});
    const data=await res.json();
    if(!res.ok){ throw new Error(data.error||'Erro ao criar o trabalho.'); }
    jobId=data.job_id; projectId=data.job.project_id;
    poll();
  }catch(err){
    alert(err.message);
    bar.classList.remove('running');
    refreshRun();
  }
}
async function poll(){
  if(!jobId) return;
  try{
    const res=await fetch('/api/jobs/'+jobId);
    const j=await res.json();
    renderProgress(j);
    if(j.state==='queued'||j.state==='running'){ pollTimer=setTimeout(poll,1200); }
    else if(j.state==='done'){ finishJob(j); }
    else if(j.state==='failed'){
      document.getElementById('stateLabel').textContent='Falhou: '+(j.error||'erro desconhecido');
      document.getElementById('stateLabel').className='bad';
      document.getElementById('progressBar').classList.remove('running');
      jobId=null; clearTimeout(pollTimer);
      refreshRun();
    }
  }catch(e){ pollTimer=setTimeout(poll,2000); }
}
function fmtElapsed(seconds){
  seconds=Math.max(0,Math.floor(seconds));
  const h=Math.floor(seconds/3600), m=Math.floor(seconds%3600/60), s=seconds%60;
  return h>0 ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}
function renderProgress(j){
  const box=document.getElementById('logBox');
  const lines=j.progress||[];
  const t0=j.started_at||(lines.length?lines[0].t:0);
  if(lines.length>logCount){
    for(let i=logCount;i<lines.length;i++){
      const at=lines[i].t?`[${fmtElapsed(lines[i].t-t0)}] `:'';
      box.textContent+=(box.textContent?'\\n':'')+at+lines[i].m;
    }
    logCount=lines.length;
    box.scrollTop=box.scrollHeight;
  }
  const label=document.getElementById('stateLabel');
  if(j.state==='queued'){label.textContent='Na fila…';label.className='';}
  else if(j.state==='running'){label.textContent=`Processando… [${fmtElapsed((j.now||Date.now()/1000)-t0)}] (veja o log abaixo)`;label.className='';}
}
function finishJob(j){
  projectId=j.project_id;
  jobId=null; clearTimeout(pollTimer);
  document.getElementById('stateLabel').textContent='✅ Concluído!';
  document.getElementById('stateLabel').className='ok';
  document.getElementById('progressBar').classList.remove('running');
  document.getElementById('resultWrap').classList.remove('hidden');
  const base=`/api/projects/${encodeURIComponent(j.project_id)}/files/`;
  const video=document.getElementById('resultVideo');
  while(video.firstChild) video.removeChild(video.firstChild);
  const outputs=j.outputs||{};
  video.src=base+(outputs.video_cantado||'video_with_lyrics.mp4')+'?t='+Date.now();
  const playbackVideo=document.getElementById('resultPlayback');
  const playbackWrap=document.getElementById('resultPlaybackWrap');
  if(outputs.video_playback){
    playbackWrap.classList.remove('hidden');
    while(playbackVideo.firstChild) playbackVideo.removeChild(playbackVideo.firstChild);
    playbackVideo.src=base+outputs.video_playback+'?t='+Date.now();
  }else{
    playbackWrap.classList.add('hidden');
    playbackVideo.removeAttribute('src'); playbackVideo.load();
  }
  const links=document.getElementById('resultLinks');
  links.innerHTML='';
  const add=(label,name,dl)=>{
    if(!name) return;
    const a=document.createElement('a'); a.href=base+name+'?t='+Date.now(); a.textContent=label;
    if(dl) a.setAttribute('download',name); else a.target='_blank';
    links.appendChild(a);
  };
  add('⬇ MP4 cantado',outputs.video_cantado||'video_with_lyrics.mp4',false);
  add('⬇ MP4 playback',outputs.video_playback,false);
  add('⬇ SRT cantado','lyrics.srt',true);
  add('⬇ SRT playback',outputs.playback_srt,true);
  add('⬇ ASS cantado','lyrics.ass',true);
  add('⬇ ASS playback',outputs.playback_ass,true);
  if(outputs.karaoke_ass) add('⬇ ASS karaokê cantado','lyrics.karaoke.ass',true);
  if(outputs.playback_karaoke_ass) add('⬇ ASS karaokê playback',outputs.playback_karaoke_ass,true);
  add('⬇ Manifesto','manifest.json',true);
  add('⬇ VTT cantado','preview.vtt',true);
  add('⬇ VTT playback',outputs.playback_vtt,true);
  const tbody=document.querySelector('#cueTable tbody'); tbody.innerHTML='';
  (j.cues||[]).forEach(c=>{
    const tr=document.createElement('tr');
    const fmt=s=>{const h=Math.floor(s/3600),m=Math.floor(s%3600/60),ss=Math.floor(s%60),ms=Math.round((s%1)*1000);return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(ss).padStart(2,'0')+','+String(ms).padStart(3,'0');};
    [c.index,fmt(c.start),fmt(c.end),c.text].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.appendChild(td);});
    tbody.appendChild(tr);
  });
  loadProjects();
  loadProjectMedia();
  refreshRun();
}
async function openProjectFolder(){
  if(!projectId) return;
  await fetch('/api/projects/'+encodeURIComponent(projectId)+'/open');
}
let PROFILES={profiles:{},active:null};
function showProfileInfo(){
  const el=document.getElementById('profileInfo'); if(!el) return;
  const p=PROFILES.profiles[PROFILES.active]||PROFILES.profiles[document.getElementById('profileSelect').value];
  if(!p){ el.textContent=''; return; }
  const e=p.extras||{};
  const bits=[];
  if(e.intro_seconds) bits.push('intro '+e.intro_seconds+'s: '+(e.intro_image||''));
  if(e.outro_image) bits.push('logo final: '+e.outro_image);
  if(e.timing_rule) bits.push(e.timing_rule);
  el.textContent=bits.join(' | ');
}
async function initProfiles(){
  try{
    const r=await fetch('/api/profiles'); PROFILES=await r.json();
    const sel=document.getElementById('profileSelect');
    sel.innerHTML=Object.entries(PROFILES.profiles).map(([k,p])=>'<option value="'+k+'">'+(p.name||k)+'</option>').join('');
    const active=PROFILES.active||Object.keys(PROFILES.profiles)[0];
    if(active) sel.value=active;
    showProfileInfo();
    if(active && localStorage.getItem('vilm-profile-applied')!==active && PROFILES.profiles[active]){
      applySettings(Object.assign({},DEFAULT_SETTINGS,PROFILES.profiles[active].settings));
      localStorage.setItem('vilm-profile-applied',active);
    }
    await loadProjects();
  }catch(e){ console.warn('perfis:',e); }
}
async function activateProfile(){
  const name=document.getElementById('profileSelect').value;
  PROFILES.active=name; showProfileInfo();
  try{ await fetch('/api/profiles/'+encodeURIComponent(name)+'/activate',{method:'POST'}); }catch(e){}
  const p=PROFILES.profiles[name];
  if(p){ applySettings(Object.assign({},DEFAULT_SETTINGS,p.settings)); localStorage.setItem('vilm-profile-applied',name); }
  if(p&&p.projects_root){ document.getElementById('projectsRootLabel').textContent='Pasta dos projetos: '+p.projects_root; }
  try{ await loadProjects(); }catch(e){}
}
async function saveCurrentProfile(){
  const name=document.getElementById('profileSelect').value;
  if(!name) return;
  const settings=collectSettings();
  try{
    const r=await fetch('/api/profiles/'+encodeURIComponent(name),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings:settings})});
    const j=await r.json();
    if(j.error){ alert(j.error); return; }
    PROFILES.profiles[name].settings=j.settings;
    alert('Perfil salvo: '+(PROFILES.profiles[name].name||name));
  }catch(e){ alert('Erro ao salvar perfil: '+e); }
}
applySettings(loadSettings());
refreshLyricsCount();
refreshRun();
initProfiles();
showTab(tabFromLocation(),false);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())