"""Servico unico de alinhamento: delega ao CLI e roda QC logo depois.

O subprocesso e executado exclusivamente por ``process_runner.run_job``;
este modulo nao chama ``subprocess`` diretamente.  O perfil de alinhamento e
aplicado pelo proprio CLI ``align`` (``--profile``), e os ajustes explicitos do
chamador sao repassados como flags aditivas.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from ..karaoke_jobs import qc_report
from ..process_runner import console_python, run_job
from ..srt import parse_srt

# Raiz da aplicacao: ``app/lyrics_aligner/services/align.py`` -> ``<install>/app``
APP_PATH = Path(__file__).resolve().parents[2]

# Chaves aceitas em ``overrides`` e a flag correspondente do CLI ``align``.
_OVERRIDE_FLAGS: dict[str, str] = {
    "max_chars": "--max-chars",
    "language": "--language",
    "model_name": "--model",
    "model": "--model",
    "lead_ms": "--lead-ms",
    "offset_seconds": "--offset-seconds",
    "range_start": "--range-start",
    "range_start_seconds": "--range-start",
    "range_end": "--range-end",
    "range_end_seconds": "--range-end",
    "end_pad_ms": "--end-pad-ms",
    "min_duration_ms": "--min-duration-ms",
    "max_duration_ms": "--max-duration-ms",
    "min_gap_ms": "--min-gap-ms",
}


def _override_flags(overrides: Mapping[str, Any] | None) -> list[str]:
    """Traduza overrides em flags explícitas do CLI (boleanos sem valor)."""
    flags: list[str] = []
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key in {"auto_segment", "partial_range", "timeline_anchor", "no_separate"}:
            if value:
                flags.append(_OVERRIDE_FLAGS.get(key, "--" + key.replace("_", "-")))
        elif key == "separate":
            if not value:
                flags.append("--no-separate")
        else:
            flag = _OVERRIDE_FLAGS.get(key)
            if flag is not None:
                flags.extend((flag, str(value)))
    return flags


def generate_alignment(
    *,
    project_dir: str | Path,
    media: str | Path,
    lyrics_text: str,
    profile_name: str,
    output: str | Path | None = None,
    work: str | Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    strict_qc: bool = False,
) -> dict[str, Any]:
    """Gere ``lyrics.srt``, o JSON de debug e o relatorio de QC.

    Por padrao os arquivos ficam em ``<project_dir>/saida/lyrics.srt`` e
    ``<project_dir>/trabalho/cli/``; ``output`` e ``work`` permitem ao chamador
    manter os caminhos historicos (e o cache de SRT) sem alterar esses padroes.
    """
    project = Path(project_dir)
    srt = Path(output) if output is not None else project / "saida" / "lyrics.srt"
    work_dir = Path(work) if work is not None else project / "trabalho" / "cli"
    work_dir.mkdir(parents=True, exist_ok=True)
    srt.parent.mkdir(parents=True, exist_ok=True)
    debug = work_dir / "alignment-debug.json"
    qc_path = work_dir / "qc.json"
    lyrics_path = work_dir / "lyrics-input.txt"
    lyrics_path.write_text(lyrics_text, encoding="utf-8-sig")

    command = [
        str(console_python(sys.executable)), "-m", "lyrics_aligner", "align",
        str(media), str(lyrics_path), "-o", str(srt),
        "--profile", str(profile_name),
        "--debug-json", str(debug),
        "--work-dir", str(work_dir),
        *_override_flags(overrides),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(APP_PATH)
    env["PYTHONIOENCODING"] = "utf-8"
    result = run_job(
        command,
        env=env,
        cancel_requested=cancel_requested,
        on_output=(lambda line: progress(line)) if progress is not None else None,
    )
    if result.returncode != 0:
        raise RuntimeError((result.output or "").strip() or "Falha no alinhamento")

    # O QC so roda depois de o SRT existir: ele usa as cues e o debug do CLI.
    cues = parse_srt(srt.read_text(encoding="utf-8-sig"))
    qc = qc_report(cues, debug)
    qc_path.write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")
    if strict_qc and qc["warnings"]:
        raise RuntimeError("QC rigoroso: " + "; ".join(qc["warnings"]))
    return {
        "srt": srt,
        "debug": debug,
        "qc": qc,
        "warnings": qc["warnings"],
        "qc_path": qc_path,
        "cues": cues,
    }
