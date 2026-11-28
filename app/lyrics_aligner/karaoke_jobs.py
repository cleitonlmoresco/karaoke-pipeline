"""Pipeline central de karaoke: perfis, alinhamento, QC, ASS, video e intro/outro.

Usado pelo CLI (comando ``make``) e pelo painel Tk.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
import warnings as _warnings
from pathlib import Path
from typing import Any, Callable, Mapping

from .burn_lock import burn_guard
from .karaoke import (
    KaraokeStyle,
    build_background_command,
    render_karaoke_ass,
    stanza_boundaries_from_lyrics,
)
from .models import Cue
from .pipeline import align_to_srt
from .publish import burn_subtitles, find_ffmpeg
from .srt import parse_srt, render_srt
from .sync import build_audio_time_map

INSTALL_DIR = Path(__file__).resolve().parents[2]
PROFILES_PATH = INSTALL_DIR / "profiles.json"
TOOLS = INSTALL_DIR / "tools"

DEFAULT_ALIGNMENT: dict[str, Any] = {
    "mode": "auto", "language": "auto", "model": "small", "separate": True,
    "max_chars": 30, "lead_ms": 600, "end_pad_ms": 500,
    "min_duration_ms": 500, "max_duration_ms": 10000, "min_gap_ms": 80,
}
DEFAULT_KARAOKE: dict[str, Any] = {
    "enabled": True, "normal_color": "#000000", "active_color": "#000000",
    "outline_color": "#000000", "back_color": "#00000000",
    "font_name": "Arial", "font_size": 84, "active_font_size": 84,
    "bold": True, "outline": 0.0, "shadow": 0.0, "alignment": 2,
    "margin_h": 80, "margin_v": 280, "lines_before": 0, "lines_after": 3,
    "lines_per_slide": 0, "stanza_mode": "stanzas", "transition": "scroll",
    "transition_ms": 600, "scroll_pause_seconds": 2.5,
}
DEFAULT_BACKGROUND: dict[str, Any] = {
    "type": "image", "color": "#0B1D3A", "image_filename": None, "fit": "contain",
}

# Limites validados antes de qualquer ffmpeg/ASS (mesma regra em profiles_api).
FPS_MIN, FPS_MAX = 1, 120
FONT_SIZE_MIN, FONT_SIZE_MAX = 8, 300
FONT_NAME_FALLBACK = "Arial"
_MARGIN_KEYS = ("margin_h", "margin_v", "margin_l", "margin_r")

ALIGNMENT_FINGERPRINT_VERSION = 1
ALIGNMENT_FINGERPRINT_FILE = "alignment-fingerprint.json"
QC_UNAVAILABLE_WARNING = (
    "QC indisponivel: alignment-debug.json ausente/ilegivel; "
    "canto sem legenda nao foi avaliado."
)


def load_profiles() -> dict:
    """Public compatibility wrapper; ``profiles_api`` owns profile I/O."""
    from .profiles_api import load_profiles as _load_profiles

    return _load_profiles()


def save_profiles(payload: dict) -> None:
    """Public compatibility wrapper; keeps the historical ``None`` return."""
    from .profiles_api import save_profiles as _save_profiles

    _save_profiles(payload)


def get_profile(name: str) -> dict:
    from .profiles_api import get_profile as _get_profile

    return _get_profile(name)


def profile_settings(profile: dict) -> dict:
    """Merge a profile's settings over the profile-oriented defaults.

    ``profiles_api.profile_settings`` delegates dict inputs back to this
    function, so the merge stays the canonical implementation.  A profile
    *name* is delegated to ``profiles_api`` as the single data-layer entry
    point.  ``run_project`` and ``make_style`` call the dict form unchanged.
    """
    if isinstance(profile, str):
        from .profiles_api import profile_settings as _profile_settings

        return _profile_settings(profile)
    raw = profile.get("settings") or {}
    return {
        "alignment": {**DEFAULT_ALIGNMENT, **(raw.get("alignment") or {})},
        "karaoke": {**DEFAULT_KARAOKE, **(raw.get("karaoke") or {})},
        "background": {**DEFAULT_BACKGROUND, **(raw.get("background") or {})},
        "output": {"width": 1920, "height": 1080, "fps": 30, **(raw.get("output") or {})},
        "burn": {"preset": "medium", "crf": 18, **(raw.get("burn") or {})},
    }


def _canonical_json(payload: Any) -> str:
    """JSON estavel para hashing (chaves ordenadas, sem espacos)."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _as_int(value: Any, label: str) -> int:
    """Converter para inteiro ou levantar ValueError com mensagem em PT."""
    if isinstance(value, bool):
        raise ValueError(f"{label} deve ser um numero inteiro (recebido: {value!r}).")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"{label} deve ser um numero inteiro (recebido: {value!r})."
        ) from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{label} deve ser um numero inteiro (recebido: {value!r}).")
    return number


