from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .karaoke import (
    KaraokeStyle,
    render_karaoke_ass,
    stanza_boundaries_from_lyrics,
)
from .karaoke_jobs import load_profiles, run_project
from .scene import compile_scene, load_scene, validate_scene
from .audiofx import analyze, apply_audio_fx
from .media import waveform_peaks
from .pipeline import align_to_srt, demo_to_srt
from .publish import default_output_dir, publish
from .srt import parse_srt
from .subtitles import AssStyle

def _add_alignment_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--max-chars", type=int, default=30)
    command.add_argument("--auto-segment", action="store_true")
    command.add_argument(
        "--language",
        default="auto",
        help="dominant spoken language code/name, or 'auto' (default)",
    )
    command.add_argument("--model", default="small")
    command.add_argument("--no-separate", action="store_true")
    command.add_argument(
        "--partial-range",
        action="store_true",
        help="locate and align only the lyric span heard in this audio range",
    )
    command.add_argument("--lead-ms", type=int, default=600)
    command.add_argument("--offset-seconds", type=float, default=0.0)
    command.add_argument("--range-start", type=float, default=0.0)
    command.add_argument("--range-end", type=float)
    command.add_argument("--timeline-anchor", action="store_true")
    command.add_argument("--end-pad-ms", type=int, default=500)
    command.add_argument("--min-duration-ms", type=int, default=500)
    command.add_argument("--max-duration-ms", type=int, default=10000)
    command.add_argument("--min-gap-ms", type=int, default=80)
    command.add_argument("--debug-json", type=Path)


