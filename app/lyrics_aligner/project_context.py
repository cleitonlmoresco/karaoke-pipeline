"""Shared, read-only project context for the tkinter tabs.

The project root is always read from the unified settings (``projects_root``).
Discovery mirrors the webapp's convention: direct children of the root are
project folders when they have ``projeto.json``, media files (up to depth 2)
or a ``saida`` directory.  Discovering never writes anything; only an explicit
:meth:`ProjectContext.activate` may create a minimal ``projeto.json`` for a
legacy folder that does not have one yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import settings as settings_module
from .project import read_project_meta, write_project_meta

# Same media extensions and exclusions as the webapp's ``_project_media_files``.
_MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mts", ".m2ts",
    ".wmv", ".flv", ".f4v", ".mpg", ".mpeg", ".m2v", ".vob", ".ts",
    ".mxf", ".ogv", ".3gp", ".3g2", ".asf", ".rm", ".rmvb", ".dv",
    ".mp3", ".mp2", ".mpga", ".wav", ".m4a", ".m4b", ".aac", ".flac",
    ".alac", ".ogg", ".oga", ".opus", ".spx", ".wma", ".aiff", ".aif",
    ".aifc", ".ape", ".wv",
}
_MEDIA_EXCLUDED_DIRS = {"saida", "trabalho", "fundo"}
_META_FILENAME = "projeto.json"


@dataclass
class ProjectInfo:
    """Read-only snapshot of one project folder."""

    id: str
    name: str
    path: Path
    has_meta: bool = False
    has_lyrics: bool = False
    has_media: bool = False
    has_saida: bool = False
    updated_at: str | None = None
    warnings: list[str] = field(default_factory=list)


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _has_media_files(project_dir: Path) -> bool:
    """Return whether media exists inside a folder (depth <= 2, read-only)."""

    def visit(directory: Path, depth: int) -> bool:
        if depth > 2:
            return False
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            return False
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if entry.name.casefold() not in _MEDIA_EXCLUDED_DIRS and visit(
                    entry, depth + 1
                ):
                    return True
            elif entry.suffix.casefold() in _MEDIA_EXTENSIONS:
                return True
        return False

    return visit(project_dir, 0)


def _describe(path: Path) -> ProjectInfo:
    meta_path = path / _META_FILENAME
    meta_exists = meta_path.is_file()
    meta: Mapping[str, Any] = read_project_meta(meta_path) if meta_exists else {}
    warnings: list[str] = []
    if meta_exists and not meta:
        warnings.append("projects_warn_bad_meta")
    elif not meta_exists:
        warnings.append("projects_warn_no_meta")

    return ProjectInfo(
        id=path.name,
        name=str(meta.get("name") or path.name),
        path=path,
        has_meta=meta_exists,
        has_lyrics=(path / "letra.txt").is_file(),
        has_media=_has_media_files(path),
        has_saida=(path / "saida").is_dir(),
        updated_at=(str(meta["updated_at"]) if meta.get("updated_at") else None),
        warnings=warnings,
    )


class ProjectContext:
    """Single source of truth for the active project folder and its paths."""

    def __init__(self, settings_path: str | Path | None = None) -> None:
        self.settings_path = settings_path
        configured = settings_module.get_setting(
            "projects_root", "", settings_path
        )
        # No creation here: a missing/invalid root simply yields no projects.
        self.root = Path(str(configured)) if str(configured or "").strip() else None
        self.active_dir: Path | None = None
        self._subscribers: list[Callable[..., Any]] = []

    # ---- discovery (strictly read-only) ----
    def discover(self) -> list[ProjectInfo]:
        """List project folders under ``root`` without writing to disk."""
        if self.root is None:
            return []
        try:
            children = sorted(
                self.root.iterdir(), key=lambda item: item.name.casefold()
            )
        except OSError:
            return []
        result: list[ProjectInfo] = []
        for entry in children:
            try:
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
            except OSError:
                continue
            info = _describe(entry)
            # ``letra.txt`` is also a legacy project marker (the webapp uses
            # meta/media/saida; a folder with only the lyrics is still useful
            # to the desktop tabs and is classified with a warning).
            if info.has_meta or info.has_media or info.has_saida or info.has_lyrics:
                result.append(info)
        return result

    def active_info(self) -> ProjectInfo | None:
        """Describe the active folder without changing it."""
        if self.active_dir is None:
            return None
        return _describe(self.active_dir)

    # ---- activation (the only path that may create ``projeto.json``) ----
    def activate(self, path: str | Path | ProjectInfo) -> Path:
        """Validate ``path`` under ``root`` and make it the active folder.

        A legacy folder without ``projeto.json`` receives a minimal, additive
        meta file.  An existing meta file is never read-modify-written here, so
        its bytes (and every legacy key) stay exactly as they were.
        """
        raw = path.path if isinstance(path, ProjectInfo) else path
        target = Path(raw).expanduser()
        if self.root is None:
            raise ValueError("Pasta de projetos nao configurada.")
        root_resolved = self.root.resolve()
        try:
            resolved = target.resolve()
        except OSError as exc:
            raise ValueError(f"Caminho de projeto invalido: {target}") from exc
        if resolved == root_resolved:
            raise ValueError("A raiz de projetos nao e um projeto.")
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError(f"Projeto fora da raiz configurada: {target}") from exc
        if not resolved.is_dir():
            raise FileNotFoundError(f"Pasta de projeto nao encontrada: {target}")

        meta_path = resolved / _META_FILENAME
        if not meta_path.is_file():
            stamp = _now()
            write_project_meta(
                meta_path,
                {
                    "name": resolved.name,
                    "created_at": stamp,
                    "updated_at": stamp,
                    "media": "",
                    "outputs": {},
                },
            )

        self.active_dir = resolved
        self.notify()
        return resolved

    # ---- derived paths ----
    def saida(self) -> Path | None:
        return self.active_dir / "saida" if self.active_dir is not None else None

    def trabalho(self) -> Path | None:
        return self.active_dir / "trabalho" if self.active_dir is not None else None

    def lyrics(self) -> Path | None:
        return self.active_dir / "letra.txt" if self.active_dir is not None else None

    def srt(self) -> Path | None:
        saida = self.saida()
        return saida / "lyrics.srt" if saida is not None else None

    # ---- change notifications ----
    def subscribe(self, callback: Callable[..., Any]) -> Callable[[], None]:
        """Register ``callback(context)``; return an unsubscribe function."""
        if callable(callback) and callback not in self._subscribers:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def notify(self) -> int:
        """Notify subscribers; return how many callbacks ran successfully."""
        done = 0
        for callback in list(self._subscribers):
            try:
                try:
                    callback(self)
                except TypeError:
                    callback()
                done += 1
            except Exception:
                # A broken tab must never prevent activation.
                continue
        return done


def iter_project_paths(context: ProjectContext) -> Iterable[Path]:
    """Convenience helper for tabs that only need the folder paths."""
    return (info.path for info in context.discover())
