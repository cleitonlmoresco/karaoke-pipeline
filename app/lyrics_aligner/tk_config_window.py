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
        self.window.geometry("400x200")
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
