"""Staging helper for preparing files for WhatsApp delivery (E7)."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def prepare_staging(
    project_dir: Path,
    finals: Iterable[Path],
    *,
    change_log: list[str] | None = None,
) -> Path:
    """Stage final MP4s into ``enviar_agora`` and archive previous ones.

    Returns the staging directory.
    """

    project_dir = Path(project_dir)
    staging = project_dir / "enviar_agora"
    archive_root = project_dir / "_nao_enviar"
    staging.mkdir(parents=True, exist_ok=True)
    archive_root.mkdir(parents=True, exist_ok=True)

    # Archive previous staged finals
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    previous = [p for p in staging.iterdir() if p.is_file()]
    if previous:
        archive_dir = archive_root / timestamp
        archive_dir.mkdir(parents=True, exist_ok=True)
        for p in previous:
            shutil.move(str(p), str(archive_dir / p.name))

    # Copy new finals
    for final in finals:
        final = Path(final)
        if final.is_file():
            shutil.copy2(final, staging / final.name)

    # Manifesto
    manifesto = staging / "manifesto_envio.md"
    lines = [f"# Envio — {project_dir.name}", "", f"Data: {timestamp}", "", "## Arquivos", ""]
    for final in finals:
        final = Path(final)
        if final.is_file():
            size_mb = final.stat().st_size / (1024 * 1024)
            lines.append(f"- `{final.name}` ({size_mb:.1f} MB)")
    lines.append("")
    if change_log:
        lines.append("## Alterações")
        lines.append("")
        for entry in change_log:
            lines.append(f"- {entry}")
        lines.append("")
    manifesto.write_text("\n".join(lines), encoding="utf-8")

    # Captions sugeridas
    captions = staging / "captions.txt"
    cap_lines = []
    for final in finals:
        final = Path(final)
        if final.is_file():
            cap_lines.append(f"=== {final.name} ===")
            if change_log:
                cap_lines.append("Alterações: " + "; ".join(change_log))
            cap_lines.append("")
    captions.write_text("\n".join(cap_lines), encoding="utf-8")

    return staging
