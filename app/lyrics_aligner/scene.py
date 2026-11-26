"""Composicao visual declarativa: camadas de imagem/texto sobre o video base.

Um arquivo scene.json descreve camadas com posicao, tamanho, tempo de
entrada/saida e estilo. O compilador gera um filter_complex do FFmpeg para
imagens e um ASS para textos.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .subtitles import ass_color, format_ass_timestamp

TOOLS = Path(__file__).resolve().parents[2] / "tools"
FFMPEG = TOOLS / "ffmpeg.exe" if (TOOLS / "ffmpeg.exe").is_file() else "ffmpeg"

EMPTY_SCENE: dict[str, Any] = {"version": 1, "width": 1920, "height": 1080, "layers": []}

# Presets: (largura, altura, margem de area segura em % da menor dimensao)
RESOLUTION_PRESETS: dict[str, tuple[int, int, float]] = {
    "1920x1080": (1920, 1080, 4.0),
    "1280x720": (1280, 720, 4.0),
    "3840x2160": (3840, 2160, 4.0),
    "1080x1920": (1080, 1920, 6.0),
    "1080x1080": (1080, 1080, 6.0),
}


def preset_size(name: str) -> tuple[int, int, float]:
    return RESOLUTION_PRESETS.get(name, (1920, 1080, 4.0))


def safe_bounds(scene: dict) -> tuple[float, float, float, float]:
    """Retangulo de area segura em pixels para a resolucao da cena."""
    width = int(scene.get("width", 1920)); height = int(scene.get("height", 1080))
    margin = float(scene.get("safe_margin", preset_size(str(scene.get("preset", "")))[2]))
    inset = min(width, height) * margin / 100.0
    return inset, inset, width - inset, height - inset


def load_scene(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("layers"), list):
        raise ValueError("scene.json deve conter uma lista 'layers'.")
    return data


def save_scene(path: str | Path, scene: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scene, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_scene(scene: dict) -> list[str]:
    """Avisos de cena: limites, ordem e sobreposicoes com area segura."""
    warnings: list[str] = []
    width = int(scene.get("width", 1920)); height = int(scene.get("height", 1080))
    sx1, sy1, sx2, sy2 = safe_bounds(scene)
    for index, layer in enumerate(scene.get("layers", []), 1):
        kind = layer.get("type", "image")
        start = float(layer.get("start", 0)); end = layer.get("end")
        if end is not None and float(end) <= start:
            warnings.append(f"Camada {index}: fim ({end}) anterior ao inicio ({start}).")
        if kind == "image":
            path = Path(str(layer.get("file") or ""))
            if not path.is_file():
                warnings.append(f"Camada {index}: arquivo nao encontrado: {path}")
            x = float(layer.get("x", 0)); y = float(layer.get("y", 0))
            w = float(layer.get("w", width)); h = float(layer.get("h", height))
            if x < sx1 or y < sy1 or x + w > sx2 or y + h > sy2:
                warnings.append(
                    f"Camada {index}: fora da area segura "
                    f"(x={x:.0f}, y={y:.0f}, {w:.0f}x{h:.0f}; "
                    f"seguro {sx1:.0f},{sy1:.0f}-{sx2:.0f},{sy2:.0f})."
                )
        if kind == "text" and not str(layer.get("text", "")).strip():
            warnings.append(f"Camada {index}: texto vazio.")
    return warnings


def _ass_for_text_layers(layers: list[dict], width: int, height: int) -> str:
    r"""Gera ASS com \pos, \fad e estilo por camada de texto."""
    header = (
        "[Script Info]\nTitle: Vilm Scene\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
    )
    styles: set[str] = set()
    events: list[str] = []
    for layer in layers:
        if layer.get("type") != "text":
            continue
        style = str(layer.get("style") or "SceneText")
        styles.add(style)
        size = int(layer.get("font_size", 64))
        color = ass_color(str(layer.get("color") or "#000000"))
        outline = float(layer.get("outline", 0))
        bold = -1 if layer.get("bold", True) else 0
        header += (
            f"Style: {style},{layer.get('font') or 'Arial'},{size},{color},"
            f"{color},&H00000000&,&H00000000&,{bold},0,0,0,100,100,0,0,1,"
            f"{outline},0,5,0,0,0,1\n"
        )
        start = float(layer.get("start", 0))
        end = float(layer.get("end") or (start + 5.0))
        fade = float(layer.get("fade", 0.3))
        fade_tag = f"{{\\fad({int(fade * 1000)},{int(fade * 1000)})}}" if fade else ""
        x = float(layer.get("x", width / 2)); y = float(layer.get("y", height / 2))
        text = str(layer.get("text", "")).replace("\n", "\\N")
        events.append(
            "Dialogue: 0," + format_ass_timestamp(start) + "," + format_ass_timestamp(end)
            + f",{style},,0,0,0,,{{\\pos({int(x)},{int(y)})}}{fade_tag}{text}"
        )
    return header + "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + "\n".join(events) + "\n"


def _video_size(path: Path) -> tuple[int, int]:
    ffprobe = TOOLS / "ffprobe.exe" if (TOOLS / "ffprobe.exe").is_file() else "ffprobe"
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    try:
        w, h = result.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except (ValueError, IndexError):
        return 1920, 1080


def compile_scene(*, scene: dict, video: Path, output: Path, work: Path) -> Path:
    """Aplica todas as camadas da cena sobre o video e retorna o arquivo final."""
    video = Path(video).resolve(); output = Path(output).resolve(); work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    width = int(scene.get("width", 1920)); height = int(scene.get("height", 1080))
    layers = [l for l in scene.get("layers", []) if l.get("type") == "image"]
    text_layers = [l for l in scene.get("layers", []) if l.get("type") == "text"]

    command: list[str] = [str(FFMPEG), "-y", "-loglevel", "error", "-i", str(video)]
    inputs = 1
    filters: list[str] = []
    audio = "[0:a:0]" if _has_audio(video) else None
    last = "[0:v:0]"
    vw, vh = _video_size(video)
    if (vw, vh) != (width, height):
        filters.append(
            f"[0:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,"
            f"setsar=1,fps=30[base]"
        )
        last = "[base]"

    for index, layer in enumerate(layers, 1):
        start = float(layer.get("start", 0))
        end = float(layer.get("end") or (start + 5.0))
        duration = max(0.1, end - start)
        # A imagem entra como fonte contínua; o tempo de exibicao e controlado
        # pelo overlay/enable. Isso evita deslocar a linha do tempo do video.
        image_path = Path(str(layer["file"]))
        fx = layer.get("fx")
        needs_fx = False
        if isinstance(fx, dict) and fx:
            needs_fx = str(fx.get("remove_background", "none")) != "none"
            for key in ("brightness", "contrast", "saturation", "sharpness",
                        "blur", "opacity", "rotate", "tolerance", "feather"):
                try:
                    if abs(float(fx.get(key, 0))) > 1e-6:
                        needs_fx = True
                except (TypeError, ValueError):
                    pass
            needs_fx = needs_fx or any(fx.get(k) for k in (
                "grayscale", "sepia", "tint", "flip_h", "flip_v", "shadow"))
        if needs_fx:
            from .imagefx import process_image
            image_path = process_image(image_path, work / f"fx_{index}.png", fx)
        command += ["-loop", "1", "-framerate", "1", "-t", f"{end:.3f}",
                    "-i", str(image_path)]
        label = f"[img{index}]"
        w = int(layer.get("w", width)); h = int(layer.get("h", height))
        chain = [f"[{inputs}:v]scale={w}:{h}:force_original_aspect_ratio=decrease",
                 f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000", "setsar=1", "format=rgba"]
        fade_in = float(layer.get("fade_in", 0))
        fade_out = float(layer.get("fade_out", 0))
        if fade_in:
            chain.append(f"fade=t=in:st={start:.3f}:d={fade_in}:alpha=1")
        if fade_out:
            chain.append(f"fade=t=out:st={max(0, end - fade_out):.3f}:d={fade_out}:alpha=1")
        if layer.get("ken_burns"):
            chain.append(f"zoompan=z='min(zoom+0.0015,1.12)':d=1:s={w}x{h}:fps=30")
        chain.append("fps=30")
        chain[-1] += label
        filters.append(",".join(chain))
        out = f"[mix{index}]"
        x = int(layer.get("x", 0)); y = int(layer.get("y", 0))
        filters.append(
            f"{last}[img{index}]overlay={x}:{y}:enable='between(t,{start:.3f},{end:.3f})':"
            f"format=auto:shortest=0{out}"
        )
        last = out
        inputs += 1

    if text_layers:
        ass = work / "scene_text.ass"
        ass.write_text(_ass_for_text_layers(text_layers, width, height), encoding="utf-8")
        last_out = "[vout]"
        filters.append(f"{last}ass=scene_text.ass{last_out}")
        last = last_out

    if not filters and video == output:
        return output
    if not filters:
        command += ["-i", str(video), "-map", "0:v:0"]
        if audio:
            command += ["-map", audio]
        command += ["-c", "copy", "-movflags", "+faststart", str(output)]
        _run(command)
        return output

    if audio:
        filters.append("[0:a:0]anull[aout]")
    command += ["-filter_complex", ";".join(filters), "-map", last]
    if audio:
        command += ["-map", "[aout]"]
    command += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(output)]
    _run(command, cwd=work)
    return output


def render_preview(*, scene: dict, background: Path | None, time: float,
                   output: Path, width: int = 640, height: int = 360) -> Path:
    """Renderiza um unico frame da cena para previa rapida no editor."""
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(FFMPEG), "-y", "-loglevel", "error", "-ss", f"{max(0.0, time):.3f}"]
    if background and Path(background).is_file():
        command += ["-i", str(background)]
        base = "[0:v:0]"
    else:
        command += ["-f", "lavfi", "-i", f"color=c=0xFFF3E8:s={width}x{height}:r=30:d=1"]
        base = "[0:v]"
    filters = [f"{base}scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0xFFF3E8,setsar=1[base]"]
    last = "[base]"; inputs = 1
    for index, layer in enumerate(scene.get("layers", []), 1):
        if layer.get("type") != "image" or not Path(str(layer.get("file") or "")).is_file():
            continue
        start = float(layer.get("start", 0)); end = float(layer.get("end") or start + 5)
        if not (start <= time <= end):
            continue
        image_path = Path(str(layer["file"]))
        fx = layer.get("fx")
        if isinstance(fx, dict) and fx:
            from .imagefx import process_image
            image_path = process_image(image_path, Path(output).parent / f"preview_fx_{index}.png", fx)
        command += ["-i", str(image_path)]
        scene_w = int(scene.get("width", 1920)); scene_h = int(scene.get("height", 1080))
        w = max(1, int(float(layer.get("w", scene_w)) * width / scene_w))
        h = max(1, int(float(layer.get("h", scene_h)) * height / scene_h))
        x = int(float(layer.get("x", 0)) * width / scene_w)
        y = int(float(layer.get("y", 0)) * height / scene_h)
        filters.append(f"[{inputs}:v]scale={w}:{h},format=rgba[i{index}]")
        filters.append(f"{last}[i{index}]overlay={x}:{y}:format=auto[m{index}]")
        last = f"[m{index}]"; inputs += 1
    filters.append(f"{last}scale={width}:{height}[out]")
    command += ["-filter_complex", ";".join(filters), "-map", "[out]",
                "-frames:v", "1", str(output)]
    _run(command)
    return output


def _has_audio(path: Path) -> bool:
    ffprobe = TOOLS / "ffprobe.exe" if (TOOLS / "ffprobe.exe").is_file() else "ffprobe"
    result = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "a",
                             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True)
    return bool(result.stdout.strip())


def _run(command: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run([str(c) for c in command], cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout)[-2500:])
