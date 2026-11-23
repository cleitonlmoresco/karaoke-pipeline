#!/usr/bin/env python
"""Gera os videos karaoke no padrao Santo (rolagem, 5 linhas, 85/90 px).

Uso tipico:
    python gerar_video_padrao_santo.py --projeto "C:\\...\\Santo - Voz da Verdade" --tempos tempos.json --backup-paginas --burn --verify

O arquivo de tempos (tempos.json) tem este formato:

{
  "titulo": "Santo",
  "cantado": [
    {"texto": "Santo, Santo, Santo",
     "palavras": [["Santo,", 59.40, 60.60], ["Santo,", 60.60, 61.60], ["Santo", 61.60, 63.10]]}
  ],
  "playback": {"offset": 0.208, "primeiro_inicio": 52.0}
}

- "palavras" sao as palavras/frases na ordem cantada, com inicio e fim em segundos.
- "playback" e opcional: desloca todas as linhas por "offset" e, se
  "primeiro_inicio" for informado, forca o inicio do primeiro slide.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
VILM = LOCALAPPDATA / "VilmPipeline"
APP = VILM / "app"
PYTHON = VILM / "python" / "python.exe"
FFMPEG = VILM / "tools" / "ffmpeg.exe"
FFPROBE = VILM / "tools" / "ffprobe.exe"

sys.path.insert(0, str(APP))

from lyrics_aligner.karaoke import KaraokeStyle, make_background_video, render_karaoke_ass  # noqa: E402
from lyrics_aligner.models import Cue  # noqa: E402
from lyrics_aligner.srt import render_srt  # noqa: E402

PADRAO = {
    "font_name": "Times New Roman",
    "normal_color": "#000000",
    "active_color": "#000000",
    "outline_color": "#000000",
    "back_color": "#00000080",
    "bold": True,
    "outline": 0.0,
    "shadow": 1.0,
    "alignment": 5,
    "font_size": 85,
    "active_font_size": 90,
    "margin_h": 40,
    "margin_v": 40,
    "lines_before": 0,
    "lines_after": 4,
    "transition": "scroll",
    "transition_ms": 400,
    "scroll_pause_seconds": 3.0,
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def fmt_time(seconds: float) -> str:
    minutes, rest = divmod(float(seconds), 60.0)
    return f"{int(minutes)}:{rest:05.2f}"


def nome_base(projeto: Path, meta: dict) -> str:
    """Nome do projeto, limpo para uso em nome de arquivo no Windows."""
    nome = str(meta.get("name") or projeto.name).strip()
    nome = re.sub(r'[<>:"/\\|?*]', "-", nome)
    nome = re.sub(r"\s+", " ", nome).strip(" .")
    if not nome:
        nome = projeto.name
    reservados = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    if nome.upper() in reservados:
        nome = f"Projeto {nome}"
    return nome


def alias(origem: Path, destino: Path) -> None:
    """Cria um hardlink (ou copia) para manter compatibilidade com o app."""
    if destino.exists() or destino.is_symlink():
        destino.unlink()
    try:
        os.link(origem, destino)
    except OSError:
        shutil.copy2(origem, destino)


def carregar_tempos(path: Path) -> tuple[str, list[dict], dict, list[dict] | None]:
    data = read_json(path)
    linhas = data.get("cantado") or data.get("lines")
    if not isinstance(linhas, list) or not linhas:
        raise SystemExit("tempos.json: informe a lista 'cantado' com as linhas.")
    for index, linha in enumerate(linhas, start=1):
        texto = str(linha.get("texto") or linha.get("text") or "").strip()
        palavras = linha.get("palavras") or linha.get("units") or []
        if not texto:
            raise SystemExit(f"tempos.json: linha {index} sem 'texto'.")
        if not palavras:
            raise SystemExit(f"tempos.json: linha {index} sem 'palavras'.")
        for palavra in palavras:
            if len(palavra) != 3:
                raise SystemExit(
                    f"tempos.json: linha {index}: cada palavra deve ser [texto, inicio, fim]."
                )
            start, end = float(palavra[1]), float(palavra[2])
            if start < 0 or end < start:
                raise SystemExit(
                    f"tempos.json: linha {index}, palavra {palavra[0]!r}: tempo invalido."
                )
    playback = data.get("playback") or {}
    playback_linhas = data.get("playback_cantado")
    if playback_linhas is not None:
        if not isinstance(playback_linhas, list) or not playback_linhas:
            raise SystemExit("tempos.json: 'playback_cantado' deve ser uma lista de linhas.")
        for index, linha in enumerate(playback_linhas, start=1):
            texto = str(linha.get("texto") or "").strip()
            palavras = linha.get("palavras") or []
            if not texto or not palavras:
                raise SystemExit(f"tempos.json: playback linha {index} incompleta.")
            for palavra in palavras:
                if len(palavra) != 3:
                    raise SystemExit(
                        f"tempos.json: playback linha {index}: palavra invalida."
                    )
                start, end = float(palavra[1]), float(palavra[2])
                if start < 0 or end < start:
                    raise SystemExit(
                        f"tempos.json: playback linha {index}, palavra {palavra[0]!r}: tempo invalido."
                    )
    return str(data.get("titulo") or "Karaoke"), linhas, playback, playback_linhas


def montar(linhas: list[dict], offset: float = 0.0) -> tuple[list[Cue], list[dict]]:
    cues: list[Cue] = []
    units: list[dict] = []
    for linha in linhas:
        texto = str(linha.get("texto") or linha.get("text") or "").strip()
        palavras = linha.get("palavras") or linha.get("units")
        line_units = [
            {
                "text": str(p[0]),
                "start": round(float(p[1]) + offset, 3),
                "end": round(float(p[2]) + offset, 3),
            }
            for p in palavras
        ]
        cues.append(
            Cue(
                text=texto,
                start=line_units[0]["start"],
                end=max(u["end"] for u in line_units),
            )
        )
        units.extend(line_units)
    for previous, cue in zip(cues, cues[1:]):
        if cue.start < previous.end - 0.001:
            raise SystemExit(
                f"Linhas sobrepostas: {previous.text!r} termina em {previous.end:.2f} "
                f"e {cue.text!r} comeca em {cue.start:.2f}."
            )
    return cues, units


def estilo(args: argparse.Namespace, cfg: dict) -> KaraokeStyle:
    k = cfg.get("karaoke") or {}
    valores = dict(PADRAO)
    valores["font_name"] = args.fonte or k.get("font_name") or PADRAO["font_name"]
    for chave_cfg, chave_padrao in (
        ("normal_color", "normal_color"),
        ("active_color", "active_color"),
        ("outline_color", "outline_color"),
        ("back_color", "back_color"),
    ):
        if k.get(chave_cfg):
            valores[chave_padrao] = k[chave_cfg]
    if args.tamanho:
        valores["font_size"] = args.tamanho
    if args.tamanho_ativo:
        valores["active_font_size"] = args.tamanho_ativo
    if args.pausa is not None:
        valores["scroll_pause_seconds"] = args.pausa
    if args.margem is not None:
        valores["margin_h"] = args.margem
        valores["margin_v"] = args.margem
    return KaraokeStyle(
        font_name=valores["font_name"],
        font_size=int(valores["font_size"]),
        active_font_size=int(valores["active_font_size"]),
        normal_color=valores["normal_color"],
        active_color=valores["active_color"],
        outline_color=valores["outline_color"],
        back_color=valores["back_color"],
        bold=bool(valores["bold"]),
        outline=float(valores["outline"]),
        shadow=float(valores["shadow"]),
        alignment=int(valores["alignment"]),
        margin_l=int(valores["margin_h"]),
        margin_r=int(valores["margin_h"]),
        margin_v=int(valores["margin_v"]),
        lines_before=int(valores["lines_before"]),
        lines_after=int(valores["lines_after"]),
        lines_per_slide=0,
        transition="scroll",
        transition_ms=int(valores["transition_ms"]),
        scroll_pause_seconds=float(valores["scroll_pause_seconds"]),
    )


def gerar_arquivos(
    saida: Path,
    titulo: str,
    cantado: list[dict],
    playback_cfg: dict,
    style: KaraokeStyle,
    cfg: dict,
    playback_linhas: list[dict] | None = None,
) -> dict:
    out = cfg.get("output") or {}
    width = int(out.get("width") or 1920)
    height = int(out.get("height") or 1080)

    cues_cantado, units_cantado = montar(cantado, 0.0)
    if playback_linhas:
        cues_playback, units_playback = montar(playback_linhas, 0.0)
        offset = 0.0
    else:
        offset = float(playback_cfg.get("offset") or 0.0)
        cues_playback, units_playback = montar(cantado, offset)
    primeiro = playback_cfg.get("primeiro_inicio")
    if primeiro is not None:
        first = cues_playback[0]
        cues_playback[0] = Cue(text=first.text, start=float(primeiro), end=first.end)

    saida.mkdir(parents=True, exist_ok=True)
    (saida / "lyrics.srt").write_text(render_srt(cues_cantado), encoding="utf-8-sig")
    (saida / "lyrics_playback.srt").write_text(
        render_srt(cues_playback), encoding="utf-8-sig"
    )
    (saida / "alignment-units-scroll.json").write_text(
        json.dumps({"global_units": units_cantado}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (saida / "alignment-units-playback-scroll.json").write_text(
        json.dumps({"global_units": units_playback}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ass_cantado = render_karaoke_ass(
        cues_cantado,
        title=titulo,
        style=style,
        units=units_cantado,
        play_res_x=width,
        play_res_y=height,
    )
    ass_playback = render_karaoke_ass(
        cues_playback,
        title=titulo,
        style=style,
        units=units_playback,
        play_res_x=width,
        play_res_y=height,
    )
    (saida / "lyrics_rolagem.karaoke.ass").write_text(ass_cantado, encoding="utf-8")
    (saida / "lyrics_playback_rolagem.karaoke.ass").write_text(
        ass_playback, encoding="utf-8"
    )
    return {
        "width": width,
        "height": height,
        "cues": len(cues_cantado),
        "events_cantado": ass_cantado.count("Dialogue: "),
        "events_playback": ass_playback.count("Dialogue: "),
        "primeiro_cantado": cues_cantado[0].start,
        "primeiro_playback": cues_playback[0].start,
        "ultimo_fim": cues_cantado[-1].end,
        "offset": offset,
    }


def garantir_backup(projeto: Path, saida: Path, meta: dict) -> None:
    base = nome_base(projeto, meta)
    origem = [
        f"{base}_cantado.mp4",
        f"{base}_playback.mp4",
        f"{base}_com_letra.mp4",
        "video_cantado.mp4",
        "video_playback.mp4",
        "video_with_lyrics.mp4",
        "lyrics.karaoke.ass",
        "lyrics_playback.karaoke.ass",
        "lyrics.srt",
        "lyrics_playback.srt",
        "alignment-units.json",
    ]
    destino = saida / "paginas"
    destino.mkdir(parents=True, exist_ok=True)
    copiados, mantidos = [], []
    for nome in origem:
        fonte = saida / nome
        alvo = destino / nome
        if not fonte.is_file():
            continue
        if alvo.exists():
            mantidos.append(nome)
            continue
        shutil.copy2(fonte, alvo)
        copiados.append(nome)
    if copiados:
        print(f"Backup em paginas: {', '.join(copiados)}")
    if mantidos:
        print(f"Backup ja existente (preservado): {', '.join(mantidos)}")


def achar_midia(projeto: Path, meta: dict, chave: str) -> Path | None:
    nome = str(meta.get(chave) or "").strip()
    if not nome:
        return None
    candidato = Path(nome)
    if candidato.is_absolute() and candidato.is_file():
        return candidato
    for base in (projeto, projeto / "midia"):
        alvo = base / Path(nome).name
        if alvo.is_file():
            return alvo
    return None


def fundo_do_projeto(projeto: Path, cfg: dict) -> Path | None:
    bg = cfg.get("background") or {}
    if str(bg.get("type") or "") != "image":
        return None
    nome = str(bg.get("image_filename") or "").strip()
    if not nome:
        return None
    alvo = projeto / "fundo" / Path(nome).name
    return alvo if alvo.is_file() else None


def backgrounds(
    projeto: Path,
    cfg: dict,
    meta: dict,
    refazer: bool,
    media_override: Path | None = None,
    playback_override: Path | None = None,
    fundo_override: Path | None = None,
) -> tuple[Path, Path]:
    padrao = projeto / "trabalho" / "_padrao_santo"
    padrao.mkdir(parents=True, exist_ok=True)
    overrides = {"media": media_override, "playback": playback_override}
    pares = {
        "background": ("media", "background.mp4"),
        "background_playback": ("playback", "background_playback.mp4"),
    }
    resultado: dict[str, Path] = {}
    for chave, (campo_midia, nome_saida) in pares.items():
        existentes = sorted(
            projeto.glob(f"trabalho/*/{nome_saida}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if existentes and not refazer:
            resultado[chave] = existentes[0]
            continue
        destino = padrao / nome_saida
        if destino.is_file() and not refazer:
            resultado[chave] = destino
            continue
        media = overrides[campo_midia] or achar_midia(projeto, meta, campo_midia)
        if media is None or not Path(media).is_file():
            raise SystemExit(
                f"Nao encontrei a midia '{campo_midia}'. Informe --{campo_midia} "
                "ou preencha 'media'/'playback' no projeto.json."
            )
        print(f"Criando {nome_saida} a partir de {Path(media).name}...")
        make_background_video(
            media,
            destino,
            color=str((cfg.get("background") or {}).get("color") or "#66000A"),
            image=fundo_override or fundo_do_projeto(projeto, cfg),
            width=int((cfg.get("output") or {}).get("width") or 1920),
            height=int((cfg.get("output") or {}).get("height") or 1080),
            ffmpeg=FFMPEG,
            preset="medium",
            crf=18,
            fit=str((cfg.get("background") or {}).get("fit") or "contain"),
        )
        resultado[chave] = destino
    return resultado["background"], resultado["background_playback"]


FFMPEG_TIMEOUT_SECONDS = 1800
FFMPEG_RETRY_ATTEMPTS = 5
FFMPEG_RETRY_DELAY_SECONDS = 1.5


def _tail_text(path: Path, limit: int = 2000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    data = data.strip()
    if len(data) <= limit:
        return data
    return "...(tail)...\n" + data[-limit:]


def rodar_ffmpeg(
    args: list[str],
    cwd: Path | None = None,
    timeout: int | None = FFMPEG_TIMEOUT_SECONDS,
    progress_file: Path | None = None,
) -> None:
    cmd = list(args)
    if progress_file is not None:
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        cmd = [*cmd[:1], "-progress", str(progress_file), *cmd[1:]]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        progresso = _tail_text(progress_file) if progress_file else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        detalhe = stderr.strip() or stdout.strip()
        msg = f"FFmpeg excedeu timeout de {timeout}s."
        if detalhe:
            msg += "\n" + detalhe
        if progresso:
            msg += "\nProgresso (tail):\n" + progresso
        raise SystemExit(msg) from exc
    if completed.returncode != 0:
        progresso = _tail_text(progress_file) if progress_file else ""
        detalhe = completed.stderr.strip() or completed.stdout.strip()
        msg = "FFmpeg falhou:\n" + detalhe
        if progresso:
            msg += "\nProgresso (tail):\n" + progresso
        raise SystemExit(msg)


def _replace_with_retry(origem: Path, destino: Path) -> None:
    ultimo_erro: Exception | None = None
    for tentativa in range(1, FFMPEG_RETRY_ATTEMPTS + 1):
        try:
            os.replace(origem, destino)
            return
        except PermissionError as exc:
            ultimo_erro = exc
            if tentativa == FFMPEG_RETRY_ATTEMPTS:
                break
            time.sleep(FFMPEG_RETRY_DELAY_SECONDS * tentativa)
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if winerror != 5:
                raise
            ultimo_erro = exc
            if tentativa == FFMPEG_RETRY_ATTEMPTS:
                break
            time.sleep(FFMPEG_RETRY_DELAY_SECONDS * tentativa)
    raise SystemExit(
        f"Nao foi possivel promover '{origem.name}' para '{destino}': arquivo destino "
        f"pode estar travado (WinError 5). Feche players/editores e tente novamente. "
        f"Arquivo temporario preservado para recuperacao: {origem}. Detalhe: {ultimo_erro}"
    )


def queimar(
    projeto: Path,
    saida: Path,
    cfg: dict,
    meta: dict,
    refazer_background: bool,
    media: Path | None = None,
    playback: Path | None = None,
    fundo: Path | None = None,
) -> None:
    bg_cantado, bg_playback = backgrounds(
        projeto, cfg, meta, refazer_background, media, playback, fundo
    )
    cor = str((cfg.get("background") or {}).get("color") or "#66000A").lstrip("#")
    filtro = (
        "scale={w}:{h}:force_original_aspect_ratio=decrease,"
        "pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x" + cor + ",setsar=1,ass={ass}"
    )
    width = int((cfg.get("output") or {}).get("width") or 1920)
    height = int((cfg.get("output") or {}).get("height") or 1080)
    trabalhos = (
        (bg_cantado, "lyrics_rolagem.karaoke.ass", "_novo_cantado.mp4"),
        (bg_playback, "lyrics_playback_rolagem.karaoke.ass", "_novo_playback.mp4"),
    )
    progress_logs: list[Path] = []
    for bg, ass, temp in trabalhos:
        print(f"Queimando {temp} ...")
        progress_file = saida / f"{temp}.progress.log"
        progress_logs.append(progress_file)
        rodar_ffmpeg(
            [
                str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                "-i", str(bg),
                "-vf", filtro.format(w=width, h=height, ass=ass),
                "-map", "0:v:0", "-map", "0:a:0?", "-sn",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                temp,
            ],
            cwd=saida,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            progress_file=progress_file,
        )

    pendentes = [
        (saida / "_novo_cantado.mp4", saida / f"{nome_base(projeto, meta)}_cantado.mp4"),
        (saida / "_novo_playback.mp4", saida / f"{nome_base(projeto, meta)}_playback.mp4"),
    ]
    invalidos = [
        str(origem)
        for origem, _ in pendentes
        if not origem.is_file() or origem.stat().st_size == 0
    ]
    if invalidos:
        raise SystemExit(
            "FFmpeg nao gerou arquivo(s) valido(s): " + ", ".join(invalidos)
        )

    promovidos: list[tuple[Path, Path]] = []
    erros_promocao: list[str] = []
    for origem, destino in pendentes:
        try:
            _replace_with_retry(origem, destino)
            promovidos.append((origem, destino))
        except SystemExit as exc:
            erros_promocao.append(str(exc))

    if erros_promocao:
        detalhes = "\n".join(erros_promocao)
        preservados = [str(origem) for origem, _ in pendentes if origem.exists()]
        msg = "Falha na promocao dos videos.\n" + detalhes
        if preservados:
            msg += "\nTemporarios preservados: " + ", ".join(preservados)
        if promovidos:
            msg += (
                "\nAtencao: houve promocao parcial antes da falha: "
                + ", ".join(str(d) for _, d in promovidos)
            )
        raise SystemExit(msg)

    base = nome_base(projeto, meta)
    alias(saida / f"{base}_cantado.mp4", saida / f"{base}_com_letra.mp4")
    # Compatibilidade com o Vilm Pipeline, que procura os nomes antigos.
    alias(saida / f"{base}_cantado.mp4", saida / "video_cantado.mp4")
    alias(saida / f"{base}_playback.mp4", saida / "video_playback.mp4")
    alias(saida / f"{base}_com_letra.mp4", saida / "video_with_lyrics.mp4")
    shutil.copy2(saida / "lyrics_rolagem.karaoke.ass", saida / "lyrics.karaoke.ass")
    shutil.copy2(
        saida / "lyrics_playback_rolagem.karaoke.ass",
        saida / "lyrics_playback.karaoke.ass",
    )
    for progress_file in progress_logs:
        try:
            progress_file.unlink(missing_ok=True)
        except OSError:
            pass
    print(
        f"Videos promovidos para {base}_cantado.mp4 e {base}_playback.mp4 "
        f"(alias: video_cantado.mp4 / video_playback.mp4)."
    )


def duracao(caminho: Path) -> float:
    r = subprocess.run(
        [str(FFPROBE), "-hide_banner", "-loglevel", "error",
         "-show_entries", "format=duration", "-of", "csv=p=0", str(caminho)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return float(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


def verificar(saida: Path, frames: list[float], base: str) -> None:
    videos = (f"{base}_cantado.mp4", f"{base}_playback.mp4")
    print("\n== Verificacao ==")
    for nome in videos:
        caminho = saida / nome
        if not caminho.is_file():
            print(f"{nome}: AUSENTE")
            continue
        dec = subprocess.run(
            [str(FFMPEG), "-hide_banner", "-v", "error", "-i", str(caminho), "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        erros = len([l for l in dec.stderr.splitlines() if l.strip()])
        print(
            f"{nome}: {fmt_time(duracao(caminho))} "
            f"({caminho.stat().st_size / 1048576:.1f} MB), erros de decodificacao={erros}"
        )
    if not frames:
        return
    destino = saida / "conferencia"
    destino.mkdir(parents=True, exist_ok=True)
    video = saida / f"{base}_cantado.mp4"
    for t in frames:
        alvo = destino / f"quadro_{t:07.2f}.jpg".replace(".", "_", 1)
        subprocess.run(
            [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
             "-update", "1", str(alvo)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        print(f"quadro {fmt_time(t)} -> {alvo.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projeto", type=Path, required=True)
    parser.add_argument("--tempos", type=Path)
    parser.add_argument("--saida", type=Path)
    parser.add_argument("--ass-only", action="store_true", help="apenas SRT/units/ASS")
    parser.add_argument("--burn", action="store_true", help="queimar os MP4")
    parser.add_argument("--verify", action="store_true", help="conferir duracao/decodificacao")
    parser.add_argument("--frames", default="", help="tempos para quadros, separados por virgula")
    parser.add_argument("--backup-paginas", action="store_true")
    parser.add_argument("--refazer-background", action="store_true")
    parser.add_argument("--media", type=Path, help="audio/video cantado (opcional)")
    parser.add_argument("--playback", type=Path, help="audio/video playback (opcional)")
    parser.add_argument("--fundo", type=Path, help="imagem de fundo (opcional)")
    parser.add_argument("--fonte")
    parser.add_argument("--tamanho", type=int)
    parser.add_argument("--tamanho-ativo", type=int)
    parser.add_argument("--pausa", type=float)
    parser.add_argument("--margem", type=int)
    args = parser.parse_args()

    projeto = args.projeto.resolve()
    if not projeto.is_dir():
        raise SystemExit(f"Projeto nao encontrado: {projeto}")
    saida = (args.saida or (projeto / "saida")).resolve()
    tempos_path = (args.tempos or (projeto / "tempos.json")).resolve()
    if not tempos_path.is_file():
        raise SystemExit(f"Arquivo de tempos nao encontrado: {tempos_path}")

    cfg = read_json(projeto / "configuracoes.json")
    meta = read_json(projeto / "projeto.json") if (projeto / "projeto.json").is_file() else {}
    base = nome_base(projeto, meta)
    titulo, linhas, playback_cfg, playback_linhas = carregar_tempos(tempos_path)
    style = estilo(args, cfg)

    if args.backup_paginas:
        garantir_backup(projeto, saida, meta)

    info = gerar_arquivos(
        saida, titulo, linhas, playback_cfg, style, cfg, playback_linhas
    )
    print(
        f"{info['cues']} versos | ASS cantado={info['events_cantado']} "
        f"playback={info['events_playback']} | primeiro cantado="
        f"{fmt_time(info['primeiro_cantado'])} playback={fmt_time(info['primeiro_playback'])} "
        f"| ultimo fim={fmt_time(info['ultimo_fim'])}"
    )
    print(f"SRT, unidades e ASS gravados em {saida}")

    if args.burn:
        if not args.backup_paginas and (
            (saida / f"{base}_cantado.mp4").exists()
            or (saida / "video_cantado.mp4").exists()
        ):
            raise SystemExit(
                "Ja existem videos em saida. Rode com --backup-paginas para preservar "
                "a versao anterior em saida\\paginas."
            )
        queimar(
            projeto,
            saida,
            cfg,
            meta,
            args.refazer_background,
            args.media.resolve() if args.media else None,
            args.playback.resolve() if args.playback else None,
            args.fundo.resolve() if args.fundo else None,
        )
    elif not args.ass_only:
        print("Dica: use --burn para gerar os MP4 e --verify para conferir.")

    if args.verify:
        frames = [float(v) for v in args.frames.split(",") if v.strip()]
        verificar(saida, frames, base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
