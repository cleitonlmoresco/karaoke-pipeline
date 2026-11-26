"""Pipeline central de karaoke: perfis, alinhamento, QC, ASS, video e intro/outro.

Usado pelo CLI (comando ``make``) e pelo painel Tk.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

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


def load_profiles() -> dict:
    try:
        data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        if isinstance(data.get("profiles"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"active": None, "profiles": {}}


def save_profiles(payload: dict) -> None:
    PROFILES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def get_profile(name: str) -> dict:
    data = load_profiles()
    profile = data.get("profiles", {}).get(name)
    if profile is None:
        raise ValueError(f"Perfil '{name}' nao encontrado. Use 'profiles' para listar.")
    return profile


def profile_settings(profile: dict) -> dict:
    raw = profile.get("settings") or {}
    return {
        "alignment": {**DEFAULT_ALIGNMENT, **(raw.get("alignment") or {})},
        "karaoke": {**DEFAULT_KARAOKE, **(raw.get("karaoke") or {})},
        "background": {**DEFAULT_BACKGROUND, **(raw.get("background") or {})},
        "output": {"width": 1920, "height": 1080, **(raw.get("output") or {})},
        "burn": {"preset": "medium", "crf": 18, **(raw.get("burn") or {})},
    }


def make_style(settings: dict, cues: list[Cue], lyrics_text: str) -> KaraokeStyle:
    k = settings["karaoke"]
    boundaries = (
        stanza_boundaries_from_lyrics(lyrics_text, cues)
        if k.get("stanza_mode") == "stanzas"
        else frozenset()
    )
    return KaraokeStyle(
        font_name=k.get("font_name", "Arial"),
        font_size=int(k.get("font_size", 84)),
        normal_color=k.get("normal_color", "#000000"),
        active_color=k.get("active_color", "#000000"),
        outline_color=k.get("outline_color", "#000000"),
        back_color=k.get("back_color", "#00000000"),
        bold=bool(k.get("bold", True)),
        outline=float(k.get("outline", 0)),
        shadow=float(k.get("shadow", 0)),
        alignment=int(k.get("alignment", 2)),
        margin_l=int(k.get("margin_h", 80)),
        margin_r=int(k.get("margin_h", 80)),
        margin_v=int(k.get("margin_v", 280)),
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
    warnings: list[str] = []
    stats: dict[str, Any] = {"cues": len(cues)}
    if debug_json is None or not Path(debug_json).is_file():
        return {"warnings": warnings, "stats": stats}
    try:
        data = json.loads(Path(debug_json).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"warnings": warnings, "stats": stats}
    units = data.get("transcript_units") or []
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


def _image_filter(color: str, width: int, height: int) -> str:
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={color},"
            f"setsar=1,fps=30")


def _encode_common() -> list[str]:
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "192k",
            "-ar", "44100", "-ac", "2"]


def make_background(image: Path | None, audio: Path, output: Path, settings: dict) -> None:
    ffmpeg = TOOLS / "ffmpeg.exe" if (TOOLS / "ffmpeg.exe").is_file() else find_ffmpeg()
    command = build_background_command(
        ffmpeg, audio, output,
        color=settings["background"].get("color", "#0B1D3A"),
        image=image,
        width=int(settings["output"]["width"]),
        height=int(settings["output"]["height"]),
        preset=settings["burn"].get("preset", "medium"),
        crf=int(settings["burn"].get("crf", 18)),
        fit=settings["background"].get("fit", "contain"),
    )
    _run(command)


def _build_final(source: Path, title: Path, srt: Path, output: Path, work: Path,
                 settings: dict, extras: dict) -> None:
    ffmpeg = TOOLS / "ffmpeg.exe" if (TOOLS / "ffmpeg.exe").is_file() else find_ffmpeg()
    width = int(settings["output"]["width"]); height = int(settings["output"]["height"])
    duration = probe_duration(source)
    last_end = srt_last_end(srt)
    intro_len = float(extras.get("intro_seconds") or 0)
    logo = extras.get("outro_image_file")
    common = _encode_common()

    if not logo or not Path(logo).is_file():
        _run([ffmpeg, "-y", "-loglevel", "error", "-i", source,
              "-c", "copy", "-movflags", "+faststart", output])
        return
    outro_start = min(max(last_end + 1.0, intro_len + 0.1), max(duration - 0.1, intro_len + 0.1))
    outro_len = max(float(extras.get("outro_min_seconds", 3.0)), duration - outro_start)
    parts: list[Path] = []
    if intro_len > 0 and title.is_file():
        intro = work / "intro.mp4"
        _run([ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30",
              "-t", str(intro_len), "-i", title, "-ss", "0", "-t", str(intro_len), "-i", source,
              "-filter_complex", f"[0:v]{_image_filter('0x0B1D3A', width, height)}[v];"
                                 f"[1:a]atrim=0:{intro_len},asetpts=PTS-STARTPTS[a]",
              "-map", "[v]", "-map", "[a]", *common, "-movflags", "+faststart", intro])
        parts.append(intro)
    main_len = max(0.1, outro_start - intro_len)
    main = work / "main.mp4"
    _run([ffmpeg, "-y", "-loglevel", "error", "-ss", str(intro_len), "-i", source,
          "-t", str(main_len), "-vf", _image_filter("0x0B1D3A", width, height),
          "-map", "0:v:0", "-map", "0:a:0", *common, "-movflags", "+faststart", main])
    parts.append(main)
    outro = work / "outro.mp4"
    fade = max(0.0, outro_len - 1.0)
    _run([ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30",
          "-t", str(outro_len), "-i", logo, "-ss", str(outro_start), "-i", source,
          "-filter_complex", f"[0:v]{_image_filter('0xFFF3E8', width, height)}[v];"
                             f"[1:a]atrim=0:{outro_len},asetpts=PTS-STARTPTS,apad,"
                             f"atrim=0:{outro_len},afade=t=out:st={fade}:d=1[a]",
          "-map", "[v]", "-map", "[a]", *common, "-movflags", "+faststart", outro])
    parts.append(outro)
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
) -> dict:
    report = {"profile": profile_name, "warnings": [], "outputs": [], "stats": {}}
    say = progress or (lambda _m: None)
    if cancel and cancel():
        return report

    profile = get_profile(profile_name)
    settings = profile_settings(profile)
    align = settings["alignment"]
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

    if reuse_alignment and srt.is_file() and srt.stat().st_size > 0:
        cues = parse_srt(srt.read_text(encoding="utf-8-sig"))
        say(f"Alinhamento reaproveitado ({len(cues)} versos).")
    else:
        say("Alinhando com Whisper + Demucs…")
        messages: list[str] = []
        cues = align_to_srt(
            media, lyrics_text, srt,
            max_chars=int(align["max_chars"]),
            auto_segment=align["mode"] != "manual",
            language=align["language"], separate=bool(align["separate"]),
            model_name=align["model"], lead_ms=int(align["lead_ms"]),
            end_pad_ms=int(align["end_pad_ms"]),
            min_duration_ms=int(align["min_duration_ms"]),
            max_duration_ms=int(align["max_duration_ms"]),
            min_gap_ms=int(align["min_gap_ms"]),
            debug_json=debug, messages=messages,
            progress=lambda m: say(m),
        )
        say(f"Alinhado: {len(cues)} versos.")
    if cancel and cancel():
        return report

    if playback is None and make_playback_from_media:
        from .audiofx import separate_audio
        say("Criando playback instrumental (Demucs)…")
        stems = separate_audio(media, work / "stems", progress=say)
        playback = stems["instrumental"]

    qc = qc_report(cues, debug)
    report["warnings"] = qc["warnings"]
    report["stats"] = qc["stats"]
    for warning in qc["warnings"]:
        say("AVISO QC: " + warning)

    style = make_style(settings, cues, lyrics_text)
    ass = work / "lyrics.karaoke.ass"
    ass.write_text(render_karaoke_ass(cues, title=media.stem, style=style), encoding="utf-8")

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
    burn_subtitles(bg, ass, burnt, ffmpeg=ffmpeg,
                   preset=settings["burn"]["preset"], crf=int(settings["burn"]["crf"]))
    say("Aplicando intro e logo…")
    _build_final(burnt, title_image or Path(""), srt, output, work, settings, extras)
    report["outputs"].append(str(output))

    if playback is not None:
        say("Sincronizando playback…")
        duration = probe_duration(playback)
        time_map = build_audio_time_map(media, playback, anchor_times=[c.start for c in cues])
        pb_cues = time_map.map_cues(cues, target_duration=duration)
        pb_srt = work / "lyrics_playback.srt"
        pb_srt.write_text(render_srt(pb_cues), encoding="utf-8-sig")
        pb_ass = work / "lyrics_playback.karaoke.ass"
        pb_style = make_style(settings, pb_cues, lyrics_text)
        pb_ass.write_text(render_karaoke_ass(pb_cues, title=playback.stem, style=pb_style),
                          encoding="utf-8")
        pb_bg = work / "background_playback.mp4"
        make_background(bg_image, playback, pb_bg, settings)
        pb_burnt = work / "playback_karaoke.mp4"
        burn_subtitles(pb_bg, pb_ass, pb_burnt, ffmpeg=ffmpeg,
                       preset=settings["burn"]["preset"], crf=int(settings["burn"]["crf"]))
        pb_out = output.with_name(output.stem + " - playback.mp4")
        _build_final(pb_burnt, title_image or Path(""), pb_srt, pb_out, work, settings, extras)
        report["outputs"].append(str(pb_out))

    (work / "qc.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    say("Concluido.")
    return report
