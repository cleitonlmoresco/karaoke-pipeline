"""Bridge to the installed OpenCode CLI for the hymn creation assistant."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

INSTALL_DIR = Path(__file__).resolve().parents[2]

# Apenas candidatos fixos: nunca varredura recursiva (rglob) na inicializacao.
OPENCODE_CANDIDATES = [
    INSTALL_DIR / "opencode-cli.exe",
    INSTALL_DIR / "tools" / "opencode-cli.exe",
    Path(r"C:\Program Files\OpenCode\opencode-cli.exe"),
]


def _local_app_data_candidates() -> list[Path]:
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        return []
    programs = Path(root) / "Programs"
    return [
        programs / "OpenCode" / "opencode-cli.exe",
        programs / "opencode" / "opencode-cli.exe",
        programs / "ai.opencode.desktop" / "resources" / "opencode-cli.exe",
        programs / "opencode.desktop" / "resources" / "opencode-cli.exe",
    ]


def _cli_in_path(name: str) -> Path | None:
    """PATH sem ``shutil.which`` extra e sem varredura recursiva."""
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry.strip().strip('"')) / name
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


_UNRESOLVED = object()
_opencode_cache: Path | None | object = _UNRESOLVED


def _resolve_opencode() -> Path | None:
    # Uma unica chamada a which (PATH).  O nome alternativo e procurado
    # diretamente em PATH, sem outra resolucao.
    found = shutil.which("opencode")
    if found:
        return Path(found).resolve()
    found_cli = _cli_in_path("opencode-cli.exe") or _cli_in_path("opencode-cli")
    if found_cli:
        return found_cli
    for candidate in (*OPENCODE_CANDIDATES, *_local_app_data_candidates()):
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def find_opencode() -> Path | None:
    """Resolve o CLI uma unica vez por processo (cache explicito)."""
    global _opencode_cache
    if _opencode_cache is _UNRESOLVED:
        _opencode_cache = _resolve_opencode()
    return _opencode_cache  # type: ignore[return-value]


def invalidate_opencode_cache() -> None:
    """Descarta a resolucao em cache (util apos instalar/mover o CLI)."""
    global _opencode_cache
    _opencode_cache = _UNRESOLVED


# Forma alternativa de invalidar, compativel com ``functools.cache``.
find_opencode.cache_clear = invalidate_opencode_cache  # type: ignore[attr-defined]


def run_prompt(prompt: str, *, model: str | None = None, timeout: int = 120) -> str:
    """Run a prompt through OpenCode and return the text response."""
    oc = find_opencode()
    if oc is None:
        raise RuntimeError("OpenCode CLI não encontrado. Instale o OpenCode Desktop.")

    cmd = [str(oc), "run", prompt, "--format", "json", "--auto"]
    if model:
        cmd += ["--model", model]

    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"OpenCode falhou (código {completed.returncode}): {detail[:500]}")

    # Parse JSON lines, collect text parts
    texts: list[str] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "text":
            part = obj.get("part", {})
            text = part.get("text")
            if text:
                texts.append(text)
    if texts:
        return "\n".join(texts)
    # Fallback: return raw stdout
    return completed.stdout.strip() or completed.stderr.strip()


def list_models() -> list[str]:
    oc = find_opencode()
    if oc is None:
        return []
    completed = subprocess.run(
        [str(oc), "models"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]