def _add_ass_style_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--font", help="font family for ASS and burned lyrics")
    command.add_argument("--font-size", type=int, help="font size at 1080p")
    command.add_argument("--primary-color", help="text color as #RRGGBB")
    command.add_argument("--outline-color", help="outline color as #RRGGBB")
    command.add_argument("--back-color", help="shadow color as #RRGGBB or #RRGGBBAA")
    command.add_argument("--outline", type=float, help="outline thickness")
    command.add_argument("--shadow", type=float, help="shadow offset")
    command.add_argument(
        "--alignment",
        type=int,
        choices=range(1, 10),
        help="ASS numpad alignment (2 = bottom center)",
    )
    command.add_argument("--margin-h", type=int, help="left/right margin")
    command.add_argument("--margin-v", type=int, help="vertical margin")
    command.add_argument("--bold", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lyrics-aligner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    waveform = subparsers.add_parser("waveform")
    waveform.add_argument("media", type=Path)
    waveform.add_argument("--bins", type=int, default=850)

    profiles_command = subparsers.add_parser(
        "profiles", help="list saved karaoke profiles (pre-sets)"
    )
    profiles_command.add_argument(
        "name", nargs="?", help="show the full JSON of one profile"
    )

    audio_command = subparsers.add_parser(
        "audio", help="audio tools: instrumental, voice volume, denoise, enhance"
    )
    audio_command.add_argument("media", type=Path)
    audio_command.add_argument("-o", "--output", type=Path, required=True)
    audio_command.add_argument("--work", type=Path)
    audio_command.add_argument("--mode", choices=("mix", "instrumental", "voice"),
                               default="mix")
    audio_command.add_argument("--voice-gain", type=float, default=1.0)
    audio_command.add_argument("--music-gain", type=float, default=1.0)
    audio_command.add_argument("--denoise", type=float, default=0.0)
    audio_command.add_argument("--enhance", choices=("voz", "musica"))
    audio_command.add_argument("--normalize", action="store_true")
    audio_command.add_argument("--analyze", action="store_true",
                               help="only print audio metrics")

    make_command = subparsers.add_parser(
        "make", help="generate a full karaoke MP4 using a saved profile"
    )
    make_command.add_argument("media", type=Path)
    make_command.add_argument("lyrics", type=Path)
    make_command.add_argument("-o", "--output", type=Path, required=True)
    make_command.add_argument("--profile", required=True, help="profile name")
    make_command.add_argument("--playback", type=Path)
    make_command.add_argument("--title-image", type=Path)
    make_command.add_argument("--logo", type=Path)
    make_command.add_argument("--background", type=Path)
    make_command.add_argument("--work", type=Path, help="work directory (default: beside output)")
    make_command.add_argument("--realign", action="store_true")
    make_command.add_argument("--scene", type=Path, help="JSON scene with image/text layers")
    make_command.add_argument("--transition", choices=("fade", "dissolve", "wipeleft",
                                                       "slideright", "smoothup"),
                              help="xfade transition between intro/karaoke/logo")
    make_command.add_argument("--transition-duration", type=float, default=0.4)
    make_command.add_argument("--make-playback", action="store_true",
                              help="derive instrumental playback with Demucs")

    for name in ("align", "demo"):
        command = subparsers.add_parser(name)
        command.add_argument("audio", type=Path)
        command.add_argument("lyrics", type=Path)
        command.add_argument("-o", "--output", type=Path, required=True)
        if name == "demo":
            command.add_argument("--max-chars", type=int, default=30)

    align = subparsers.choices["align"]
    _add_alignment_options(align)
    align.add_argument(
        "--work-dir",
        type=Path,
        help="caller-owned temporary directory (removed by the caller)",
    )

    publish_command = subparsers.add_parser(
        "publish",
        help="align lyrics and create an editor-ready SRT/ASS package",
    )
    publish_command.add_argument("media", type=Path)
    publish_command.add_argument("lyrics", type=Path)
    publish_command.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="package folder (default: <media>_lyrics beside the source)",
    )
    _add_alignment_options(publish_command)
    publish_command.add_argument("--title", help="title stored in the ASS and manifest")
    _add_ass_style_options(publish_command)
    publish_command.add_argument(
        "--burn",
        action="store_true",
        help="also render video_with_lyrics.mp4 with FFmpeg",
    )
    publish_command.add_argument(
        "--ffmpeg",
        type=Path,
        help="FFmpeg executable path (auto-detected when omitted)",
    )
    publish_command.add_argument(
        "--preset",
        default="medium",
        choices=(
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
            "slow",
            "slower",
            "veryslow",
        ),
        help="x264 preset used by --burn (default: medium)",
    )
    publish_command.add_argument(
        "--crf",
        type=int,
        default=18,
        help="x264 quality from 0 (best) to 51 (smallest), default: 18",
    )

    karaoke_command = subparsers.add_parser(
        "karaoke",
        help="render a progressive karaoke ASS from an SRT and the source lyrics",
    )
    karaoke_command.add_argument("srt", type=Path, help="aligned lyrics SRT")
    karaoke_command.add_argument("lyrics", type=Path, help="original lyrics (with blank-line stanzas)")
    karaoke_command.add_argument("-o", "--output", type=Path, required=True)
    karaoke_command.add_argument("--profile", help="apply a saved karaoke profile")
    karaoke_command.add_argument(
        "--stanza-mode",
        choices=("time", "stanzas"),
        default="time",
        help=(
            "time: verses up to 6 s apart share a slide; "
            "stanzas: blank lines in the lyrics split stanzas (6 s still applies)"
        ),
    )
    karaoke_command.add_argument("--lines-per-slide", type=int, default=3)
    karaoke_command.add_argument("--lines-before", type=int, default=1)
    karaoke_command.add_argument("--lines-after", type=int, default=1)
    karaoke_command.add_argument("--entry-lead-ms", type=int, default=200)
    karaoke_command.add_argument("--active-font-size", type=int, default=0)
    karaoke_command.add_argument("--scroll-pause-seconds", type=float, default=2.0)
    karaoke_command.add_argument(
        "--transition",
        default="fade",
        choices=("none", "scroll", "fade", "up", "down"),
    )
    karaoke_command.add_argument("--transition-ms", type=int, default=400)
    _add_ass_style_options(karaoke_command)
    return parser


def _ass_style(args: argparse.Namespace) -> AssStyle:
    defaults = AssStyle()
    return AssStyle(
        font_name=args.font if args.font is not None else defaults.font_name,
        font_size=(
            args.font_size if args.font_size is not None else defaults.font_size
        ),
        primary_color=(
            args.primary_color
            if args.primary_color is not None
            else defaults.primary_color
        ),
        outline_color=(
            args.outline_color
            if args.outline_color is not None
            else defaults.outline_color
        ),
        back_color=(
            args.back_color if args.back_color is not None else defaults.back_color
        ),
        bold=args.bold,
        outline=args.outline if args.outline is not None else defaults.outline,
        shadow=args.shadow if args.shadow is not None else defaults.shadow,
        alignment=(
            args.alignment if args.alignment is not None else defaults.alignment
        ),
        margin_l=args.margin_h if args.margin_h is not None else defaults.margin_l,
        margin_r=args.margin_h if args.margin_h is not None else defaults.margin_r,
        margin_v=args.margin_v if args.margin_v is not None else defaults.margin_v,
    )


