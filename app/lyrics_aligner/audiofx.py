"""Processamento de audio: separacao de vozes, playback, volume, ruido e qualidade."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parents[2] / "tools"
FFMPEG = TOOLS / "ffmpeg.exe" if (TOOLS / "ffmpeg.exe").is_file() else "ffmpeg"
FFPROBE = TOOLS / "ffprobe.exe" if (TOOLS / "ffprobe.exe").is_file() else "ffprobe"


def _run(command: list[str]) -> str:
    result = subprocess.run([str(c) for c in command], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout)[-2500:])
    return result.stdout


def separate_audio(media: str | Path, work_dir: str | Path, *,
                   model: str = "htdemucs", device: str = "auto",
                   progress=None) -> dict[str, Path]:
    """Separa voz e instrumental. Retorna {'vocals', 'instrumental'}."""
    from .backends.demucs import separate_vocals
    media = Path(media).resolve(); work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    if progress:
        progress("Separando vozes com Demucs…")
    vocals = separate_vocals(media, work, model=model, device=device)
    instrumental = vocals.with_name("no_vocals.wav")
    if not instrumental.is_file():
        matches = list(work.glob("**/no_vocals.wav"))
        if not matches:
            raise RuntimeError("Demucs nao gerou o instrumental (no_vocals.wav).")
        instrumental = matches[0]
    return {"vocals": vocals, "instrumental": instrumental}


def mix_audio(vocals: str | Path, instrumental: str | Path, output: str | Path,
              *, voice_gain: float = 1.0, music_gain: float = 1.0) -> Path:
    """Mistura voz e instrumental com ganhos independentes."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run([FFMPEG, "-y", "-loglevel", "error", "-i", vocals, "-i", instrumental,
          "-filter_complex",
          f"[0:a]volume={max(0.0, voice_gain):.4f}[v];"
          f"[1:a]volume={max(0.0, music_gain):.4f}[m];"
          "[v][m]amix=inputs=2:normalize=0:dropout_transition=0[out]",
          "-map", "[out]", "-c:a", "pcm_s16le", str(output)])
    return output


def denoise(media: str | Path, output: str | Path, *, strength: float = 10.0) -> Path:
    """Reduz ruido de banda larga (FFT). strength: 2 (leve) a 30 (forte)."""
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    _run([FFMPEG, "-y", "-loglevel", "error", "-i", media,
          "-af", f"afftdn=nr={max(1.0, min(40.0, strength)):.1f}:nf=-40",
          "-c:a", "pcm_s16le", str(output)])
    return output


def normalize(media: str | Path, output: str | Path, *, lufs: float = -16.0,
              true_peak: float = -1.5, lra: float = 11.0) -> Path:
    """Normalizacao de loudness EBU R128."""
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    _run([FFMPEG, "-y", "-loglevel", "error", "-i", media,
          "-af", f"loudnorm=I={lufs}:TP={true_peak}:LRA={lra}",
          "-c:a", "pcm_s16le", str(output)])
    return output


def enhance(media: str | Path, output: str | Path, *, preset: str = "voz") -> Path:
    """Melhora clareza: corte de grave, presence EQ, compressao e loudness."""
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    if preset == "musica":
        eq = "highpass=f=40,equalizer=f=120:t=q:w=1:g=1,equalizer=f=8000:t=q:w=1:g=1"
        comp = "acompressor=threshold=-16dB:ratio=2:attack=30:release=200"
    else:
        eq = "highpass=f=75,equalizer=f=3000:t=q:w=1:g=2.5,equalizer=f=8000:t=q:w=1:g=1.5"
        comp = "acompressor=threshold=-18dB:ratio=3:attack=20:release=150"
    _run([FFMPEG, "-y", "-loglevel", "error", "-i", media,
          "-af", f"{eq},{comp},loudnorm=I=-16:TP=-1.5:LRA=11",
          "-c:a", "pcm_s16le", str(output)])
    return output


def analyze(media: str | Path) -> dict[str, Any]:
    """Mede duracao, pico e volume medio para mostrar na interface."""
    out = _run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries",
                "format=duration:stream=codec_name,sample_rate,channels",
                "-of", "json", str(media)])
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    stats = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(media),
                  "-af", "volumedetect", "-f", "null", "-"])
    result: dict[str, Any] = {
        "duration": float((data.get("format") or {}).get("duration", 0)),
        "codec": stream.get("codec_name"), "sample_rate": stream.get("sample_rate"),
        "channels": stream.get("channels"),
    }
    for line in stats.splitlines():
        if "mean_volume:" in line:
            result["mean_db"] = line.split("mean_volume:")[1].strip().split()[0]
        elif "max_volume:" in line:
            result["max_db"] = line.split("max_volume:")[1].strip().split()[0]
    return result


def apply_audio_fx(media: str | Path, output: str | Path, fx: dict,
                   work_dir: str | Path, *, progress=None) -> Path:
    """Aplica uma sequencia de efeitos descrita em fx."""
    media = Path(media); output = Path(output); work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    current = media
    step = 0
    if float(fx.get("denoise", 0)) > 0:
        step += 1
        current = denoise(current, work / f"step{step}.wav",
                          strength=float(fx["denoise"]))
    if fx.get("enhance"):
        step += 1
        current = enhance(current, work / f"step{step}.wav",
                          preset=str(fx.get("enhance_preset", "voz")))
    if fx.get("normalize"):
        step += 1
        current = normalize(current, work / f"step{step}.wav")
    wants_mix = (fx.get("instrumental")
                 or abs(float(fx.get("voice_gain", 1.0)) - 1.0) > 1e-6
                 or abs(float(fx.get("music_gain", 1.0)) - 1.0) > 1e-6)
    if wants_mix:
        stems = separate_audio(current, work / "stems", progress=progress)
        voice_gain = float(fx.get("voice_gain", 1.0))
        music_gain = float(fx.get("music_gain", 1.0))
        if fx.get("instrumental"):
            current = stems["instrumental"]
        else:
            step += 1
            current = mix_audio(stems["vocals"], stems["instrumental"],
                                work / f"step{step}.wav",
                                voice_gain=voice_gain, music_gain=music_gain)
    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = output.suffix.lower()
    codec = ("libmp3lame" if suffix == ".mp3" else
             "aac" if suffix in {".m4a", ".aac"} else "pcm_s16le")
    _run([FFMPEG, "-y", "-loglevel", "error", "-i", current,
          "-ar", "44100", "-c:a", codec, str(output)])
    return output
