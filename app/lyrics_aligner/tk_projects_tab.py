"""Projects tab: read-only discovery plus explicit activation of a project."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from weakref import WeakSet

from . import settings as settings_module
from .i18n import mark, on_language_changed, t
from .project_context import ProjectContext, ProjectInfo

_HEADINGS = (
    ("name", "projects_col_name"),
    ("status", "projects_col_status"),
    ("updated", "projects_col_updated"),
)
_BUTTONS = (
    ("projects_button_select", "_select"),
    ("projects_button_open", "_open_explorer"),
    ("projects_button_create", "_create"),
    ("projects_button_refresh", "_refresh"),
)
# Larguras BASE em px: nunca multiplicamos a largura atual, sempre a base.
_COLUMN_WIDTHS = (("name", 320), ("status", 150), ("updated", 170))
_BASE_ROWHEIGHT = 20


def _ui_scale(widget):
    """Provider opcional no root da janela; ``None`` = escala neutra."""
    try:
        return getattr(widget.winfo_toplevel(), "ui_scale", None)
    except Exception:
        return None


def _base_factor(widget) -> tuple[float, object]:
    """``(fator, provider)`` com fallback neutro (1.0, sem provider)."""
    provider = _ui_scale(widget)
    factor_fn = getattr(provider, "factor", None)
    if not callable(factor_fn):
        return 1.0, None
    try:
        value = float(factor_fn())
    except Exception:
        return 1.0, None
    return (value if value > 0 else 1.0), provider


class ProjectsTab:
    """List the folders under ``projects_root`` and activate one on demand."""

    def __init__(self, app, parent: ttk.Frame) -> None:
        self.app = app
        self.parent = parent
        self.context: ProjectContext = getattr(app, "project_context", None)
        if self.context is None:
            self.context = ProjectContext(
                settings_path=getattr(app, "settings_path", None)
            )
            try:
                app.project_context = self.context
            except Exception:
                pass
        self._items: dict[str, ProjectInfo] = {}
        self._build()
        _PROJECTS_TABS.add(self)

    # ---- construction ----
    def _build(self) -> None:
        frame = ttk.LabelFrame(self.parent, text=t("projects_title"))
        mark(frame, "projects_title")
        frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.tree = ttk.Treeview(
            list_frame,
            columns=tuple(column for column, _ in _HEADINGS),
            show="headings",
            height=12,
        )
        self._apply_headings()
        self.tree.heading("name", command=self._refresh)
        for column, _base_width in _COLUMN_WIDTHS:
            self.tree.column(column, anchor="w")
        self._apply_tree_scale()
        self._register_scale()
        scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        buttons = ttk.Frame(frame)
        buttons.pack(fill=tk.X, padx=4, pady=4)
        for key, method in _BUTTONS:
            button = ttk.Button(buttons, text=t(key), command=getattr(self, method))
            mark(button, key)
            button.pack(side=tk.LEFT, padx=4)

        self._refresh()

    def _apply_headings(self) -> None:
        """Headings have no i18n binding kind; the module hook refreshes them."""
        for column, key in _HEADINGS:
            self.tree.heading(column, text=t(key))

    # ---- escala visual (somente px; sem logica funcional) ---------------
    def _apply_tree_scale(self) -> None:
        """Reaplica larguras BASE e rowheight com o fator atual do root."""
        tree = getattr(self, "tree", None)
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                return
        except Exception:
            return
        factor, provider = _base_factor(tree)
        try:
            for column, base_width in _COLUMN_WIDTHS:
                tree.column(column, width=max(1, round(base_width * factor)))
            # Sem provider (app legado) preserva o rowheight padrao do Tk.
            if provider is not None:
                ttk.Style(tree).configure(
                    "Treeview", rowheight=max(1, round(_BASE_ROWHEIGHT * factor))
                )
        except Exception:
            pass

    def _register_scale(self) -> None:
        """Um unico callback por instancia; widgets destruidos sao ignorados."""
        if getattr(self, "_scale_registered", False):
            return
        provider = _ui_scale(self.tree)
        on_apply = getattr(provider, "on_apply", None)
        if not callable(on_apply):
            return
        on_apply(self._apply_tree_scale)
        self._scale_registered = True

    # ---- helpers ----
    def _status(self, text: str) -> None:
        setter = getattr(self.app, "set_status", None)
        if callable(setter):
            try:
                setter(text)
            except Exception:
                pass

    def _log(self, text: str) -> None:
        logger = getattr(self.app, "log", None)
        if callable(logger):
            try:
                logger(text)
            except Exception:
                pass

    def _selected_info(self) -> ProjectInfo | None:
        selection = self.tree.selection()
        return self._items.get(selection[0]) if selection else None

    def _select(self, _event=None) -> None:
        info = self._selected_info()
        if info is None:
            self._status(t("projects_status_no_selection"))
            return
        try:
            self.context.activate(info.path)
        except (ValueError, FileNotFoundError, OSError) as exc:
            message = t("projects_status_activate_fail", exc=exc)
            self._status(message)
            self._log(message)
            return
        message = t("projects_status_selected", name=info.name)
        self._status(message)
        self._log(message)
        self._refresh()

    def _open_explorer(self, _event=None) -> None:
        info = self._selected_info()
        if info is None:
            self._status(t("projects_status_no_selection"))
            return
        if not info.path.is_dir():
            message = t("projects_status_missing", path=str(info.path))
            self._status(message)
            self._log(message)
            return
        try:
            if sys.platform == "win32":
                try:
                    # explorer.exe brings the window to the front; startfile
                    # is the legacy fallback when explorer cannot be spawned.
                    subprocess.Popen(["explorer.exe", str(info.path)])
                except (FileNotFoundError, OSError):
                    os.startfile(str(info.path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(info.path)])
        except (OSError, ValueError) as exc:
            message = t("projects_status_open_fail", exc=exc)
            self._status(message)
            self._log(message)
            return
        message = t("projects_status_opened", path=str(info.path))
        self._status(message)
        self._log(message)

    def _create(self, name: str | None = None, _event=None) -> None:
        """Create a folder under the configured root (no meta file is written)."""
        if name is None:
            import tkinter.simpledialog as sd

            name = sd.askstring(
                t("projects_create_title"),
                t("projects_create_prompt"),
                parent=self.parent,
            )
        if not name:
            return
        cleaned = str(name).strip()
        invalid = set('\\/:*?"<>|')
        if not cleaned or cleaned.startswith(".") or any(c in invalid for c in cleaned):
            self._status(t("projects_create_invalid"))
            return
        root = self.context.root
        if root is None or not root.is_dir():
            self._status(t("projects_status_outside_root"))
            return
        target: Path = root / cleaned
        try:
            target.mkdir(exist_ok=False)
        except OSError as exc:
            message = t("projects_status_create_fail", exc=exc)
            self._status(message)
            self._log(message)
            return
        self._refresh()
        iid = str(target)
        if iid in self._items:
            self.tree.selection_set(iid)
        message = t("projects_status_created", name=cleaned)
        self._status(message)
        self._log(message)

    def _refresh(self, _event=None) -> list[ProjectInfo]:
        """Re-read the folders; discovery itself never writes to disk."""
        # Settings may have changed since construction (e.g. in Configuracoes);
        # re-read the configured root on every refresh without touching
        # ``active_dir`` and without writing anything back to settings.json.
        configured = settings_module.get_setting(
            "projects_root", "", self.context.settings_path
        )
        self.context.root = (
            Path(str(configured)) if str(configured or "").strip() else None
        )
        selected = self.tree.selection() if hasattr(self, "tree") else ()
        selected_path = self._items.get(selected[0]).path if selected else None
        if hasattr(self, "tree"):
            self.tree.delete(*self.tree.get_children())
        self._items.clear()
        infos = self.context.discover()
        active = self.context.active_dir
        for info in infos:
            status = ", ".join(t(warning) for warning in info.warnings) or (
                t("projects_status_with_meta")
                if info.has_meta
                else t("projects_status_no_meta")
            )
            iid = str(info.path)
            self.tree.insert("", "end", iid=iid, values=(info.name, status, info.updated_at or ""))
            self._items[iid] = info
        preferred = str(selected_path) if selected_path else (
            str(active) if active is not None else ""
        )
        if preferred in self._items:
            self.tree.selection_set(preferred)
        if not infos:
            self._status(t("projects_empty"))
        return infos


_PROJECTS_TABS: WeakSet["ProjectsTab"] = WeakSet()


def _on_language_changed(_lang: str) -> None:
    for tab in list(_PROJECTS_TABS):
        try:
            if tab.tree.winfo_exists():
                tab._apply_headings()
                tab._refresh()
        except Exception:
            pass


on_language_changed(_on_language_changed)
