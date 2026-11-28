"""Full configuration window (language, etc.)."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .i18n import (
    available_languages,
    get_language,
    language_label,
    mark,
    t,
)


def _scale_factor(widget) -> float:
    """Fator da casca pelo toplevel do widget; 1.0 sem provider (legado)."""
    try:
        provider = getattr(widget.winfo_toplevel(), "ui_scale", None)
    except Exception:
        return 1.0
    factor_fn = getattr(provider, "factor", None)
    if not callable(factor_fn):
        return 1.0
    try:
        value = float(factor_fn())
    except Exception:
        return 1.0
    return value if value > 0 else 1.0


class ConfigWindow:
    def __init__(
        self, parent: tk.Tk, app, settings_path: str | Path | None = None
    ) -> None:
        self.parent = parent
        self.app = app
        self.settings_path = settings_path
        self.window = tk.Toplevel(parent)
        self.window.title(t("config_window_title"))
        mark(self.window, "config_window_title", kind="title")
        # Geometry escalada apenas na abertura: nada e gravado em settings.
        factor = _scale_factor(parent)
        self.window.geometry(f"{round(400 * factor)}x{round(200 * factor)}")
        self.window.transient(parent)

        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)

        label = ttk.Label(frame, text=t("language"))
        mark(label, "language")
        label.grid(row=0, column=0, sticky="w", padx=4, pady=6)
        codes = available_languages()
        self._labels = [language_label(code) for code in codes]
        self._codes = dict(zip(self._labels, codes))
        current = get_language()
        initial = language_label(current) if current in codes else self._labels[0]
        self.v_lang = tk.StringVar(value=initial)
        combo = ttk.Combobox(frame, textvariable=self.v_lang, values=self._labels,
                             state="readonly", width=12)
        combo.grid(row=0, column=1, sticky="w", padx=4, pady=6)

        btn = ttk.Button(frame, text=t("ok_button"), command=self._apply)
        mark(btn, "ok_button")
        btn.grid(row=1, column=0, columnspan=2, pady=12)

    def _apply(self) -> None:
        selected = self.v_lang.get()
        lang = self._codes.get(selected, selected)
        self.window.destroy()
        # Single funnel: apply_language validates, sets the global language,
        # persists ui_language once, retranslates and reports the status.
        self.app.apply_language(lang)
