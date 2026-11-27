"""Advanced settings window: themes, colors, font sizes, system configuration.

Values are persisted through ``settings`` (``settings.json``).  The old
``~/.vilm_tkinter_config.json`` path is kept only as a legacy constant and is
never read or written again.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser, ttk
from pathlib import Path

from .i18n import t
from .settings import load_settings, save_settings

CONFIG_PATH = Path.home() / ".vilm_tkinter_config.json"
# Text fields that may be intentionally cleared to an empty string.
_CLEARABLE_KEYS = {"projects_root", "font_family"}

DEFAULT_ADVANCED = {
    "theme": "light",
    "font_family": "Segoe UI",
    "font_size": 10,
    "bg_color": "#F4EFE0",
    "fg_color": "#1A1A1A",
    "accent_color": "#043D20",
    "button_bg": "#EDE8DA",
    "window_width": 1100,
    "window_height": 720,
    "log_font_size": 9,
    "projects_root": "",
}


def load_advanced_config(path: str | Path | None = None) -> dict:
    """Read advanced keys from the unified settings file (never the legacy one)."""
    try:
        payload = load_settings(path)
    except Exception:
        return dict(DEFAULT_ADVANCED)
    result = dict(DEFAULT_ADVANCED)
    for key in DEFAULT_ADVANCED:
        value = payload.get(key)
        if value is not None and (value != "" or key in _CLEARABLE_KEYS):
            result[key] = value
    return result


def save_advanced_config(cfg: dict, path: str | Path | None = None) -> None:
    """Persist advanced keys via settings; empty text fields are explicit clears."""
    payload = {
        key: value
        for key, value in cfg.items()
        if key in DEFAULT_ADVANCED
        and value is not None
        and (value != "" or key in _CLEARABLE_KEYS)
    }
    save_settings(payload, path)


THEMES = {
    "light": {
        "bg_color": "#F4EFE0", "fg_color": "#1A1A1A",
        "accent_color": "#043D20", "button_bg": "#EDE8DA",
    },
    "dark": {
        "bg_color": "#1E1E1E", "fg_color": "#E0E0E0",
        "accent_color": "#4CAF50", "button_bg": "#2D2D2D",
    },
    "blue": {
        "bg_color": "#E3F2FD", "fg_color": "#0D47A1",
        "accent_color": "#1976D2", "button_bg": "#BBDEFB",
    },
}


class AdvancedSettingsWindow:
    def __init__(self, parent: tk.Tk, app, settings_path: str | Path | None = None) -> None:
        self.parent = parent
        self.app = app
        self.settings_path = settings_path
        self.cfg = load_advanced_config(self.settings_path)
        self.window = tk.Toplevel(parent)
        self.window.title(t("menu_advanced"))
        self.window.geometry("520x600")
        self.window.transient(parent)

        self.vars: dict[str, tk.Variable] = {}
        self._build()

    def _build(self) -> None:
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        # --- Theme tab ---
        theme_frame = ttk.Frame(notebook)
        notebook.add(theme_frame, text=t("tab_theme"))
        self._build_theme_tab(theme_frame)

        # --- Colors tab ---
        color_frame = ttk.Frame(notebook)
        notebook.add(color_frame, text=t("tab_colors"))
        self._build_color_tab(color_frame)

        # --- Fonts tab ---
        font_frame = ttk.Frame(notebook)
        notebook.add(font_frame, text=t("tab_fonts"))
        self._build_font_tab(font_frame)

        # --- System tab ---
        sys_frame = ttk.Frame(notebook)
        notebook.add(sys_frame, text=t("tab_system"))
        self._build_system_tab(sys_frame)

        # Buttons
        btn_frame = ttk.Frame(self.window)
        btn_frame.pack(fill="x", padx=8, pady=8)
        ttk.Button(btn_frame, text=t("apply"), command=self._apply).pack(side="left", padx=4)
        ttk.Button(btn_frame, text=t("save_close"), command=self._save_and_close).pack(side="left", padx=4)
        ttk.Button(btn_frame, text=t("restore_defaults"), command=self._reset).pack(side="left", padx=4)

    def _build_theme_tab(self, parent) -> None:
        ttk.Label(parent, text=t("theme_label")).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.v_theme = tk.StringVar(value=self.cfg["theme"])
        combo = ttk.Combobox(parent, textvariable=self.v_theme, values=list(THEMES.keys()), state="readonly", width=12)
        combo.grid(row=0, column=1, sticky="w", padx=8, pady=6)
        combo.bind("<<ComboboxSelected>>", self._on_theme_change)

        ttk.Label(parent, text=t("preview_label")).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.preview = tk.Label(parent, text=t("sample_text"), padx=12, pady=8)
        self.preview.grid(row=1, column=1, sticky="w", padx=8, pady=6)
        self._update_preview()

    def _on_theme_change(self, _event=None) -> None:
        theme = THEMES.get(self.v_theme.get())
        if theme:
            for k, v in theme.items():
                self.cfg[k] = v
                if k in self.vars:
                    self.vars[k].set(v)
            self._update_preview()

    def _build_color_tab(self, parent) -> None:
        colors = [
            ("bg_color", t("color_bg_label")),
            ("fg_color", t("color_fg_label")),
            ("accent_color", t("color_accent_label")),
            ("button_bg", t("color_button_label")),
        ]
        row = 0
        for key, label in colors:
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
            var = tk.StringVar(value=self.cfg[key])
            self.vars[key] = var
            entry = ttk.Entry(parent, textvariable=var, width=12)
            entry.grid(row=row, column=1, sticky="w", padx=4, pady=4)
            btn = ttk.Button(parent, text="...", width=3, command=lambda k=key, v=var: self._pick_color(k, v))
            btn.grid(row=row, column=2, padx=4, pady=4)
            swatch = tk.Label(parent, width=4, bg=self.cfg[key], relief="solid", borderwidth=1)
            swatch.grid(row=row, column=3, padx=4, pady=4)
            self.vars[key + "_swatch"] = swatch
            row += 1

    def _pick_color(self, key: str, var: tk.StringVar) -> None:
        color = colorchooser.askcolor(color=var.get(), title=f"Escolher cor: {key}")
        if color and color[1]:
            var.set(color[1])
            swatch = self.vars.get(key + "_swatch")
            if swatch:
                swatch.config(bg=color[1])
            self.cfg[key] = color[1]
            self._update_preview()

    def _build_font_tab(self, parent) -> None:
        ttk.Label(parent, text=t("font_family_label")).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.v_font_family = tk.StringVar(value=self.cfg["font_family"])
        ttk.Entry(parent, textvariable=self.v_font_family, width=20).grid(row=0, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(parent, text=t("font_size_label")).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.v_font_size = tk.IntVar(value=self.cfg["font_size"])
        ttk.Spinbox(parent, from_=8, to=24, textvariable=self.v_font_size, width=6).grid(row=1, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(parent, text=t("log_font_size_label")).grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.v_log_font_size = tk.IntVar(value=self.cfg["log_font_size"])
        ttk.Spinbox(parent, from_=7, to=16, textvariable=self.v_log_font_size, width=6).grid(row=2, column=1, sticky="w", padx=8, pady=6)

    def _build_system_tab(self, parent) -> None:
        ttk.Label(parent, text=t("window_width_label")).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.v_win_w = tk.IntVar(value=self.cfg["window_width"])
        ttk.Spinbox(parent, from_=800, to=2560, textvariable=self.v_win_w, width=8).grid(row=0, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(parent, text=t("window_height_label")).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.v_win_h = tk.IntVar(value=self.cfg["window_height"])
        ttk.Spinbox(parent, from_=600, to=1440, textvariable=self.v_win_h, width=8).grid(row=1, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(parent, text=t("projects_root_label")).grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.v_projects_root = tk.StringVar(value=self.cfg.get("projects_root", ""))
        ttk.Entry(parent, textvariable=self.v_projects_root, width=30).grid(row=2, column=1, sticky="w", padx=8, pady=6)

    def _update_preview(self) -> None:
        try:
            bg = self.vars.get("bg_color", tk.StringVar(value=self.cfg["bg_color"])).get()
            fg = self.vars.get("fg_color", tk.StringVar(value=self.cfg["fg_color"])).get()
            self.preview.config(bg=bg, fg=fg, font=(self.cfg.get("font_family", "Segoe UI"), self.cfg.get("font_size", 10)))
        except Exception:
            pass

    def _collect(self) -> dict:
        cfg = dict(self.cfg)
        cfg["theme"] = self.v_theme.get()
        cfg["font_family"] = self.v_font_family.get()
        cfg["font_size"] = int(self.v_font_size.get())
        cfg["log_font_size"] = int(self.v_log_font_size.get())
        cfg["window_width"] = int(self.v_win_w.get())
        cfg["window_height"] = int(self.v_win_h.get())
        cfg["projects_root"] = self.v_projects_root.get()
        for key in ("bg_color", "fg_color", "accent_color", "button_bg"):
            if key in self.vars:
                cfg[key] = self.vars[key].get()
        return cfg

    def _set_status(self, message: str) -> None:
        setter = getattr(self.app, "set_status", None)
        if callable(setter):
            setter(message)

    def _apply(self) -> None:
        self.cfg = self._collect()
        save_advanced_config(self.cfg, self.settings_path)
        self._update_preview()
        apply_theme = getattr(self.app, "_apply_theme", None)
        if callable(apply_theme):
            apply_theme(self.cfg)
        self._set_status(t("settings_applied"))

    def _save_and_close(self) -> None:
        self._apply()
        self.window.destroy()

    def _reset(self) -> None:
        self.cfg = dict(DEFAULT_ADVANCED)
        save_advanced_config(self.cfg, self.settings_path)
        apply_theme = getattr(self.app, "_apply_theme", None)
        if callable(apply_theme):
            apply_theme(self.cfg)
        self.window.destroy()
        self._set_status(t("settings_restored"))
