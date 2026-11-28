"""Global burn lock to prevent parallel FFmpeg burns (Bloco A3).

A API publica historica (``BurnLock(timeout=300.0)`` e ``LOCK_PATH``) fica
inalterada: a GUI continua usando exatamente o mesmo lock global.  O parametro
opcional ``key`` permite locks por diretorio de trabalho/saida e o helper
:func:`burn_guard` estende a protecao aos burns disparados fora da GUI sem
criar um segundo lock quando o processo (ou o processo pai, no caso do
subprocesso da GUI) ja possui um ativo.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK_PATH = Path(os.environ.get("TEMP", ".")) / "vilm_burn.lock"

BURN_LOCK_DEFAULT_TIMEOUT = 300.0
BURN_LOCK_BUSY_MESSAGE = "Não foi possível adquirir o lock de burn: outro burn em andamento."
BURN_LOCK_TIMEOUT_MESSAGE = "Não foi possível adquirir o lock de burn (timeout)."

_REGISTRY_LOCK = threading.RLock()


class _LockState:
    __slots__ = ("owner_thread", "count", "guard")

    def __init__(self, owner_thread: int, guard: bool) -> None:
        self.owner_thread = owner_thread
        self.count = 1
        self.guard = guard


# Registro em processo: permite reconhecer lock ja ativo sem confundir com um
# lock stale deixado por uma execucao anterior com o mesmo PID (PID reaproveitado).
_ACTIVE_LOCKS: dict[str, _LockState] = {}


def lock_path_for(key: str | Path | None = None) -> Path:
    """Arquivo de lock associado a uma chave (diretorio de trabalho/saida).

    ``None`` devolve o lock global historico.  Para uma chave, o nome e
    determinístico e fica junto de ``LOCK_PATH`` (normalmente ``%TEMP%``), sem
    gravar arquivos dentro do diretorio do projeto.
    """
    if key is None:
        return LOCK_PATH
    normalized = str(Path(key).resolve())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return LOCK_PATH.parent / f"vilm_burn_{digest}.lock"


def _process_creation_token(pid: int) -> str:
    """Identificador de criacao do processo (para detectar reuse de PID)."""
    if os.name != "nt" or pid <= 0:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            ok = ctypes.windll.kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time),
            )
            if not ok:
                return ""
            return f"{creation.dwHighDateTime:08x}{creation.dwLowDateTime:08x}"
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return ""


def _read_lock(path: Path) -> tuple[int | None, str]:
    """Le ``pid|token``; aceita tambem o formato legado (somente PID)."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None, ""
    parts = raw.split("|", 1)
    try:
        pid = int(parts[0].strip())
    except (ValueError, IndexError):
        return None, ""
    return pid, (parts[1].strip() if len(parts) > 1 else "")


