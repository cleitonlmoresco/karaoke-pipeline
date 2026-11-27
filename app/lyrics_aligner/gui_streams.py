"""Safe standard streams for GUI (``pythonw``) execution.

On Windows, ``pythonw.exe`` starts with ``sys.stdout``, ``sys.stderr`` and
``sys.stdin`` set to ``None``.  A lot of third-party code (Demucs, Torch, Click,
tqdm, ...) calls ``isatty()`` on those streams while importing or running,
which would otherwise raise ``AttributeError: 'NoneType' object has no
attribute 'isatty'`` inside the tkinter app.

Call :func:`ensure_gui_streams` once, before importing heavy backends.
"""

from __future__ import annotations

import sys


class _GuiStream:
    """Minimal, harmless text stream used when there is no console."""

    encoding = "utf-8"
    errors = "replace"
    mode = "w"

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, text: str) -> int:
        return len(str(text))

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        return None

    def read(self, size: int = -1) -> str:
        return ""

    def readline(self, size: int = -1) -> str:
        return ""

    def fileno(self) -> int:
        raise OSError("no console attached (pythonw)")

    def close(self) -> None:
        return None

    @property
    def closed(self) -> bool:
        return False


def ensure_gui_streams() -> None:
    """Replace missing std streams with a no-op object that behaves like a TTY-less stream."""
    for name in ("stdin", "stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _GuiStream())