def _as_non_negative(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} deve ser maior ou igual a zero (recebido: {value!r}).")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"{label} deve ser maior ou igual a zero (recebido: {value!r})."
        ) from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} deve ser maior ou igual a zero (recebido: {value!r}).")
    return number


def _validated_fps(value: Any) -> int:
    fps = _as_int(value, "FPS")
    if not FPS_MIN <= fps <= FPS_MAX:
        raise ValueError(f"FPS invalido ({fps}). Use um inteiro entre {FPS_MIN} e {FPS_MAX}.")
    return fps


def _validated_font_size(value: Any) -> int:
    size = _as_int(value, "Tamanho da fonte")
    if not FONT_SIZE_MIN <= size <= FONT_SIZE_MAX:
        raise ValueError(
            f"Tamanho da fonte invalido ({size}). "
            f"Use um inteiro entre {FONT_SIZE_MIN} e {FONT_SIZE_MAX}."
        )
    return size


def _validated_font_name(
    value: Any,
    *,
    fallback: bool,
    warnings: list[str] | None = None,
    say: Callable[[str], None] | None = None,
) -> str:
    name = "" if value is None else str(value).strip()
    if name:
        return name
    if not fallback:
        raise ValueError("O nome da fonte nao pode ser vazio.")
    message = f"Nome da fonte vazio; usando '{FONT_NAME_FALLBACK}'."
    if warnings is not None:
        warnings.append(message)
    if say is not None:
        say("AVISO " + message)
    _warnings.warn(message, UserWarning, stacklevel=3)
    return FONT_NAME_FALLBACK


