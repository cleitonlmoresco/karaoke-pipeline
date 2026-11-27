"""Settings tab for the tkinter frontend.

The tab reads and writes through the local data layer (``profiles_api`` and
``settings``).  The legacy ``webapp`` module is no longer imported: its
``DEFAULT_SETTINGS`` diverged from the real profiles (max_duration_ms,
font sizes, stanza mode), so the defaults below are profile-oriented and the
UI-only fields are filled locally.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping
from weakref import WeakSet

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from . import profiles_api
from .i18n import mark, mark_tooltip, on_language_changed, t
from .settings import update_settings
from .subtitles import ass_color
from .tk_tooltip import add_tooltip


# Profile-oriented defaults.  Keys absent from ``karaoke_jobs`` defaults
# (offsets, optional font sizes, entry lead, target duration) are UI-only and
# are kept here rather than borrowed from the webapp defaults.
DEFAULT_SETTINGS: dict[str, Any] = {
    "alignment": {
        "mode": "auto",
        "language": "auto",
        "model": "small",
        "separate": True,
        "max_chars": 30,
        "lead_ms": 600,
        "end_pad_ms": 500,
        "min_duration_ms": 500,
        "max_duration_ms": 10000,
        "min_gap_ms": 80,
        "offset_seconds": 0.0,
        "playback_offset_seconds": 0.0,
        "range_start": 0.0,
        "range_end": None,
    },
    "karaoke": {
        "enabled": True,
        "normal_color": "#000000",
        "active_color": "#000000",
        "outline_color": "#000000",
        "back_color": "#00000000",
        "font_name": "Arial",
        "font_size": 84,
        "active_font_size": 84,
        "center_font_size": None,
        "context_font_size": None,
        "bold": True,
        "outline": 0.0,
        "shadow": 0.0,
        "alignment": 2,
        "margin_h": 80,
        "margin_v": 280,
        "lines_before": 0,
        "lines_after": 3,
        "lines_per_slide": 0,
        "stanza_mode": "stanzas",
        "transition": "scroll",
        "transition_ms": 600,
        "entry_lead_ms": 200,
        "scroll_pause_seconds": 2.5,
    },
    "background": {
        "type": "image",
        "color": "#0B1D3A",
        "image_filename": None,
        "fit": "contain",
    },
    "output": {"width": 1920, "height": 1080},
    "burn": {"preset": "medium", "crf": 18, "target_duration_seconds": None},
    "demo": False,
}

# Preset mantido pela aba Tk (rotulo e comportamento inalterados).
SANTO_PRESET: dict[str, Any] = {
    "alignment": {
        "mode": "manual",
        "language": "pt",
        "model": "small",
        "separate": True,
        "max_chars": 80,
        "lead_ms": 0,
        "end_pad_ms": 3000,
        "min_duration_ms": 500,
        "max_duration_ms": 30000,
        "min_gap_ms": 80,
        "offset_seconds": 0.0,
        "playback_offset_seconds": 0.0,
        "range_start": 0.0,
        "range_end": None,
    },
    "karaoke": {
        "enabled": True,
        "normal_color": "#FFFFFF",
        "active_color": "#FFFFFF",
        "outline_color": "#000000",
        "back_color": "#00000080",
        "font_name": "Times New Roman, Georgia, Cambria, DejaVu Serif",
        "font_size": 90,
        "active_font_size": 95,
        "bold": True,
        "outline": 3.0,
        "shadow": 1.0,
        "alignment": 5,
        "margin_h": 40,
        "margin_v": 40,
        "lines_before": 2,
        "lines_after": 2,
        "lines_per_slide": 0,
        "stanza_mode": "time",
        "transition": "scroll",
        "transition_ms": 400,
        "entry_lead_ms": 900,
    },
    "background": {
        "type": "image",
        "color": "#0B1D3A",
        "image_filename": None,
        "fit": "contain",
    },
    "output": {"width": 1920, "height": 1080},
    "burn": {"preset": "medium", "crf": 18},
    "demo": False,
}

_BURN_PRESETS = {
    "ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow",
}

# Campos que ``collect()`` preenche com valores fixos (e que apagariam o
# perfil) nunca sao enviados ao ``save_profile_settings``.
_PROTECTED_FROM_COLLECT = {
    "karaoke": ("normal_color", "active_color", "outline_color", "back_color"),
    "background": ("image_filename",),
}


def _merge(base: dict, override: Mapping[str, Any] | None) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _require_int(
    settings: dict[str, Any], group: str, key: str, low: int, high: int, label: str
) -> int:
    try:
        value = int(settings[group][key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(t("err_must_be_int", label=label)) from exc
    if not low <= value <= high:
        raise ValueError(t("err_between", label=label, low=low, high=high))
    return value


def _require_float(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(t("err_must_be_number", label=label)) from exc


def _validated_color(value: Any, label: str, *, allow_alpha: bool = True) -> str:
    text = str(value or "").strip()
    try:
        ass_color(text)
    except ValueError as exc:
        raise ValueError(t("err_color_invalid", label=label)) from exc
    if not allow_alpha and len(text.removeprefix("#")) != 6:
        raise ValueError(t("err_color_no_alpha", label=label))
    return text


def normalize_settings(raw: Any) -> dict[str, Any]:
    """Validate/normalize the collected payload without touching the webapp."""
    settings = _merge(DEFAULT_SETTINGS, raw)
    align = settings["alignment"]
    if align["mode"] not in ("auto", "manual"):
        raise ValueError(t("err_invalid_mode"))
    align["language"] = str(align["language"] or "auto").strip()[:30] or "auto"
    align["model"] = str(align["model"] or "small").strip()[:30] or "small"
    align["separate"] = bool(align["separate"])
    _require_int(settings, "alignment", "max_chars", 10, 100, t("max_chars"))
    _require_int(settings, "alignment", "lead_ms", 0, 2000, t("lead_ms"))
    _require_int(settings, "alignment", "end_pad_ms", 0, 5000, t("end_pad"))
    _require_int(settings, "alignment", "min_duration_ms", 100, 5000, t("min_duration"))
    _require_int(settings, "alignment", "max_duration_ms", 500, 60000, t("max_duration_ms_label"))
    _require_int(settings, "alignment", "min_gap_ms", 0, 1000, t("min_gap"))
    align["offset_seconds"] = _require_float(align["offset_seconds"], t("offset_seconds"))
    align["playback_offset_seconds"] = max(
        -60.0,
        min(
            60.0,
            _require_float(
                align.get("playback_offset_seconds", 0.0), t("playback_offset")
            ),
        ),
    )
    align["range_start"] = max(0.0, _require_float(align["range_start"], t("label_range_start")))
    align["range_end"] = (
        None
        if align["range_end"] in (None, "", "null")
        else _require_float(align["range_end"], t("label_range_end"))
    )
    if align["range_end"] is not None and align["range_end"] <= align["range_start"]:
        raise ValueError(t("err_range_end_order"))

    karaoke = settings["karaoke"]
    karaoke["enabled"] = bool(karaoke["enabled"])
    karaoke["normal_color"] = _validated_color(karaoke["normal_color"], t("label_color_normal"))
    karaoke["active_color"] = _validated_color(karaoke["active_color"], t("label_color_active"))
    karaoke["outline_color"] = _validated_color(
        karaoke.get("outline_color"), t("label_color_outline")
    )
    karaoke["back_color"] = _validated_color(karaoke.get("back_color"), t("label_color_shadow"))
    karaoke["font_name"] = str(karaoke["font_name"] or "Arial").strip()[:200] or "Arial"
    _require_int(settings, "karaoke", "font_size", 8, 300, t("label_font_size_plain"))
    karaoke["bold"] = bool(karaoke["bold"])
    karaoke["outline"] = _require_float(karaoke["outline"], t("outline"))
    karaoke["shadow"] = _require_float(karaoke["shadow"], t("fx_shadow"))
    _require_int(settings, "karaoke", "alignment", 1, 9, t("alignment_label"))
    _require_int(settings, "karaoke", "margin_h", 0, 1000, t("margin_h"))
    _require_int(settings, "karaoke", "margin_v", 0, 1000, t("margin_v"))
    _require_int(settings, "karaoke", "lines_before", 0, 5, t("lines_before"))
    _require_int(settings, "karaoke", "lines_after", 0, 5, t("lines_after"))
    _require_int(settings, "karaoke", "lines_per_slide", 0, 5, t("lines_per_slide"))
    karaoke["stanza_mode"] = str(karaoke.get("stanza_mode") or "time").strip().lower()
    if karaoke["stanza_mode"] not in ("time", "stanzas"):
        raise ValueError(t("err_stanza_mode"))
    karaoke["transition"] = str(karaoke.get("transition") or "none").strip().lower()
    if karaoke["transition"] not in ("none", "scroll", "fade", "up", "down"):
        raise ValueError(t("err_transition"))
    _require_int(settings, "karaoke", "transition_ms", 100, 2000, t("transition_ms"))

    background = settings["background"]
    if background["type"] not in ("color", "image"):
        raise ValueError(t("err_bg_type"))
    background["color"] = _validated_color(
        background["color"], t("fx_bg_color"), allow_alpha=False
    )
    background["fit"] = str(background.get("fit") or "contain").strip().lower()
    if background["fit"] not in ("contain", "cover"):
        raise ValueError(t("err_bg_fit"))
    background["image_filename"] = (
        Path(str(background["image_filename"])).name
        if background.get("image_filename")
        else None
    )

    output = settings["output"]
    _require_int(settings, "output", "width", 320, 7680, t("label_output_width"))
    _require_int(settings, "output", "height", 240, 4320, t("label_output_height"))
    if output["width"] % 2 or output["height"] % 2:
        raise ValueError(
            t("err_even_dimensions")
        )

    burn = settings["burn"]
    if burn["preset"] not in _BURN_PRESETS:
        raise ValueError(t("err_burn_preset"))
    _require_int(settings, "burn", "crf", 0, 51, t("crf_label"))

    settings["demo"] = bool(settings["demo"])
    return settings


class SettingsTab:
    def __init__(
        self,
        parent: ttk.Frame,
        app,
        profiles_path: str | Path | None = None,
        settings_path: str | Path | None = None,
    ) -> None:
        self.parent = parent
        self.app = app
        self.vars: dict[str, tk.Variable] = {}
        self.profiles_path = profiles_path
        self.settings_path = settings_path
        self._status_key: str | None = None
        self._status_kwargs: dict[str, Any] = {}
        self._status_text = ""
        self._build()
        _SETTINGS_TABS.add(self)
        active = profiles_api.get_active_profile(self.profiles_path)
        initial = active if active in profiles_api.list_profiles(self.profiles_path) else None
        if initial:
            self._load_profile(initial)
        else:
            self._apply_to_vars(DEFAULT_SETTINGS)
            self._refresh_profile_info()

    # ---- helpers ----
    # Keys (not rendered strings): the map is evaluated at import time, so a
    # frozen translation would never follow a later language change.
    TOOLTIP_MAP = {
        "language": "tip_language",
        "model": "tip_model",
        "max_chars": "tip_max_chars",
        "lead_ms": "tip_lead",
        "end_pad_ms": "tip_end_pad",
        "min_duration_ms": "tip_min_duration",
        "max_duration_ms": "tip_max_duration",
        "min_gap_ms": "tip_min_gap",
        "offset_seconds": "tip_offset",
        "playback_offset_seconds": "tip_pb_offset",
        "font_name": "tip_font",
        "font_size": "tip_font_size",
        "center_font_size": "tip_center_fs",
        "context_font_size": "tip_context_fs",
        "outline": "tip_outline",
        "shadow": "tip_shadow",
        "alignment": "tip_alignment",
        "margin_h": "tip_margin_h",
        "margin_v": "tip_margin_v",
        "lines_before": "tip_lines_before",
        "lines_after": "tip_lines_after",
        "lines_per_slide": "tip_lines_per_slide",
        "transition_ms": "tip_transition_ms",
        "entry_lead_ms": "tip_entry_lead",
        "bg_color": "tip_color",
        "out_width": "tip_width",
        "out_height": "tip_height",
        "burn_crf": "tip_crf",
        "target_duration_seconds": "tip_target_duration",
    }

    def _add_field(self, frame, row, label_key, key, var, width=12):
        label = ttk.Label(frame, text=t(label_key))
        mark(label, label_key)
        label.grid(row=row, column=0, sticky="w", padx=4, pady=2)
        entry = ttk.Entry(frame, textvariable=var, width=width)
        entry.grid(row=row, column=1, sticky="w", padx=4, pady=2)
        self.vars[key] = var
        tip_key = self.TOOLTIP_MAP.get(key)
        if tip_key:
            add_tooltip(entry, t(tip_key))
            mark_tooltip(entry, tip_key)
        return entry

    def _build(self) -> None:
        canvas = tk.Canvas(self.parent)
        scrollbar = ttk.Scrollbar(self.parent, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # --- Profiles (data layer) ---
        profile_frame = ttk.LabelFrame(self.inner, text=t("profile_group"))
        mark(profile_frame, "profile_group")
        profile_frame.pack(fill="x", padx=8, pady=6)
        lbl_active = ttk.Label(profile_frame, text=t("active_profile_label"))
        mark(lbl_active, "active_profile_label")
        lbl_active.grid(row=0, column=0, sticky="w", padx=4, pady=2)
        active = profiles_api.get_active_profile(self.profiles_path) or ""
        names = profiles_api.list_profiles(self.profiles_path)
        self.profile_var = tk.StringVar(value=active)
        self.profile_combo = ttk.Combobox(
            profile_frame, textvariable=self.profile_var, values=names,
            state="readonly", width=28,
        )
        self.profile_combo.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        add_tooltip(self.profile_combo, t("tip_profile_select"))
        mark_tooltip(self.profile_combo, "tip_profile_select")

        btn_row = ttk.Frame(profile_frame)
        btn_row.grid(row=1, column=0, columnspan=2, sticky="w", padx=4, pady=6)
        profile_buttons = (
            ("profile_new", self._new_profile, "tip_new_profile"),
            ("profile_duplicate", self._duplicate_profile, "tip_duplicate_profile"),
            ("profile_save_settings", self._save_profile_settings, "tip_save_profile"),
            ("profile_delete", self._delete_profile, "tip_delete_profile"),
            ("profile_save_app", self._save_app_settings, "tip_save_app"),
            ("profile_restore", self._restore_defaults, "tip_restore"),
        )
        for key, command, tip_key in profile_buttons:
            button = ttk.Button(btn_row, text=t(key), command=command)
            mark(button, key)
            button.pack(side="left", padx=2)
            add_tooltip(button, t(tip_key))
            mark_tooltip(button, tip_key)

        # --- What this profile changes ---
        info_frame = ttk.LabelFrame(self.inner, text=t("profile_changes"))
        mark(info_frame, "profile_changes")
        info_frame.pack(fill="x", padx=8, pady=6)
        self.profile_info = tk.Text(info_frame, height=7, wrap=tk.WORD, state=tk.DISABLED)
        self.profile_info.pack(fill="x", padx=4, pady=4)

        settings = DEFAULT_SETTINGS
        align = settings["alignment"]
        karaoke = settings["karaoke"]
        background = settings["background"]
        output = settings["output"]
        burn = settings["burn"]

        # --- Alignment ---
        sec = ttk.LabelFrame(self.inner, text=t("settings_title"))
        mark(sec, "settings_title")
        sec.pack(fill="x", padx=8, pady=6)
        r = 0
        self.v_mode = tk.StringVar(value=align["mode"])
        lbl_mode = ttk.Label(sec, text=t("mode_short"))
        mark(lbl_mode, "mode_short")
        lbl_mode.grid(row=r, column=0, sticky="w", padx=4, pady=2)
        cb_mode = ttk.Combobox(sec, textvariable=self.v_mode, values=["auto", "manual"], width=10)
        cb_mode.grid(row=r, column=1, sticky="w", padx=4, pady=2)
        add_tooltip(cb_mode, t("tip_mode"))
        mark_tooltip(cb_mode, "tip_mode")
        r += 1
        self.v_language = tk.StringVar(value=align["language"])
        self._add_field(sec, r, "language", "language", self.v_language, 10); r += 1
        self.v_model = tk.StringVar(value=align["model"])
        self._add_field(sec, r, "model_short", "model", self.v_model, 10); r += 1
        self.v_separate = tk.BooleanVar(value=align["separate"])
        cb_sep = ttk.Checkbutton(sec, text=t("separate_vocals"), variable=self.v_separate)
        mark(cb_sep, "separate_vocals")
        cb_sep.grid(row=r, column=0, columnspan=2, sticky="w", padx=4, pady=2); r += 1
        self.v_max_chars = tk.IntVar(value=align["max_chars"])
        self._add_field(sec, r, "max_chars_short", "max_chars", self.v_max_chars); r += 1
        self.v_lead_ms = tk.IntVar(value=align["lead_ms"])
        self._add_field(sec, r, "lead_ms", "lead_ms", self.v_lead_ms); r += 1
        self.v_end_pad = tk.IntVar(value=align["end_pad_ms"])
        self._add_field(sec, r, "end_pad", "end_pad_ms", self.v_end_pad); r += 1
        self.v_min_dur = tk.IntVar(value=align["min_duration_ms"])
        self._add_field(sec, r, "min_duration", "min_duration_ms", self.v_min_dur); r += 1
        self.v_max_dur = tk.IntVar(value=align["max_duration_ms"])
        self._add_field(sec, r, "max_duration_ms_label", "max_duration_ms", self.v_max_dur); r += 1
        self.v_min_gap = tk.IntVar(value=align["min_gap_ms"])
        self._add_field(sec, r, "min_gap", "min_gap_ms", self.v_min_gap); r += 1
        self.v_offset = tk.DoubleVar(value=align["offset_seconds"])
        self._add_field(sec, r, "offset_seconds", "offset_seconds", self.v_offset); r += 1
        self.v_pb_offset = tk.DoubleVar(value=align["playback_offset_seconds"])
        self._add_field(sec, r, "playback_offset", "playback_offset_seconds", self.v_pb_offset); r += 1

        # --- Karaoke ---
        sec = ttk.LabelFrame(self.inner, text=t("tab_karaoke"))
        mark(sec, "tab_karaoke")
        sec.pack(fill="x", padx=8, pady=6)
        r = 0
        self.v_k_enabled = tk.BooleanVar(value=karaoke["enabled"])
        cb_enabled = ttk.Checkbutton(sec, text=t("karaoke_enabled"), variable=self.v_k_enabled)
        mark(cb_enabled, "karaoke_enabled")
        cb_enabled.grid(row=r, column=0, columnspan=2, sticky="w", padx=4, pady=2); r += 1
        self.v_font_name = tk.StringVar(value=karaoke["font_name"])
        self._add_field(sec, r, "field_font", "font_name", self.v_font_name, 40); r += 1
        self.v_font_size = tk.IntVar(value=karaoke["font_size"])
        self._add_field(sec, r, "font_size_short", "font_size", self.v_font_size); r += 1
        self.v_center_fs = tk.StringVar(value="" if karaoke["center_font_size"] is None else karaoke["center_font_size"])
        self._add_field(sec, r, "center_font", "center_font_size", self.v_center_fs); r += 1
        self.v_context_fs = tk.StringVar(value="" if karaoke["context_font_size"] is None else karaoke["context_font_size"])
        self._add_field(sec, r, "context_font", "context_font_size", self.v_context_fs); r += 1
        self.v_bold = tk.BooleanVar(value=karaoke["bold"])
        cb_bold = ttk.Checkbutton(sec, text=t("bold"), variable=self.v_bold)
        mark(cb_bold, "bold")
        cb_bold.grid(row=r, column=0, columnspan=2, sticky="w", padx=4, pady=2); r += 1
        self.v_outline = tk.DoubleVar(value=karaoke["outline"])
        self._add_field(sec, r, "outline", "outline", self.v_outline); r += 1
        self.v_shadow = tk.DoubleVar(value=karaoke["shadow"])
        self._add_field(sec, r, "fx_shadow", "shadow", self.v_shadow); r += 1
        self.v_alignment = tk.IntVar(value=karaoke["alignment"])
        self._add_field(sec, r, "alignment_label", "alignment", self.v_alignment); r += 1
        self.v_margin_h = tk.IntVar(value=karaoke["margin_h"])
        self._add_field(sec, r, "margin_h", "margin_h", self.v_margin_h); r += 1
        self.v_margin_v = tk.IntVar(value=karaoke["margin_v"])
        self._add_field(sec, r, "margin_v", "margin_v", self.v_margin_v); r += 1
        self.v_lines_before = tk.IntVar(value=karaoke["lines_before"])
        self._add_field(sec, r, "lines_before", "lines_before", self.v_lines_before); r += 1
        self.v_lines_after = tk.IntVar(value=karaoke["lines_after"])
        self._add_field(sec, r, "lines_after", "lines_after", self.v_lines_after); r += 1
        self.v_lines_per_slide = tk.IntVar(value=karaoke["lines_per_slide"])
        self._add_field(sec, r, "lines_per_slide", "lines_per_slide", self.v_lines_per_slide); r += 1
        self.v_transition = tk.StringVar(value=karaoke["transition"])
        lbl_transition = ttk.Label(sec, text=t("transition_label"))
        mark(lbl_transition, "transition_label")
        lbl_transition.grid(row=r, column=0, sticky="w", padx=4, pady=2)
        cb_tr = ttk.Combobox(sec, textvariable=self.v_transition, values=["none", "scroll", "fade", "up", "down"], width=10)
        cb_tr.grid(row=r, column=1, sticky="w", padx=4, pady=2)
        add_tooltip(cb_tr, t("tip_transition"))
        mark_tooltip(cb_tr, "tip_transition")
        r += 1
        self.v_transition_ms = tk.IntVar(value=karaoke["transition_ms"])
        self._add_field(sec, r, "transition_ms", "transition_ms", self.v_transition_ms); r += 1
        self.v_entry_lead_ms = tk.IntVar(value=karaoke.get("entry_lead_ms", 200))
        self._add_field(sec, r, "entry_lead", "entry_lead_ms", self.v_entry_lead_ms); r += 1

        # --- Background ---
        sec = ttk.LabelFrame(self.inner, text=t("background_label"))
        mark(sec, "background_label")
        sec.pack(fill="x", padx=8, pady=6)
        r = 0
        self.v_bg_type = tk.StringVar(value=background["type"])
        lbl_bg_type = ttk.Label(sec, text=t("bg_type_label"))
        mark(lbl_bg_type, "bg_type_label")
        lbl_bg_type.grid(row=r, column=0, sticky="w", padx=4, pady=2)
        cb_bg = ttk.Combobox(sec, textvariable=self.v_bg_type, values=["color", "image"], width=10)
        cb_bg.grid(row=r, column=1, sticky="w", padx=4, pady=2)
        add_tooltip(cb_bg, t("tip_bg_type"))
        mark_tooltip(cb_bg, "tip_bg_type")
        r += 1
        self.v_bg_color = tk.StringVar(value=background["color"])
        self._add_field(sec, r, "field_color", "bg_color", self.v_bg_color, 12); r += 1
        self.v_bg_fit = tk.StringVar(value=background["fit"])
        lbl_fit = ttk.Label(sec, text=t("fit_label"))
        mark(lbl_fit, "fit_label")
        lbl_fit.grid(row=r, column=0, sticky="w", padx=4, pady=2)
        cb_fit = ttk.Combobox(sec, textvariable=self.v_bg_fit, values=["contain", "cover"], width=10)
        cb_fit.grid(row=r, column=1, sticky="w", padx=4, pady=2)
        add_tooltip(cb_fit, t("tip_fit"))
        mark_tooltip(cb_fit, "tip_fit")
        r += 1

        # --- Output ---
        sec = ttk.LabelFrame(self.inner, text=t("output_group"))
        mark(sec, "output_group")
        sec.pack(fill="x", padx=8, pady=6)
        r = 0
        self.v_out_w = tk.IntVar(value=output["width"])
        self._add_field(sec, r, "field_width", "out_width", self.v_out_w); r += 1
        self.v_out_h = tk.IntVar(value=output["height"])
        self._add_field(sec, r, "field_height", "out_height", self.v_out_h); r += 1

        # --- Burn ---
        sec = ttk.LabelFrame(self.inner, text=t("burn_group"))
        mark(sec, "burn_group")
        sec.pack(fill="x", padx=8, pady=6)
        r = 0
        self.v_burn_preset = tk.StringVar(value=burn["preset"])
        lbl_preset = ttk.Label(sec, text=t("preset"))
        mark(lbl_preset, "preset")
        lbl_preset.grid(row=r, column=0, sticky="w", padx=4, pady=2)
        ttk.Combobox(sec, textvariable=self.v_burn_preset, values=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"], width=12).grid(row=r, column=1, sticky="w", padx=4, pady=2); r += 1
        self.v_burn_crf = tk.IntVar(value=burn["crf"])
        self._add_field(sec, r, "crf_label", "burn_crf", self.v_burn_crf); r += 1
        td = burn.get("target_duration_seconds")
        self.v_target_dur = tk.StringVar(value="" if td is None else str(td))
        self._add_field(sec, r, "target_duration", "target_duration_seconds", self.v_target_dur); r += 1

        # --- Buttons ---
        btn_frame = ttk.Frame(self.inner)
        btn_frame.pack(fill="x", padx=8, pady=8)
        b1 = ttk.Button(btn_frame, text=t("apply_santo"), command=self.apply_santo)
        mark(b1, "apply_santo")
        b1.pack(side="left", padx=4)
        add_tooltip(b1, t("tip_santo"))
        mark_tooltip(b1, "tip_santo")
        b2 = ttk.Button(btn_frame, text=t("validate_config"), command=self.validate)
        mark(b2, "validate_config")
        b2.pack(side="left", padx=4)
        add_tooltip(b2, t("tip_validate_config"))
        mark_tooltip(b2, "tip_validate_config")

        self.status_label = ttk.Label(self.inner, text="", foreground="blue")
        self.status_label.pack(padx=8, pady=4, anchor="w")

    def _set_status(self, key: str, color: str, **kwargs: Any) -> None:
        """Remember a translated status message so a language switch refreshes it."""
        text = t(key, **kwargs)
        self.status_label.config(text=text, foreground=color)
        self._status_key = key
        self._status_kwargs = kwargs
        self._status_text = text

    def _retranslate_dynamic(self) -> None:
        """Hook: refresh status/profile text that static marks cannot cover."""
        if self._status_key and self.status_label.winfo_exists():
            current = str(self.status_label.cget("text"))
            if current == self._status_text or current == t(self._status_key, **self._status_kwargs):
                text = t(self._status_key, **self._status_kwargs)
                self.status_label.config(text=text)
                self._status_text = text
        if self.profile_info.winfo_exists():
            self._refresh_profile_info()

    # ---- actions ----
    def collect(self) -> dict:
        def _int_or_none(v):
            v = v.strip() if isinstance(v, str) else v
            if v in ("", None):
                return None
            return int(float(v))

        return {
            "alignment": {
                "mode": self.v_mode.get(),
                "language": self.v_language.get(),
                "model": self.v_model.get(),
                "separate": bool(self.v_separate.get()),
                "max_chars": int(self.v_max_chars.get()),
                "lead_ms": int(self.v_lead_ms.get()),
                "end_pad_ms": int(self.v_end_pad.get()),
                "min_duration_ms": int(self.v_min_dur.get()),
                "max_duration_ms": int(self.v_max_dur.get()),
                "min_gap_ms": int(self.v_min_gap.get()),
                "offset_seconds": float(self.v_offset.get()),
                "playback_offset_seconds": float(self.v_pb_offset.get()),
            },
            "karaoke": {
                "enabled": bool(self.v_k_enabled.get()),
                "normal_color": "#FFFFFF",
                "active_color": "#FFFFFF",
                "outline_color": "#000000",
                "back_color": "#00000080",
                "font_name": self.v_font_name.get(),
                "font_size": int(self.v_font_size.get()),
                "center_font_size": _int_or_none(self.v_center_fs.get()),
                "context_font_size": _int_or_none(self.v_context_fs.get()),
                "bold": bool(self.v_bold.get()),
                "outline": float(self.v_outline.get()),
                "shadow": float(self.v_shadow.get()),
                "alignment": int(self.v_alignment.get()),
                "margin_h": int(self.v_margin_h.get()),
                "margin_v": int(self.v_margin_v.get()),
                "lines_before": int(self.v_lines_before.get()),
                "lines_after": int(self.v_lines_after.get()),
                "lines_per_slide": int(self.v_lines_per_slide.get()),
                "transition": self.v_transition.get(),
                "transition_ms": int(self.v_transition_ms.get()),
                "entry_lead_ms": int(self.v_entry_lead_ms.get()),
            },
            "background": {
                "type": self.v_bg_type.get(),
                "color": self.v_bg_color.get(),
                "image_filename": None,
                "fit": self.v_bg_fit.get(),
            },
            "output": {"width": int(self.v_out_w.get()), "height": int(self.v_out_h.get())},
            "burn": {
                "preset": self.v_burn_preset.get(),
                "crf": int(self.v_burn_crf.get()),
                "target_duration_seconds": (None if not self.v_target_dur.get().strip() else float(self.v_target_dur.get())),
            },
            "demo": False,
        }

    def _profile_settings_payload(self) -> dict:
        """Edited fields only: never persist collect()'s hardcoded placeholders."""
        payload = copy.deepcopy(self.collect())
        for group, keys in _PROTECTED_FROM_COLLECT.items():
            for key in keys:
                payload[group].pop(key, None)
        payload.pop("demo", None)
        return payload

    def _apply_to_vars(self, settings: Mapping[str, Any]) -> None:
        settings = _merge(DEFAULT_SETTINGS, settings)
        align = settings["alignment"]
        karaoke = settings["karaoke"]
        background = settings["background"]
        output = settings["output"]
        burn = settings["burn"]
        self.v_mode.set(align.get("mode", "auto"))
        self.v_language.set(align.get("language", "auto"))
        self.v_model.set(align.get("model", "small"))
        self.v_separate.set(bool(align.get("separate", True)))
        self.v_max_chars.set(int(align.get("max_chars", 30)))
        self.v_lead_ms.set(int(align.get("lead_ms", 600)))
        self.v_end_pad.set(int(align.get("end_pad_ms", 500)))
        self.v_min_dur.set(int(align.get("min_duration_ms", 500)))
        self.v_max_dur.set(int(align.get("max_duration_ms", 10000)))
        self.v_min_gap.set(int(align.get("min_gap_ms", 80)))
        self.v_offset.set(float(align.get("offset_seconds", 0.0)))
        self.v_pb_offset.set(float(align.get("playback_offset_seconds", 0.0)))
        self.v_k_enabled.set(bool(karaoke.get("enabled", True)))
        self.v_font_name.set(karaoke.get("font_name", "Arial"))
        self.v_font_size.set(int(karaoke.get("font_size", 84)))
        center = karaoke.get("center_font_size")
        context = karaoke.get("context_font_size")
        self.v_center_fs.set("" if center is None else str(center))
        self.v_context_fs.set("" if context is None else str(context))
        self.v_bold.set(bool(karaoke.get("bold", True)))
        self.v_outline.set(float(karaoke.get("outline", 0.0)))
        self.v_shadow.set(float(karaoke.get("shadow", 0.0)))
        self.v_alignment.set(int(karaoke.get("alignment", 2)))
        self.v_margin_h.set(int(karaoke.get("margin_h", 80)))
        self.v_margin_v.set(int(karaoke.get("margin_v", 280)))
        self.v_lines_before.set(int(karaoke.get("lines_before", 0)))
        self.v_lines_after.set(int(karaoke.get("lines_after", 3)))
        self.v_lines_per_slide.set(int(karaoke.get("lines_per_slide", 0)))
        self.v_transition.set(karaoke.get("transition", "scroll"))
        self.v_transition_ms.set(int(karaoke.get("transition_ms", 600)))
        self.v_entry_lead_ms.set(int(karaoke.get("entry_lead_ms", 200)))
        self.v_bg_type.set(background.get("type", "image"))
        self.v_bg_color.set(background.get("color", "#0B1D3A"))
        self.v_bg_fit.set(background.get("fit", "contain"))
        self.v_out_w.set(int(output.get("width", 1920)))
        self.v_out_h.set(int(output.get("height", 1080)))
        self.v_burn_preset.set(burn.get("preset", "medium"))
        self.v_burn_crf.set(int(burn.get("crf", 18)))
        target = burn.get("target_duration_seconds")
        self.v_target_dur.set("" if target is None else str(target))

    def _current_profile(self) -> str:
        return str(self.profile_var.get()).strip()

    def _on_profile_selected(self, _event=None) -> None:
        self._load_profile(self._current_profile())

    def _load_profile(self, name: str) -> None:
        if not name:
            self._apply_to_vars(DEFAULT_SETTINGS)
            self._refresh_profile_info()
            return
        try:
            settings = profiles_api.profile_settings(name, path=self.profiles_path)
        except ValueError as exc:
            self._set_status("config_error", "red", exc=exc)
            return
        self._apply_to_vars(settings)
        self._refresh_profile_info()
        self._set_status("profile_loaded", "blue", name=name)

    def _refresh_profile_info(self) -> None:
        name = self._current_profile()
        lines: list[str] = []
        try:
            profile = profiles_api.get_profile(name, path=self.profiles_path)
        except ValueError:
            profile = None
        if profile is None:
            lines.append(t("profile_none"))
        else:
            lines.append(t("profile_name_info", name=profile.get("name", name)))
            description = str(profile.get("description") or "").strip()
            if description:
                lines.append(t("profile_description_info", text=description))
            if profile.get("projects_root"):
                lines.append(t("profile_projects_root_info", path=profile["projects_root"]))
            lines.append("")
            lines.append(t("profile_diffs"))
            merged = profiles_api.profile_settings(profile)
            for group in sorted(merged):
                values = merged[group]
                if not isinstance(values, dict):
                    continue
                defaults = DEFAULT_SETTINGS.get(group) or {}
                for key in sorted(values):
                    value = values[key]
                    if value != defaults.get(key):
                        lines.append(f"  {group}.{key}: {defaults.get(key)!r} -> {value!r}")
            extras = profile.get("extras")
            if isinstance(extras, dict) and extras:
                lines.append("")
                lines.append(t("profile_extras"))
                for key in sorted(extras):
                    lines.append(f"  extras.{key}: {extras[key]!r}")
        self.profile_info.config(state=tk.NORMAL)
        self.profile_info.delete("1.0", tk.END)
        self.profile_info.insert("1.0", "\n".join(lines))
        self.profile_info.config(state=tk.DISABLED)

    def _refresh_profile_list(self, select: str | None = None) -> None:
        names = profiles_api.list_profiles(self.profiles_path)
        self.profile_combo.config(values=names)
        target = select if select in names else (profiles_api.get_active_profile(self.profiles_path) or "")
        self.profile_var.set(target)
        if target:
            self._load_profile(target)
        else:
            self._apply_to_vars(DEFAULT_SETTINGS)
            self._refresh_profile_info()

    def _new_profile(self) -> None:
        name = simpledialog.askstring(t("new_profile_title"), t("new_profile_prompt"))
        if not name or not name.strip():
            return
        try:
            profiles_api.create_profile(name.strip(), path=self.profiles_path)
        except ValueError as exc:
            messagebox.showerror(t("new_profile_title"), str(exc))
            return
        self._refresh_profile_list(name.strip())
        self._set_status("profile_created", "green", name=name.strip())

    def _duplicate_profile(self) -> None:
        source = self._current_profile()
        if not source:
            messagebox.showwarning(t("profile_duplicate"), t("select_duplicate_warn"))
            return
        name = simpledialog.askstring(t("duplicate_profile_title"), t("duplicate_profile_prompt"), initialvalue=f"{source}-copia")
        if not name or not name.strip():
            return
        try:
            profiles_api.duplicate_profile(source, name.strip(), path=self.profiles_path)
        except ValueError as exc:
            messagebox.showerror(t("duplicate_profile_title"), str(exc))
            return
        self._refresh_profile_list(name.strip())
        self._set_status("profile_duplicated", "green", name=name.strip())

    def _save_profile_settings(self) -> None:
        name = self._current_profile()
        if not name:
            messagebox.showwarning(t("save_adjust_title"), t("select_profile_warn"))
            return
        try:
            normalize_settings(self._profile_settings_payload())
            profiles_api.save_profile_settings(name, self._profile_settings_payload(), path=self.profiles_path)
        except (ValueError, OSError) as exc:
            messagebox.showerror(t("save_adjust_title"), str(exc))
            self._set_status("config_error", "red", exc=exc)
            return
        self._refresh_profile_info()
        self._set_status("profile_saved", "green", name=name)

    def _delete_profile(self) -> None:
        name = self._current_profile()
        if not name:
            messagebox.showwarning(t("profile_delete"), t("select_profile_warn"))
            return
        active = profiles_api.get_active_profile(self.profiles_path) == name
        force = False
        if active:
            confirmed = messagebox.askyesno(
                t("delete_active_title"),
                t("delete_active_question", name=name),
            )
            if not confirmed:
                self._set_status("profile_delete_cancel", "blue")
                return
            force = True
        try:
            profiles_api.delete_profile(name, self.profiles_path, force=force)
        except ValueError as exc:
            messagebox.showerror(t("profile_delete"), str(exc))
            self._set_status("config_error", "red", exc=exc)
            return
        self._refresh_profile_list()
        self._set_status("profile_deleted", "green", name=name)

    def _activate_profile(self, name: str) -> None:
        """Ativa o perfil pela fachada unica (active + profile juntos).

        A aba nunca escreve ``profile`` diretamente: quando ha caminhos
        isolados eles sao repassados a fachada; caso contrario a casca pode
        oferecer ``set_active_profile`` e, na falta dela, a fachada padrao
        cuida de ambos os arquivos.
        """
        profiles_path = (
            self.profiles_path
            if self.profiles_path is not None
            else getattr(self.app, "profiles_path", None)
        )
        settings_path = (
            self.settings_path
            if self.settings_path is not None
            else getattr(self.app, "settings_path", None)
        )
        if profiles_path is not None or settings_path is not None:
            profiles_api.set_active_profile(
                name,
                profiles_path=profiles_path,
                settings_path=settings_path,
            )
            return
        setter = getattr(self.app, "set_active_profile", None)
        if callable(setter):
            setter(name)
            return
        profiles_api.set_active_profile(name)

    def _save_app_settings(self) -> None:
        name = self._current_profile()
        # ``profile`` nao pertence a este escritor: a fachada acima grava
        # profiles.json.active e settings.json.profile em uma unica chamada.
        payload: dict[str, Any] = {"karaoke_settings": self._profile_settings_payload()}
        try:
            if name:
                self._activate_profile(name)
            update_settings(payload, self.settings_path)
        except (ValueError, OSError) as exc:
            self._set_status("config_error", "red", exc=exc)
            return
        self._set_status("settings_saved", "green")

    def _restore_defaults(self) -> None:
        self._apply_to_vars(DEFAULT_SETTINGS)
        try:
            update_settings(
                {"karaoke_settings": copy.deepcopy(DEFAULT_SETTINGS)},
                self.settings_path,
            )
        except (ValueError, OSError):
            pass
        self._set_status("defaults_restored", "green")

    def apply_santo(self) -> None:
        s = SANTO_PRESET
        a = s["alignment"]; k = s["karaoke"]; b = s["background"]; o = s["output"]; bu = s["burn"]
        self.v_mode.set(a["mode"]); self.v_language.set(a["language"]); self.v_model.set(a["model"])
        self.v_separate.set(a["separate"]); self.v_max_chars.set(a["max_chars"])
        self.v_lead_ms.set(a["lead_ms"]); self.v_end_pad.set(a["end_pad_ms"])
        self.v_min_dur.set(a["min_duration_ms"]); self.v_max_dur.set(a["max_duration_ms"])
        self.v_min_gap.set(a["min_gap_ms"]); self.v_offset.set(a["offset_seconds"])
        self.v_pb_offset.set(a["playback_offset_seconds"])
        self.v_k_enabled.set(k["enabled"]); self.v_font_name.set(k["font_name"])
        self.v_font_size.set(k["font_size"]); self.v_center_fs.set(k["center_font_size"] if k["center_font_size"] is not None else "")
        self.v_context_fs.set(k["context_font_size"] if k["context_font_size"] is not None else "")
        self.v_bold.set(k["bold"]); self.v_outline.set(k["outline"]); self.v_shadow.set(k["shadow"])
        self.v_alignment.set(k["alignment"]); self.v_margin_h.set(k["margin_h"]); self.v_margin_v.set(k["margin_v"])
        self.v_lines_before.set(k["lines_before"]); self.v_lines_after.set(k["lines_after"])
        self.v_lines_per_slide.set(k["lines_per_slide"]); self.v_transition.set(k["transition"])
        self.v_transition_ms.set(k["transition_ms"]); self.v_entry_lead_ms.set(k["entry_lead_ms"])
        self.v_bg_type.set(b["type"]); self.v_bg_color.set(b["color"]); self.v_bg_fit.set(b["fit"])
        self.v_out_w.set(o["width"]); self.v_out_h.set(o["height"])
        self.v_burn_preset.set(bu["preset"]); self.v_burn_crf.set(bu["crf"])
        self._set_status("santo_applied", "green")

    def validate(self) -> None:
        try:
            settings = self.collect()
            normalize_settings(settings)
            self._set_status("config_valid", "green")
        except Exception as exc:
            self._set_status("config_error", "red", exc=exc)


# Hooks are module level so they are registered exactly once per import.
# Instances are held weakly; destroyed tabs are skipped safely.
_SETTINGS_TABS: WeakSet["SettingsTab"] = WeakSet()


def _on_language_changed(_lang: str) -> None:
    for tab in list(_SETTINGS_TABS):
        try:
            tab._retranslate_dynamic()
        except Exception:
            pass


on_language_changed(_on_language_changed)
