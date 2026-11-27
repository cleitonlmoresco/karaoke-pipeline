"""Global burn lock to prevent parallel FFmpeg burns (Bloco A3)."""

from __future__ import annotations

import os
import time
from pathlib import Path


LOCK_PATH = Path(os.environ.get("TEMP", ".")) / "vilm_burn.lock"


class BurnLock:
    def __init__(self, timeout: float = 300.0) -> None:
        self.timeout = timeout
        self._acquired = False

    def acquire(self) -> bool:
        start = time.time()
        while time.time() - start < self.timeout:
            try:
                # O_CREAT | O_EXCL ensures atomic creation
                fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self._acquired = True
                return True
            except FileExistsError:
                # Check if the holding process is still alive
                try:
                    pid = int(LOCK_PATH.read_text().strip())
                    # On Windows, check if process exists
                    import ctypes
                    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                    if handle:
                        ctypes.windll.kernel32.CloseHandle(handle)
                        time.sleep(1.0)
                        continue
                    else:
                        # Process dead — remove stale lock
                        LOCK_PATH.unlink(missing_ok=True)
                        continue
                except Exception:
                    time.sleep(1.0)
                    continue
        return False

    def release(self) -> None:
        if self._acquired:
            try:
                LOCK_PATH.unlink(missing_ok=True)
            except Exception:
                pass
            self._acquired = False

    def __enter__(self) -> "BurnLock":
        if not self.acquire():
            raise RuntimeError("Não foi possível adquirir o lock de burn (timeout).")
        return self

    def __exit__(self, *exc) -> None:
        self.release()