def _holder_alive(pid: int | None, token: str, *, registered: bool) -> bool:
    """Processo detentor ainda vivo?  PID reaproveitado conta como morto."""
    if pid is None or pid <= 0:
        return False
    if pid == os.getpid():
        # Sem registro local, um lock com o nosso PID e de uma execucao
        # anterior: nao pode bloquear a execucao atual.
        return registered
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                if token:
                    current = _process_creation_token(pid)
                    if current and current != token:
                        return False  # PID reaproveitado: lock stale
                return True
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class BurnLock:
    def __init__(self, timeout: float = BURN_LOCK_DEFAULT_TIMEOUT, *,
                 key: str | Path | None = None, _guard: bool = False) -> None:
        self.timeout = float(timeout)
        self.key = key
        self._guard = _guard
        self._acquired = False

    @property
    def lock_path(self) -> Path:
        return lock_path_for(self.key)

    def acquire(self) -> bool:
        path = self.lock_path
        key = str(path)
        start = time.monotonic()
        stale_cleanups = 0
        while True:
            remaining = self.timeout - (time.monotonic() - start)
            if remaining <= 0:
                return False
            wait_for_holder = False
            # A criacao do arquivo e o registro em processo formam uma unica
            # secao critica: outra thread do mesmo processo nunca deve ver o
            # arquivo antes do registro (isso pareceria lock stale/PID reuse).
            with _REGISTRY_LOCK:
                try:
                    # O_CREAT | O_EXCL ensures atomic creation
                    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    registered = key in _ACTIVE_LOCKS
                    pid, token = _read_lock(path)
                    if not _holder_alive(pid, token, registered=registered):
                        # Lock stale (processo morto OU PID reaproveitado):
                        # limpeza imediata, sem esperar o timeout inteiro.
                        if stale_cleanups >= 3:
                            return False
                        stale_cleanups += 1
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            pass
                        continue
                    wait_for_holder = True
                except OSError:
                    return False
                else:
                    try:
                        token = _process_creation_token(os.getpid())
                        os.write(fd, f"{os.getpid()}|{token}\n".encode("ascii"))
                    finally:
                        os.close(fd)
                    state = _ACTIVE_LOCKS.get(key)
                    if state is None:
                        _ACTIVE_LOCKS[key] = _LockState(threading.get_ident(), self._guard)
                    else:
                        state.count += 1
                    self._acquired = True
                    return True
            if wait_for_holder:
                time.sleep(min(0.2, max(0.01, remaining)))

    def release(self) -> None:
        if not self._acquired:
            return
        path = self.lock_path
        key = str(path)
        with _REGISTRY_LOCK:
            state = _ACTIVE_LOCKS.get(key)
            remove_file = True
            if state is not None:
                state.count -= 1
                if state.count > 0:
                    remove_file = False
                else:
                    _ACTIVE_LOCKS.pop(key, None)
            if remove_file:
                try:
                    pid, token = _read_lock(path)
                    if pid == os.getpid() or not _holder_alive(pid, token, registered=False):
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
        self._acquired = False

    def __enter__(self) -> "BurnLock":
        if not self.acquire():
            raise RuntimeError(BURN_LOCK_TIMEOUT_MESSAGE)
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def has_active_lock(key: str | Path | None = None) -> bool:
    """Um lock deste processo esta ativo? (opcionalmente, o de uma chave)."""
    with _REGISTRY_LOCK:
        if key is not None:
            return str(lock_path_for(key)) in _ACTIVE_LOCKS
        return bool(_ACTIVE_LOCKS)


def _nested_burn_lock_active() -> bool:
    """Lock ativo criado fora de ``burn_guard`` (GUI) ou na mesma thread."""
    ident = threading.get_ident()
    with _REGISTRY_LOCK:
        return any(not state.guard or state.owner_thread == ident
                   for state in _ACTIVE_LOCKS.values())


def _global_lock_held_by_parent() -> bool:
    """A GUI segura o lock global e nos somos o subprocesso do make?"""
    try:
        pid, token = _read_lock(LOCK_PATH)
    except Exception:
        return False
    if pid is None or pid == os.getpid() or pid != os.getppid():
        return False
    return _holder_alive(pid, token, registered=False)


@contextmanager
def burn_guard(path: str | Path | None = None, *, timeout: float | None = None,
               progress: object | None = None) -> Iterator[BurnLock | None]:
    """Protege um burn fora da GUI.

    Se o processo (ou o processo pai, no subprocesso da GUI) ja tiver lock
    ativo, nao cria outro: reutiliza o escopo existente.  Caso contrario
    resolve o lock global e o lock da chave (diretorio de trabalho/saida) na
    mesma ordem em todos os chamadores, evitando deadlock.
    """
    if _nested_burn_lock_active():
        if progress is not None:
            try:
                progress("Lock de burn já ativo neste processo; reutilizando.")
            except Exception:
                pass
        yield None
        return

    wait = BURN_LOCK_DEFAULT_TIMEOUT if timeout is None else float(timeout)
    key_path = lock_path_for(path)
    acquired: list[BurnLock] = []
    try:
        # O subprocesso da GUI herda o lock global do pai; adquiri-lo aqui
        # causaria espera de 300s contra o proprio chamador.
        if not _global_lock_held_by_parent() or key_path == LOCK_PATH:
            global_lock = BurnLock(timeout=wait, _guard=True)
            if not global_lock.acquire():
                raise RuntimeError(BURN_LOCK_BUSY_MESSAGE)
            acquired.append(global_lock)
        if key_path != LOCK_PATH:
            key_lock = BurnLock(timeout=wait, key=path, _guard=True)
            if not key_lock.acquire():
                detail = f" ({key_path.name})" if key_path != LOCK_PATH else ""
                raise RuntimeError(BURN_LOCK_BUSY_MESSAGE + detail)
            acquired.append(key_lock)
        yield acquired[0] if acquired else None
    finally:
        for lock in reversed(acquired):
            lock.release()