def _run_publish(args: argparse.Namespace) -> int:
    lyrics_text = args.lyrics.read_text(encoding="utf-8-sig")
    output_dir = args.output_dir or default_output_dir(args.media)
    messages: list[str] = []
    result = publish(
        args.media,
        lyrics_text,
        output_dir,
        lyrics_path=args.lyrics,
        title=args.title,
        style=_ass_style(args),
        burn=args.burn,
        ffmpeg=args.ffmpeg,
        preset=args.preset,
        crf=args.crf,
        align_options={
            "max_chars": args.max_chars,
            "auto_segment": args.auto_segment,
            "language": args.language,
            "separate": not args.no_separate,
            "partial_range": args.partial_range,
            "model_name": args.model,
            "lead_ms": args.lead_ms,
            "offset_seconds": args.offset_seconds,
            "range_start_seconds": args.range_start,
            "range_end_seconds": args.range_end,
            "timeline_anchor": args.timeline_anchor,
            "end_pad_ms": args.end_pad_ms,
            "min_duration_ms": args.min_duration_ms,
            "max_duration_ms": args.max_duration_ms,
            "min_gap_ms": args.min_gap_ms,
            "debug_json": args.debug_json,
            "messages": messages,
        },
        progress=lambda message: print(message, flush=True),
    )
    for message in messages:
        print(f"Note: {message}")
    print(f"Aligned {len(result.cues)} cues.")
    print(f"  SRT: {result.srt_path}")
    print(f"  ASS: {result.ass_path}")
    print(f"  Manifest: {result.manifest_path}")
    if result.video_path is not None:
        print(f"  Video: {result.video_path}")
    return 0


