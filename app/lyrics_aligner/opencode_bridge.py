"""Bridge to the installed OpenCode CLI for the hymn creation assistant."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

OPENCODE_CANDIDATES = [
    Path(r"C:\Users\IEAD Midia Canarana\AppData\Local\Programs\ai.opencode.desktop\resources\opencode-cli.exe"),
    Path(r"C:\Program Files\OpenCode\opencode-cli.exe"),
]


def find_opencode() -> Path | None:
    # PATH first
    found = shutil.which("opencode") or shutil.which("opencode-cli")
    if found:
        return Path(found)
    for cand in OPENCODE_CANDIDATES:
        if cand.is_file():
            return cand
    # search common locations
    for root in (Path.home(), Path(r"C:\Users")):
        for p in root.rglob("opencode-cli.exe"):
            return p
    return None


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