def validate_profile_values(
    settings: Mapping[str, Any],
    *,
    allow_font_fallback: bool = True,
    warnings: list[str] | None = None,
    say: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Validar fps/fonte/margens do perfil ANTES de ffmpeg e ASS.

    Levanta ``ValueError`` com mensagem clara em portugues para valores fora
    da faixa; nao engula excecoes.  So valida as chaves efetivamente
    presentes, para que um ``settings`` parcial (create/update) seja aceito.
    Devolve os valores normalizados (fonte vazia vira ``Arial`` quando
    ``allow_font_fallback`` e verdadeiro, com aviso).
    """
    normalized: dict[str, Any] = {"output": {}, "karaoke": {}}
    output = settings.get("output") or {}
    if not isinstance(output, Mapping):
        raise ValueError("A secao 'output' do perfil deve ser um objeto JSON.")
    if "fps" in output:
        normalized["output"]["fps"] = _validated_fps(output["fps"])

    karaoke = settings.get("karaoke") or {}
    if not isinstance(karaoke, Mapping):
        raise ValueError("A secao 'karaoke' do perfil deve ser um objeto JSON.")
    if "font_name" in karaoke:
        normalized["karaoke"]["font_name"] = _validated_font_name(
            karaoke["font_name"], fallback=allow_font_fallback,
            warnings=warnings, say=say,
        )
    if "font_size" in karaoke:
        normalized["karaoke"]["font_size"] = _validated_font_size(karaoke["font_size"])
    for key in _MARGIN_KEYS:
        if key in karaoke:
            normalized["karaoke"][key] = _as_non_negative(karaoke[key], f"Margem '{key}'")
    return normalized


def alignment_overrides(settings: dict) -> dict[str, Any]:
    """Overrides exatamente como enviados ao servico de alinhamento."""
    align = settings.get("alignment") or {}
    default = DEFAULT_ALIGNMENT
    return {
        "max_chars": int(align.get("max_chars", default["max_chars"])),
        "auto_segment": str(align.get("mode", default["mode"])) != "manual",
        "language": align.get("language", default["language"]),
        "model_name": align.get("model", default["model"]),
        "separate": bool(align.get("separate", default["separate"])),
        "lead_ms": int(align.get("lead_ms", default["lead_ms"])),
        "offset_seconds": float(align.get("offset_seconds", 0.0) or 0.0),
        "range_start_seconds": float(align.get("range_start", 0.0) or 0.0),
        "range_end_seconds": align.get("range_end"),
        "end_pad_ms": int(align.get("end_pad_ms", default["end_pad_ms"])),
        "min_duration_ms": int(align.get("min_duration_ms", default["min_duration_ms"])),
        "max_duration_ms": int(align.get("max_duration_ms", default["max_duration_ms"])),
        "min_gap_ms": int(align.get("min_gap_ms", default["min_gap_ms"])),
    }


def _alignment_timing(settings: dict, extras: dict) -> dict[str, Any]:
    """Dados de fps/resolucao/transicao/intro que afetam o timing final."""
    output = settings.get("output") or {}
    timing: dict[str, Any] = {
        "fps": _output_fps(settings),
        "width": int(output.get("width", 1920)),
        "height": int(output.get("height", 1080)),
        "transition": str(extras.get("transition") or ""),
        "transition_duration": extras.get("transition_duration"),
        "intro_seconds": extras.get("intro_seconds"),
    }
    for key in ("fps", "width", "height"):
        if extras.get(key) is not None:
            timing[key] = extras[key]
    return timing


def alignment_fingerprint(media: Path | str, lyrics_path: Path | str,
                          profile_name: str, settings: dict,
                          extras: dict) -> str:
    """SHA-256 do estado completo que produz um alinhamento.

    Inclui midia (caminho absoluto, tamanho e mtime), bytes da letra, nome do
    perfil, overrides canonicos e fps/resolucao/transicao/intro relevantes.
    """
    media_path = Path(media).resolve()
    try:
        stat = media_path.stat()
        media_state: dict[str, Any] = {
            "path": str(media_path), "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        media_state = {"path": str(media_path), "size": None, "mtime_ns": None}
    try:
        lyrics_bytes = Path(lyrics_path).read_bytes()
    except OSError:
        lyrics_bytes = b""
    payload = {
        "schema": ALIGNMENT_FINGERPRINT_VERSION,
        "media": media_state,
        "lyrics_sha256": hashlib.sha256(lyrics_bytes).hexdigest(),
        "profile": str(profile_name),
        "overrides": alignment_overrides(settings),
        "timing": _alignment_timing(settings, extras),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _write_alignment_fingerprint(work: Path, fingerprint: str) -> None:
    """Grava a assinatura de forma atomica (tmp + ``os.replace``).

    Uma falha no write/replace nao pode corromper uma assinatura anterior: o
    temporario e gravado no mesmo diretorio e removido em qualquer falha.
    """
    payload = {
        "version": ALIGNMENT_FINGERPRINT_VERSION,
        "fingerprint": fingerprint,
        "hash": fingerprint,
    }
    work = Path(work)
    target = work / ALIGNMENT_FINGERPRINT_FILE
    temp = work / f".{ALIGNMENT_FINGERPRINT_FILE}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        temp.write_text(_canonical_json(payload) + "\n", encoding="utf-8")
        os.replace(temp, target)
    except BaseException:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _read_alignment_fingerprint(work: Path) -> str | None:
    path = Path(work) / ALIGNMENT_FINGERPRINT_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("version", data.get("schema_version"))
    if version != ALIGNMENT_FINGERPRINT_VERSION:
        return None
    value = data.get("fingerprint") or data.get("hash")
    return value if isinstance(value, str) and value else None


def make_style(
    settings: dict,
    cues: list[Cue],
    lyrics_text: str,
    *,
    warnings: list[str] | None = None,
    say: Callable[[str], None] | None = None,
) -> KaraokeStyle:
    k = settings["karaoke"]
    checked = validate_profile_values(settings, warnings=warnings, say=say)["karaoke"]
    margin_h = checked.get("margin_h", k.get("margin_h", 80))
    margin_v = checked.get("margin_v", k.get("margin_v", 280))
    boundaries = (
        stanza_boundaries_from_lyrics(lyrics_text, cues)
        if k.get("stanza_mode") == "stanzas"
        else frozenset()
    )
    return KaraokeStyle(
        font_name=checked.get("font_name", k.get("font_name", FONT_NAME_FALLBACK)),
        font_size=int(checked.get("font_size", k.get("font_size", 84))),
        normal_color=k.get("normal_color", "#000000"),
        active_color=k.get("active_color", "#000000"),
        outline_color=k.get("outline_color", "#000000"),
        back_color=k.get("back_color", "#00000000"),
        bold=bool(k.get("bold", True)),
        outline=float(k.get("outline", 0)),
        shadow=float(k.get("shadow", 0)),
        alignment=int(k.get("alignment", 2)),
        margin_l=int(checked.get("margin_l", margin_h)),
        margin_r=int(checked.get("margin_r", margin_h)),
        margin_v=int(margin_v),
        lines_before=int(k.get("lines_before", 0)),
        lines_after=int(k.get("lines_after", 3)),
        lines_per_slide=int(k.get("lines_per_slide", 0)),
        transition=str(k.get("transition", "scroll")),
        transition_ms=int(k.get("transition_ms", 600)),
        scroll_pause_seconds=float(k.get("scroll_pause_seconds", 2.5)),
        active_font_size=int(k.get("active_font_size", 0)),
        stanza_boundaries=boundaries,
    )


def qc_report(cues: list[Cue], debug_json: Path | None, *, min_vocal: float = 1.2) -> dict:
    """Preflight: canta sem legenda, pausas instrumentais e texto repetido.

    Usa as unidades de transcricao salvas pelo alinhador (quando disponiveis).
    """
    stats: dict[str, Any] = {"cues": len(cues), "qc_available": False}
    unavailable = {"warnings": [QC_UNAVAILABLE_WARNING], "stats": stats}
    if debug_json is None or not Path(debug_json).is_file():
        return unavailable
    try:
        data = json.loads(Path(debug_json).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return unavailable
    if not isinstance(data, dict):
        return unavailable
    units = data.get("transcript_units")
    if not isinstance(units, list) or not units:
        # Debug legivel, mas sem unidades de transcricao: nao ha como avaliar
        # canto sem legenda.  Nao marcar QC como disponivel com zero unidades.
        return unavailable
    stats["qc_available"] = True
    warnings: list[str] = []
    stats["transcript_units"] = len(units)

    bare: list[tuple[float, float, str]] = []
    current: list[dict] = []
    for unit in units:
        try:
            start = float(unit["start"]); end = float(unit["end"])
        except (KeyError, TypeError, ValueError):
            continue
        mid = (start + end) / 2
        covered = any(c.start - 0.12 <= mid <= c.end + 0.12 for c in cues)
        if covered:
            if current and sum(u["end"] - u["start"] for u in current) >= min_vocal:
                a = min(u["start"] for u in current); b = max(u["end"] for u in current)
                bare.append((a, b, " ".join(u["text"].strip() for u in current)))
            current = []
        else:
            current.append({"start": start, "end": end, "text": str(unit.get("text", ""))})
    if current and sum(u["end"] - u["start"] for u in current) >= min_vocal:
        bare.append((min(u["start"] for u in current), max(u["end"] for u in current),
                     " ".join(u["text"].strip() for u in current)))

    for a, b, text in bare:
        warnings.append(
            f"Canto sem legenda entre {a:.1f}s e {b:.1f}s: {text[:90]}"
        )
    stats["uncued_vocal_segments"] = len(bare)

    gaps = []
    for i in range(len(cues) - 1):
        gap = cues[i + 1].start - cues[i].end
        if gap >= 3.0:
            gaps.append((cues[i].end, cues[i + 1].start, gap))
    stats["instrumental_gaps"] = len(gaps)
    for a, b, gap in gaps[:6]:
        warnings.append(f"Pausa instrumental de {gap:.1f}s ({a:.1f}s - {b:.1f}s).")
    return {"warnings": warnings, "stats": stats}


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg falhou")[-2000:])


def probe_duration(path: str | Path) -> float:
    ffprobe = TOOLS / "ffprobe.exe" if (TOOLS / "ffprobe.exe").is_file() else "ffprobe"
    out = subprocess.check_output(
        [str(ffprobe), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", str(path)], text=True)
    return float(out.strip())


def srt_last_end(path: str | Path) -> float:
    text = Path(path).read_text(encoding="utf-8-sig")
    ends = []
    for line in text.splitlines():
        if "-->" in line:
            end = line.split("-->")[1].strip().split()[0]
            h, m, rest = end.split(":")
            s, ms = rest.replace(",", ".").split(".")
            ends.append(int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000)
    return max(ends) if ends else 0.0


def _image_filter(color: str, width: int, height: int, fps: int = 30) -> str:
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={color},"
            f"setsar=1,fps={int(fps)}")


def _encode_common(fps: int = 30) -> list[str]:
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(int(fps)), "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2"]


def _output_fps(settings: dict) -> int:
    """FPS validado do perfil (``settings["output"]["fps"]``), padrao 30.

    Um valor invalido levanta ``ValueError`` (mensagem PT) antes de chegar ao
    ffmpeg; nao ha mais fallback silencioso para 30.
    """
    output = settings.get("output") or {}
    return _validated_fps(output.get("fps", 30))


def make_background(image: Path | None, audio: Path, output: Path, settings: dict) -> None:
    ffmpeg = TOOLS / "ffmpeg.exe" if (TOOLS / "ffmpeg.exe").is_file() else find_ffmpeg()
    command = build_background_command(
        ffmpeg, audio, output,
        color=settings["background"].get("color", "#0B1D3A"),
        image=image,
        width=int(settings["output"]["width"]),
        height=int(settings["output"]["height"]),
        fps=_output_fps(settings),
        preset=settings["burn"].get("preset", "medium"),
        crf=int(settings["burn"].get("crf", 18)),
        fit=settings["background"].get("fit", "contain"),
    )
    _run(command)


def _join_parts(ffmpeg: Path | str, parts: list[Path], work: Path, output: Path,
                extras: dict, common: list[str]) -> None:
    """Une intro / karaoke / logo por xfade ou concatenacao simples."""
    transition = str(extras.get("transition") or "")
    if transition and len(parts) >= 2:
        # Transicoes suaves entre intro / karaoke / logo (xfade + acrossfade).
        durations = [probe_duration(p) for p in parts]
        overlap = max(0.1, min(float(extras.get("transition_duration", 0.4)),
                               min(durations) / 3.0))
        command: list[str] = [ffmpeg, "-y", "-loglevel", "error"]
        for part in parts:
            command += ["-i", str(part)]
        video_chain = (
            f"[0:v][1:v]xfade=transition={transition}:duration={overlap:.2f}:"
            f"offset={durations[0] - overlap:.2f}[v1]"
        )
        audio_chain = f"[0:a][1:a]acrossfade=d={overlap:.2f}[a1]"
        for index in range(2, len(parts)):
            offset = sum(durations[:index]) - overlap * index
            video_chain += (
                f";[v{index - 1}][{index}:v]xfade=transition={transition}:"
                f"duration={overlap:.2f}:offset={offset:.2f}[v{index}]"
            )
            audio_chain += f";[a{index - 1}][{index}:a]acrossfade=d={overlap:.2f}[a{index}]"
        last_v = f"[v{len(parts) - 1}]"
        last_a = f"[a{len(parts) - 1}]"
        command += ["-filter_complex", video_chain + ";" + audio_chain,
                    "-map", last_v, "-map", last_a, *common,
                    "-movflags", "+faststart", str(output)]
        _run(command)
    else:
        listing = work / "concat.txt"
        listing.write_text("".join("file '" + str(p).replace("\\", "/") + "'\n" for p in parts),
                           encoding="utf-8")
        _run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
              "-i", listing, "-c", "copy", "-movflags", "+faststart", output])


def _build_final(source: Path, title: Path, srt: Path, output: Path, work: Path,
                 settings: dict, extras: dict, *,
                 warnings: list[str] | None = None,
                 say: Callable[[str], None] | None = None,
                 _seen: set[str] | None = None) -> list[str]:
    """Monta o video final; devolve avisos (e os repassa a warnings/say).

    ``_seen`` e opcional e deduplica avisos entre as duas chamadas (video
    principal e playback): o mesmo aviso e emitido apenas uma vez.
    """
    notes: list[str] = []
    seen = _seen if _seen is not None else set()

    def _emit(note: str) -> None:
        if note in seen or (warnings is not None and note in warnings):
            return
        seen.add(note)
        notes.append(note)
        if warnings is not None:
            warnings.append(note)
        if say is not None:
            say("AVISO " + note)

    ffmpeg = TOOLS / "ffmpeg.exe" if (TOOLS / "ffmpeg.exe").is_file() else find_ffmpeg()
    width = int(settings["output"]["width"]); height = int(settings["output"]["height"])
    fps = _output_fps(settings)
    duration = probe_duration(source)
    last_end = srt_last_end(srt)
    intro_len = float(extras.get("intro_seconds") or 0)
    logo = extras.get("outro_image_file")
    transition = str(extras.get("transition") or "")
    has_logo = bool(logo) and Path(logo).is_file()
    has_intro = intro_len > 0 and title.is_file()
    common = _encode_common(fps=fps)

    if not has_logo and not has_intro:
        if transition:
            note = "Transicao ignorada: necessario logo ou imagem de titulo."
            _emit(note)
        _run([ffmpeg, "-y", "-loglevel", "error", "-i", source,
              "-c", "copy", "-movflags", "+faststart", output])
        return notes

    outro_start = min(max(last_end + 1.0, intro_len + 0.1), max(duration - 0.1, intro_len + 0.1))
    outro_len = max(float(extras.get("outro_min_seconds", 3.0)), duration - outro_start)
    parts: list[Path] = []
    if has_intro:
        intro = work / "intro.mp4"
        _run([ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(fps),
              "-t", str(intro_len), "-i", title, "-ss", "0", "-t", str(intro_len), "-i", source,
              "-filter_complex", f"[0:v]{_image_filter('0x0B1D3A', width, height, fps)}[v];"
                                 f"[1:a]atrim=0:{intro_len},asetpts=PTS-STARTPTS[a]",
              "-map", "[v]", "-map", "[a]", *common, "-movflags", "+faststart", intro])
        parts.append(intro)
    main_len = max(0.1, (outro_start if has_logo else duration) - intro_len)
    main = work / "main.mp4"
    _run([ffmpeg, "-y", "-loglevel", "error", "-ss", str(intro_len), "-i", source,
          "-t", str(main_len), "-vf", _image_filter("0x0B1D3A", width, height, fps),
          "-map", "0:v:0", "-map", "0:a:0", *common, "-movflags", "+faststart", main])
    parts.append(main)
    if has_logo:
        outro = work / "outro.mp4"
        fade = max(0.0, outro_len - 1.0)
        _run([ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(fps),
              "-t", str(outro_len), "-i", logo, "-ss", str(outro_start), "-i", source,
              "-filter_complex", f"[0:v]{_image_filter('0xFFF3E8', width, height, fps)}[v];"
                                 f"[1:a]atrim=0:{outro_len},asetpts=PTS-STARTPTS,apad,"
                                 f"atrim=0:{outro_len},afade=t=out:st={fade}:d=1[a]",
              "-map", "[v]", "-map", "[a]", *common, "-movflags", "+faststart", outro])
        parts.append(outro)
    _join_parts(ffmpeg, parts, work, output, extras, common)
    return notes


def run_project(
    *,
    media: Path,
    lyrics_path: Path,
    output: Path,
    work: Path,
    profile_name: str,
    playback: Path | None = None,
    title_image: Path | None = None,
    logo_image: Path | None = None,
    background_image: Path | None = None,
    progress: Callable[[str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    reuse_alignment: bool = True,
    transition: str | None = None,
    transition_duration: float | None = None,
    make_playback_from_media: bool = False,
    fps: int | None = None,
) -> dict:
    warnings: list[str] = []
    report = {"profile": profile_name, "warnings": warnings, "outputs": [], "stats": {}}
    base_say = progress or (lambda _m: None)
    _said: set[str] = set()

    def say(message: str) -> None:
        """Repassa o progresso sem repetir avisos ja emitidos nesta execucao."""
        if message.startswith("AVISO") and message in _said:
            return
        _said.add(message)
        base_say(message)

    _seen_notes: set[str] = set()
    if cancel and cancel():
        return report

    profile = get_profile(profile_name)
    settings = profile_settings(profile)
    if fps is not None:
        # Override vindo da CLI (``--fps``); sem ele o perfil/settings manda.
        settings.setdefault("output", {})["fps"] = int(fps)
    # Preflight do perfil: nenhum ffmpeg/aligner/ASS e iniciado com valores
    # invalidos, e o fallback de fonte e informado em warnings/say.
    checked = validate_profile_values(settings, warnings=warnings, say=say)
    if "font_name" in checked["karaoke"]:
        settings["karaoke"]["font_name"] = checked["karaoke"]["font_name"]
    overrides = alignment_overrides(settings)
    extras = dict(profile.get("extras") or {})
    if transition is not None:
        extras["transition"] = transition
    if transition_duration is not None:
        extras["transition_duration"] = transition_duration
    if logo_image:
        extras["outro_image_file"] = str(logo_image)
    if background_image:
        settings["background"]["image_filename"] = background_image.name
        settings["background"]["type"] = "image"
    elif not settings["background"].get("image_filename"):
        settings["background"]["type"] = "color"

    work.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    srt = work / "lyrics.srt"
    debug = work / "alignment-debug.json"
    lyrics_text = lyrics_path.read_text(encoding="utf-8-sig")

    fingerprint = alignment_fingerprint(media, lyrics_path, profile_name, settings, extras)
    stored_fingerprint = _read_alignment_fingerprint(work)
    has_srt = srt.is_file() and srt.stat().st_size > 0
    if reuse_alignment and has_srt and stored_fingerprint == fingerprint:
        cues = parse_srt(srt.read_text(encoding="utf-8-sig"))
        say(f"Alinhamento reaproveitado ({len(cues)} versos).")
    else:
        if has_srt:
            note = ("Cache sem assinatura; realinhando." if stored_fingerprint is None
                    else "Cache invalido (midia/letra/perfil mudou); realinhando.")
            warnings.append(note)
            say("AVISO " + note)
        say("Alinhando com Whisper + Demucs…")
        from .services.align import generate_alignment

        # O servico passa a ser o unico ponto de geracao de alinhamento.  Os
        # caminhos historicos (work/lyrics.srt, work/alignment-debug.json) sao
        # preservados; a assinatura e gravada junto com o novo alinhamento.
        generated = generate_alignment(
            project_dir=work.parent,
            media=media,
            lyrics_text=lyrics_text,
            profile_name=profile_name,
            output=srt,
            work=work,
            overrides=overrides,
            progress=say,
            cancel_requested=cancel,
            strict_qc=False,
        )
        _write_alignment_fingerprint(work, fingerprint)
        for warning in generated.get("warnings", []):
            say("AVISO ALINHAMENTO: " + warning)
        cues = parse_srt(srt.read_text(encoding="utf-8-sig"))
        say(f"Alinhado: {len(cues)} versos.")
    if cancel and cancel():
        return report

    if playback is None and make_playback_from_media:
        from .audiofx import separate_audio
        say("Criando playback instrumental (Demucs)…")
        stems = separate_audio(media, work / "stems", progress=say)
        playback = stems["instrumental"]

    qc = qc_report(cues, debug)
    report["stats"] = qc["stats"]
    for warning in qc["warnings"]:
        warnings.append(warning)
        say("AVISO QC: " + warning)

    play_res_x = int(settings["output"]["width"])
    play_res_y = int(settings["output"]["height"])
    style = make_style(settings, cues, lyrics_text, warnings=warnings, say=say)
    ass = work / "lyrics.karaoke.ass"
    ass.write_text(render_karaoke_ass(cues, title=media.stem, style=style,
                                      play_res_x=play_res_x, play_res_y=play_res_y),
                   encoding="utf-8")

    bg = work / "background.mp4"
    bg_image = background_image or (
        work.parent / "fundo" / settings["background"]["image_filename"]
        if settings["background"].get("image_filename") else None
    )
    say("Criando fundo…")
    ffmpeg = TOOLS / "ffmpeg.exe" if (TOOLS / "ffmpeg.exe").is_file() else find_ffmpeg()
    make_background(bg_image, media, bg, settings)
    if cancel and cancel():
        return report
    say("Queimando legendas…")
    burnt = work / "cantado_karaoke.mp4"
    with burn_guard(work, progress=say):
        burn_subtitles(bg, ass, burnt, ffmpeg=ffmpeg,
                       preset=settings["burn"]["preset"], crf=int(settings["burn"]["crf"]))
    say("Aplicando intro e logo…")
    _build_final(burnt, title_image or Path(""), srt, output, work, settings, extras,
                 warnings=warnings, say=say, _seen=_seen_notes)
    report["outputs"].append(str(output))

    if playback is not None:
        say("Sincronizando playback…")
        duration = probe_duration(playback)
        time_map = build_audio_time_map(media, playback, anchor_times=[c.start for c in cues])
        pb_cues = time_map.map_cues(cues, target_duration=duration)
        pb_srt = work / "lyrics_playback.srt"
        pb_srt.write_text(render_srt(pb_cues), encoding="utf-8-sig")
        pb_ass = work / "lyrics_playback.karaoke.ass"
        pb_style = make_style(settings, pb_cues, lyrics_text, warnings=warnings, say=say)
        pb_ass.write_text(render_karaoke_ass(pb_cues, title=playback.stem, style=pb_style,
                                             play_res_x=play_res_x, play_res_y=play_res_y),
                          encoding="utf-8")
        pb_bg = work / "background_playback.mp4"
        make_background(bg_image, playback, pb_bg, settings)
        pb_burnt = work / "playback_karaoke.mp4"
        with burn_guard(work, progress=say):
            burn_subtitles(pb_bg, pb_ass, pb_burnt, ffmpeg=ffmpeg,
                           preset=settings["burn"]["preset"], crf=int(settings["burn"]["crf"]))
        pb_out = output.with_name(output.stem + " - playback.mp4")
        _build_final(pb_burnt, title_image or Path(""), pb_srt, pb_out, work, settings, extras,
                     warnings=warnings, say=say, _seen=_seen_notes)
        report["outputs"].append(str(pb_out))

    # Um mesmo aviso pode ser gerado por mais de uma etapa (validacao do
    # perfil, QC, playback); qc.json deve ser deterministico e sem duplicatas.
    warnings[:] = list(dict.fromkeys(warnings))
    (work / "qc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    say("Concluido.")
    return report