def _run_karaoke(args: argparse.Namespace) -> int:
    cues = parse_srt(args.srt.read_text(encoding="utf-8-sig"))
    if not cues:
        raise ValueError("O SRT não contém legendas.")
    lyrics_text = args.lyrics.read_text(encoding="utf-8-sig")
    profile = None
    if getattr(args, "profile", None):
        profile = load_profiles().get("profiles", {}).get(args.profile)
        if profile is None:
            raise ValueError(
                f"Perfil '{args.profile}' nao encontrado. "
                "Use 'lyrics-aligner profiles' para listar."
            )
    kp = (profile or {}).get("settings", {}).get("karaoke", {})

    def pick(key: str, default):
        return kp.get(key, default)

    base = _ass_style(args)
    stanza_mode = pick("stanza_mode", args.stanza_mode)
    boundaries = (
        stanza_boundaries_from_lyrics(lyrics_text, cues)
        if stanza_mode == "stanzas"
        else frozenset()
    )
    style = KaraokeStyle(
        font_name=pick("font_name", base.font_name),
        font_size=pick("font_size", base.font_size),
        normal_color=pick("normal_color", base.primary_color),
        active_color=pick("active_color", base.primary_color),
        outline_color=pick("outline_color", base.outline_color),
        back_color=pick("back_color", base.back_color),
        bold=pick("bold", base.bold),
        outline=pick("outline", base.outline),
        shadow=pick("shadow", base.shadow),
        alignment=pick("alignment", base.alignment),
        margin_l=pick("margin_h", base.margin_l),
        margin_r=pick("margin_h", base.margin_r),
        margin_v=pick("margin_v", base.margin_v),
        lines_per_slide=pick("lines_per_slide", args.lines_per_slide),
        lines_before=pick("lines_before", args.lines_before),
        lines_after=pick("lines_after", args.lines_after),
        entry_lead_ms=args.entry_lead_ms,
        active_font_size=pick("active_font_size", args.active_font_size),
        scroll_pause_seconds=pick("scroll_pause_seconds", args.scroll_pause_seconds),
        transition=pick("transition", args.transition),
        transition_ms=pick("transition_ms", args.transition_ms),
        stanza_boundaries=boundaries,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        render_karaoke_ass(cues, title=args.srt.stem, style=style),
        encoding="utf-8",
    )
    if profile:
        print(f"Perfil: {profile.get('name', args.profile)}")
    print(f"Karaoke ASS: {args.output}")
    print(f"Modo de estrofes: {stanza_mode} ({len(boundaries)} quebra(s))")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "karaoke":
            return _run_karaoke(args)
        if args.command == "audio":
            if args.analyze:
                print(json.dumps(analyze(args.media), ensure_ascii=False, indent=2))
                return 0
            work = args.work or (args.output.parent / "trabalho" / "audio")
            fx: dict[str, Any] = {
                "voice_gain": args.voice_gain,
                "music_gain": args.music_gain,
                "instrumental": args.mode == "instrumental",
            }
            if args.denoise > 0:
                fx["denoise"] = args.denoise
            if args.enhance:
                fx["enhance"] = True
                fx["enhance_preset"] = args.enhance
            if args.normalize:
                fx["normalize"] = True
            result = apply_audio_fx(args.media, args.output, fx, work,
                                    progress=lambda m: print(m, flush=True))
            print(json.dumps({"output": str(result), "analysis": analyze(result)},
                             ensure_ascii=False, indent=2))
            return 0
        if args.command == "make":
            work = args.work or (args.output.parent / "trabalho" / "cli")
            report = run_project(
                media=args.media, lyrics_path=args.lyrics, output=args.output,
                work=work, profile_name=args.profile, playback=args.playback,
                title_image=args.title_image, logo_image=args.logo,
                background_image=args.background,
                progress=lambda message: print(message, flush=True),
                reuse_alignment=not args.realign,
                transition=args.transition,
                transition_duration=args.transition_duration,
                make_playback_from_media=args.make_playback,
            )
            if args.scene:
                scene = load_scene(args.scene)
                warnings = validate_scene(scene)
                for warning in warnings:
                    print("AVISO CENA: " + warning)
                    report.setdefault("warnings", []).append(warning)
                for item in list(report.get("outputs", [])):
                    source = Path(item)
                    compiled = source.with_name(source.stem + " - cena.mp4")
                    compile_scene(scene=scene, video=source, output=compiled, work=work)
                    report["outputs"].append(str(compiled))
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if args.command == "profiles":
            payload = load_profiles()
            if args.name:
                profile = payload.get("profiles", {}).get(args.name)
                if profile is None:
                    raise ValueError(f"Perfil '{args.name}' nao encontrado.")
                print(json.dumps(profile, ensure_ascii=False, indent=2))
                return 0
            print("Perfis de karaoke (pre-sets):")
            for name, profile in payload.get("profiles", {}).items():
                marker = " *" if name == payload.get("active") else ""
                print(f"  {name}{marker} - {profile.get('name', name)}")
                print(f"      {profile.get('description', '')}")
            return 0
        if args.command == "waveform":
            duration, peaks = waveform_peaks(args.media, bins=args.bins)
            print(json.dumps({"duration": duration, "peaks": peaks}, separators=(",", ":")))
            return 0
        if args.command == "publish":
            return _run_publish(args)
        lyrics_text = args.lyrics.read_text(encoding="utf-8-sig")
        messages: list[str] = []
        if args.command == "demo":
            cues = demo_to_srt(
                args.audio,
                lyrics_text,
                args.output,
                max_chars=args.max_chars,
            )
        else:
            cues = align_to_srt(
                args.audio,
                lyrics_text,
                args.output,
                max_chars=args.max_chars,
                auto_segment=args.auto_segment,
                language=args.language,
                separate=not args.no_separate,
                partial_range=args.partial_range,
                model_name=args.model,
                lead_ms=args.lead_ms,
                offset_seconds=args.offset_seconds,
                range_start_seconds=args.range_start,
                range_end_seconds=args.range_end,
                timeline_anchor=args.timeline_anchor,
                end_pad_ms=args.end_pad_ms,
                min_duration_ms=args.min_duration_ms,
                max_duration_ms=args.max_duration_ms,
                min_gap_ms=args.min_gap_ms,
                debug_json=args.debug_json,
                messages=messages,
                progress=lambda message: print(message, flush=True),
                work_dir=args.work_dir,
            )
            for message in messages:
                print(f"Note: {message}")
    except Exception as exc:
        print(f"lyrics-aligner: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {len(cues)} cues to {args.output}")
    return 0