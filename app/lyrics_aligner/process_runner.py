from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class JobCancelled(RuntimeError):
    pass


class JobTimeout(RuntimeError):
    """Raised by :func:`run_job` when its optional deadline expires."""

    def __init__(self, timeout: float, output: str = "") -> None:
        super().__init__(f"Job timed out after {timeout} seconds")
        self.timeout = timeout
        self.output = output


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    output: str


def console_python(python: str | Path) -> Path:
    """Return the console interpreter for a GUI Python launcher.

    ``pythonw.exe`` has no console, so its output pipes are useless to the
    streaming runner.  The sibling ``python.exe`` is returned instead; all
    other paths (including the console interpreter itself) pass through.
    """
    path = Path(python)
    if path.name.casefold() == "pythonw.exe":
        return path.with_name("python.exe")
    return path


def run_streaming_process(
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    on_output: Callable[[str], None] | None = None,
    poll_interval: float = 0.1,
) -> ProcessResult:
    """Run a child process while streaming merged UTF-8 output and supporting cancel."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    popen_options = {}
    if os.name == "nt":
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_options["start_new_session"] = True
    child_env = os.environ.copy()
    if env is not None:
        child_env.update(env)
    child_env.setdefault("PYTHONUTF8", "1")
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd) if cwd is not None else None,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
        **popen_options,
    )
    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                output_queue.put(line)
        finally:
            output_queue.put(None)

    reader = threading.Thread(target=read_output, name="lyrics-aligner-output", daemon=True)
    reader.start()
    collected: list[str] = []
    reader_done = False

    try:
        while process.poll() is None or not reader_done:
            if cancel_requested is not None and cancel_requested():
                _terminate_process_tree(process)
                raise JobCancelled("Job cancelled")
            try:
                item = output_queue.get(timeout=poll_interval)
            except queue.Empty:
                continue
            if item is None:
                reader_done = True
                continue
            collected.append(item)
            if on_output is not None:
                on_output(item.rstrip("\r\n"))
        return ProcessResult(returncode=int(process.returncode or 0), output="".join(collected))
    finally:
        if process.poll() is None:
            _terminate_process_tree(process)
        reader.join(timeout=0.5)
        # Closing a pipe while another thread is blocked in read() can itself
        # block on Windows. The daemon reader owns it until the process tree is
        # gone, so only close synchronously once the reader has finished.
        if process.stdout is not None and not reader.is_alive():
            process.stdout.close()


def run_job(
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    on_output: Callable[[str], None] | None = None,
    poll_interval: float = 0.1,
    timeout: float | None = None,
) -> ProcessResult:
    """Run a job with the streaming runner, optionally with a deadline.

    This is a strict superset of :func:`run_streaming_process`: every
    pre-existing parameter keeps its name, position-independent default and
    semantics.  With ``timeout=None`` the call is delegated verbatim, so the
    behaviour is byte-for-byte identical.  No ``check=True`` is used: a
    non-zero return code is reported in :class:`ProcessResult`.
    """
    if timeout is None:
        return run_streaming_process(
            command,
            cwd=cwd,
            env=env,
            cancel_requested=cancel_requested,
            on_output=on_output,
            poll_interval=poll_interval,
        )
    if timeout <= 0:
        raise JobTimeout(timeout)

    expired = threading.Event()
    caller_cancel = threading.Event()

    def should_cancel() -> bool:
        # Consultar o chamador primeiro preserva a origem do cancelamento:
        # so assim a corrida cancel+timeout pode priorizar o cancelamento.
        if cancel_requested is not None and cancel_requested():
            caller_cancel.set()
            return True
        return expired.is_set()

    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["result"] = run_streaming_process(
                command,
                cwd=cwd,
                env=env,
                cancel_requested=should_cancel,
                on_output=on_output,
                poll_interval=poll_interval,
            )
        except JobCancelled as exc:
            box["cancelled"] = exc
            box["caller_cancel"] = caller_cancel.is_set()
        except BaseException as exc:  # preserved for the caller below
            box["error"] = exc

    thread = threading.Thread(target=worker, name="lyrics-aligner-job", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        expired.set()
        # run_streaming_process notices the flag within poll_interval and
        # terminates the whole process tree before returning.
        thread.join()

    # Um cancelamento explicito do chamador tem prioridade sobre o deadline:
    # se ambos acontecem na mesma janela, recebe JobCancelled. Cancelamentos
    # induzidos pelo proprio deadline continuam sendo reportados como Timeout.
    if "cancelled" in box:
        if box.get("caller_cancel"):
            raise box["cancelled"]
        raise JobTimeout(timeout)
    if "error" in box:
        raise box["error"]
    result = box.get("result")
    if not isinstance(result, ProcessResult):
        raise RuntimeError("Job finished without a result")
    if expired.is_set():
        raise JobTimeout(timeout, result.output)
    return result


def _terminate_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    try:
        process.wait(timeout=1)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill()
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass
