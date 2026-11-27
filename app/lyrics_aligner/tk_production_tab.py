"""Bloco de producao extraido de ``desktop.py`` (F3-I5).

O modulo e dono dos widgets de midia/faixa/letra, perfil, geracao de SRT,
lote/QC/historico, preview e status/log, e dos metodos que os operam.  Toda
dependencia de outros blocos passa pela fachada ``app``: ``set_status``,
``emit``, ``append_qc``, ``project_root_for``, ``bind_project``,
``scene_snapshot``, ``selected_batch_item``, ``background_image``,
``first_batch_item``, ``set_active_profile``, ``production_busy`` e
``open_folder``.  Nenhum widget alheio e acessado diretamente.

Os rotulos sao criados com ``t()`` + ``mark()`` no ato da construcao e
reaplicados por ``refresh_language()``, registrada como hook global via
WeakSet (mesmo padrao dos demais modulos de aba).  O modulo nunca altera o
idioma global, nunca grava preferencias de idioma e nao faz escrita de
perfil: a ativacao passa sempre pelo dono unico na fachada.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from tkinter import (BOTH, END, LEFT, RIGHT, X, Y, Canvas, Listbox, StringVar,
                     Text, filedialog, messagebox)
from tkinter import ttk
from weakref import WeakSet

from PIL import Image, ImageDraw, ImageFont, ImageTk

from . import profiles_api
from .i18n import mark, on_language_changed, t
from .media import waveform_peaks
from .process_runner import JobCancelled, console_python, run_job
from .project import update_project_meta
from .services.align import generate_alignment

APP_NAME = "Vilm Lyrics Aligner"
VILM_APP_PATH = Path(__file__).resolve().parents[2] / "app"


def _format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes, remainder = divmod(seconds, 60.0)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remainder:05.2f}"
    return f"{minutes:02d}:{remainder:05.2f}"


def _result_value(result, key: str, default=None):
    """Le um campo do resultado do servico (dict ou objeto simples)."""
    if isinstance(result, dict):
        return result.get(key, default)
    return getattr(result, key, default)


class ProductionTab:
    """Bloco de producao da aba Karaoke (midia, SRT, lote e QC)."""

    def __init__(self, app, parent) -> None:
        self.app = app
        self.parent = parent
        # Estado do job principal: o modulo e o dono; a fachada so o expoe
        # por propriedades somente-leitura/escrita para compatibilidade.
        self.media_path: Path | None = None
        self.duration = 0.0
        self.peaks: list[float] = []
        self.range_start = 0.0
        self.range_end = 0.0
        self.drag_start_x: float | None = None
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.last_output: Path | None = None
        self.lead_ms = 600
        self.last_warnings: list[str] = []
        self._language_widgets: list[tuple[object, str]] = []
        self.build(parent)
        _PRODUCTION_TABS.add(self)

    # ---- fachada inversa (estado de perfil pertence a DesktopApp) --------
    @property
    def profiles(self) -> dict:
        return self.app.profiles

    @profiles.setter
    def profiles(self, value: dict) -> None:
        self.app.profiles = value

    @property
    def active_profile(self) -> str:
        return self.app.active_profile

    @active_profile.setter
    def active_profile(self, value: str) -> None:
        self.app.active_profile = value

    def set_status(self, text: str) -> None:
        """Status via fachada (o widget pertence a este modulo)."""
        self.app.set_status(text)

    # ---- construcao -----------------------------------------------------
    def _register_text(self, widget, key: str):
        """Marca um rotulo traduzivel e o guarda para refresh_language."""
        mark(widget, key)
        self._language_widgets.append((widget, key))
        return widget

    def _label(self, parent, key: str, **kwargs):
        return self._register_text(ttk.Label(parent, text=t(key), **kwargs), key)

    def _button(self, parent, key: str, command, **kwargs):
        return self._register_text(
            ttk.Button(parent, text=t(key), command=command, **kwargs), key)

    def build(self, parent) -> None:
        """Monta os widgets de producao dentro do ``parent`` da fachada."""
        profile_frame = ttk.Frame(parent)
        profile_frame.pack(fill=X, pady=(0, 8))
        self.profile_label = self._label(profile_frame, "profile", style="Section.TLabel")
        self.profile_label.pack(anchor="w")
        self.profile_info = ttk.Label(profile_frame, style="Hint.TLabel", wraplength=900)
        self.profile_info.pack(anchor="w", pady=(2, 4))
        profile_row = ttk.Frame(profile_frame)
        profile_row.pack(fill=X)
        names = profiles_api.list_profiles()
        self.profile_box = ttk.Combobox(profile_row, state="readonly", width=36, values=names)
        self.profile_box.pack(side=LEFT)
        if self.active_profile in names:
            self.profile_box.current(names.index(self.active_profile))
        elif names:
            self.active_profile = names[0]
            self.profile_box.current(0)
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_changed)

        self.media_label = self._label(parent, "media", style="Section.TLabel")
        self.media_label.pack(anchor="w")
        self.media_hint = self._label(parent, "drop", style="Hint.TLabel", wraplength=900)
        self.media_hint.pack(anchor="w", pady=(2, 6))
        media_row = ttk.Frame(parent)
        media_row.pack(fill=X)
        self.media_name = ttk.Label(media_row, relief="sunken", padding=8,
                                    text=t("no_media"))
        mark(self.media_name, "no_media")
        self.media_name.pack(side=LEFT, fill=X, expand=True)
        self.browse_button = self._button(media_row, "browse", self._browse_media)
        self.browse_button.pack(side=RIGHT, padx=(8, 0))

        self.range_label = self._label(parent, "range", style="Section.TLabel")
        self.range_label.pack(anchor="w", pady=(14, 0))
        range_modes = ttk.Frame(parent)
        range_modes.pack(fill=X, pady=(3, 4))
        self.range_mode = StringVar(value="full")
        self.full_radio = ttk.Radiobutton(range_modes, variable=self.range_mode,
                                          value="full", text=t("full"),
                                          command=self._range_mode_changed)
        self._register_text(self.full_radio, "full")
        self.full_radio.pack(side=LEFT)
        self.selection_radio = ttk.Radiobutton(
            range_modes, variable=self.range_mode, value="selection",
            text=t("selection"), command=self._range_mode_changed)
        self._register_text(self.selection_radio, "selection")
        self.selection_radio.pack(side=LEFT, padx=(20, 0))
        self.range_readout = ttk.Label(range_modes, text="00:00.00 — 00:00.00")
        self.range_readout.pack(side=RIGHT)
        self.waveform = Canvas(parent, height=88, background="#20242b",
                               highlightthickness=1, highlightbackground="#555b66")
        self.waveform.pack(fill=X)
        self.waveform.bind("<Button-1>", self._wave_press)
        self.waveform.bind("<B1-Motion>", self._wave_drag)
        self.waveform.bind("<ButtonRelease-1>", self._wave_release)
        self.waveform.bind("<Configure>", lambda _event: self._draw_waveform())
        self.range_hint = self._label(parent, "range_help", style="Hint.TLabel", wraplength=900)
        self.range_hint.pack(anchor="w", pady=(4, 0))

        self.lyrics_label = self._label(parent, "lyrics", style="Section.TLabel")
        self.lyrics_label.pack(anchor="w", pady=(14, 0))
        self.lyrics_hint = self._label(parent, "lyrics_hint", style="Hint.TLabel", wraplength=900)
        self.lyrics_hint.pack(anchor="w", pady=(2, 4))
        self.lyrics = Text(parent, height=7, wrap="word", undo=True, font=("Segoe UI", 10))
        self.lyrics.pack(fill=BOTH, expand=True)
        self.load_button = self._button(parent, "load", self._load_lyrics)
        self.load_button.pack(anchor="e", pady=(5, 0))

        options = ttk.Frame(parent)
        options.pack(fill=X, pady=(12, 0))
        self.mode_label = self._label(options, "mode", style="Section.TLabel")
        self.mode_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.mode_box = ttk.Combobox(options, state="readonly", width=25,
                                     values=[t("auto"), t("manual")])
        self.mode_box.grid(row=0, column=1, sticky="ew")
        self.mode_box.bind("<<ComboboxSelected>>",
                           lambda _event: self._update_manual_visibility())
        self.device_label = self._label(options, "device")
        self.device_label.grid(row=0, column=2, sticky="e", padx=(24, 8))
        devices = (
            ["CUDA · NVIDIA", "CPU"]
            if self.app.backend == "cuda"
            else ["Apple GPU · Metal", "CPU"]
            if self.app.backend == "mps"
            else ["CPU"]
        )
        self.device_box = ttk.Combobox(options, state="readonly", width=20, values=devices)
        self.device_box.grid(row=0, column=3, sticky="ew")
        self.device_box.current(0)
        options.columnconfigure(1, weight=1)
        options.columnconfigure(3, weight=1)

        self.manual_frame = ttk.Frame(parent)
        self.max_chars_label = self._label(self.manual_frame, "max_chars")
        self.max_chars_label.grid(row=0, column=1, padx=(0, 5))
        self.max_chars = StringVar(value="30")
        ttk.Spinbox(self.manual_frame, from_=1, to=80, width=6,
                    textvariable=self.max_chars).grid(row=0, column=2)
        self.max_duration_label = self._label(self.manual_frame, "max_duration")
        self.max_duration_label.grid(row=0, column=3, padx=(18, 5))
        self.max_duration = StringVar(value="10")
        ttk.Spinbox(self.manual_frame, from_=3, to=30, width=6,
                    textvariable=self.max_duration).grid(row=0, column=4)
        self.end_hold_label = self._label(self.manual_frame, "end_hold")
        self.end_hold_label.grid(row=0, column=5, padx=(18, 5))
        self.end_hold = ttk.Combobox(self.manual_frame, state="readonly", width=10,
                                     values=["0.3 s", "0.5 s", "1.0 s", "1.5 s"])
        self.end_hold.grid(row=0, column=6)
        self.end_hold.current(1)

        self.guide = self._label(parent, "guide", style="Hint.TLabel", wraplength=900)
        self.guide.pack(fill=X, pady=(10, 6))
        action_row = ttk.Frame(parent)
        action_row.pack(fill=X)
        self.status = ttk.Label(action_row, text=t("ready"))
        mark(self.status, "ready")
        self.status.pack(side=LEFT, fill=X, expand=True)
        self.open_folder_button = self._button(action_row, "open_folder",
                                               self._open_output_folder)
        self.cancel_button = self._button(action_row, "cancel", self._cancel)
        self.generate_button = self._button(action_row, "generate", self._generate,
                                            style="Primary.TButton")
        self.generate_button.pack(side=RIGHT)
        self.progress = ttk.Progressbar(parent, mode="indeterminate")
        self.progress.pack(fill=X, pady=(7, 0))
        self.log = Text(parent, height=4, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.pack(fill=X, pady=(6, 0))

        batch = ttk.LabelFrame(parent, text=t("batch_title"), padding=8)
        self._register_text(batch, "batch_title")
        batch.pack(fill=X, pady=(10, 0))
        top = ttk.Frame(batch)
        top.pack(fill=X)
        self._label(top, "intro_label").pack(side=LEFT)
        self.intro_seconds = StringVar(value="4")
        ttk.Spinbox(top, from_=0, to=10, width=5,
                    textvariable=self.intro_seconds).pack(side=LEFT, padx=(4, 14))
        self._label(top, "final_logo").pack(side=LEFT)
        self.logo_var = StringVar()
        ttk.Entry(top, textvariable=self.logo_var, width=36).pack(side=LEFT, padx=4)
        ttk.Button(top, text="…", width=3,
                   command=self._browse_logo).pack(side=LEFT)
        self._label(top, "background_label").pack(side=LEFT, padx=(14, 0))
        self.bg_var = StringVar()
        ttk.Entry(top, textvariable=self.bg_var, width=22).pack(side=LEFT, padx=4)
        ttk.Button(top, text="…", width=3,
                   command=self._browse_bg).pack(side=LEFT)

        name_row = ttk.Frame(batch)
        name_row.pack(fill=X, pady=(6, 0))
        self._label(name_row, "filename_label").pack(side=LEFT)
        self.name_var = StringVar(value="{pasta}")
        ttk.Entry(name_row, textvariable=self.name_var, width=44).pack(side=LEFT, padx=4)
        self._label(name_row, "folder_hint", style="Hint.TLabel").pack(side=LEFT)
        self.transition_var = StringVar(value="")
        ttk.Combobox(name_row, state="readonly", width=12,
                     textvariable=self.transition_var,
                     values=["", "fade", "dissolve", "wipeleft", "slideright",
                             "smoothup"]).pack(side=RIGHT)
        self._label(name_row, "transition_label").pack(side=RIGHT, padx=(8, 2))

        mid = ttk.Frame(batch)
        mid.pack(fill=X, pady=(8, 0))
        self.preview = ttk.Label(mid, relief="sunken", background="#20242b", width=320)
        self.preview.pack(side=LEFT, fill=Y)
        list_frame = ttk.Frame(mid)
        list_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(10, 0))
        self.batch_list = Listbox(list_frame, height=5)
        self.batch_list.pack(side=LEFT, fill=BOTH, expand=True)
        buttons = ttk.Frame(list_frame)
        buttons.pack(side=RIGHT, fill=Y, padx=(6, 0))
        self._button(buttons, "add_button", self._add_batch).pack(fill=X)
        self._button(buttons, "add_folder_button", self._add_folder).pack(fill=X, pady=4)
        self._button(buttons, "remove_button", self._remove_batch).pack(fill=X, pady=4)
        self._button(buttons, "clear_button", self._clear_batch).pack(fill=X)
        self._button(buttons, "generate_videos", self._generate_batch,
                     style="Primary.TButton").pack(fill=X, pady=(12, 0))

        self._label(batch, "qc_preflight").pack(anchor="w", pady=(8, 0))
        self.qc_text = Text(batch, height=5, wrap="word", font=("Consolas", 9))
        self.qc_text.pack(fill=X)

        self._apply_profile()

    def refresh_language(self, _lang: str | None = None) -> None:
        """Reaplica os rotulos sem tocar em midia, letra, faixa ou perfil."""
        for widget, key in list(self._language_widgets):
            try:
                if widget is None or not widget.winfo_exists():
                    continue
                widget.configure(text=t(key))
            except Exception:
                pass
        profile = self.profiles.get("profiles", {}).get(self.active_profile, {})
        description = (profile.get("description") or t("profile_help")) \
            if self.active_profile else t("profile_help")
        self.profile_info.configure(text=description)
        if self.media_path is None:
            self.media_name.configure(text=t("no_media"))
        current_mode = self.mode_box.current()
        self.mode_box.configure(values=[t("auto"), t("manual")])
        self.mode_box.current(max(0, current_mode))
        audio_worker = self.app.audio_worker
        audio_busy = audio_worker is not None and audio_worker.is_alive()
        if not self.production_busy() and not audio_busy:
            self.status.configure(text=t("ready"))

    # ---- fachada para os modulos (somente leitura) ----------------------
    def selected_batch_item(self) -> str | None:
        selection = self.batch_list.curselection()
        if selection:
            return str(self.batch_list.get(selection[0]))
        return None

    def background_image(self) -> Path | None:
        value = self.bg_var.get().strip()
        return Path(value) if value else None

    def first_batch_item(self) -> str | None:
        if self.batch_list.size():
            return str(self.batch_list.get(0))
        return None

    def production_busy(self) -> bool:
        return bool(self.worker is not None and self.worker.is_alive())

    # ---- helpers locais -------------------------------------------------
    def _format_time(self, seconds: float) -> str:
        return _format_time(seconds)

    def _result_value(self, result, key: str, default=None):
        return _result_value(result, key, default)

    # ---- perfil ---------------------------------------------------------
    def _profile_timing(self) -> dict[str, int]:
        """Tempos do perfil ativo resolvidos por profiles_api (com padroes)."""
        timing = {
            "max_chars": 30,
            "lead_ms": 600,
            "end_pad_ms": 500,
            "max_duration_ms": 10000,
        }
        if self.active_profile not in profiles_api.list_profiles():
            return timing
        alignment = profiles_api.profile_settings(self.active_profile)["alignment"]
        for key in timing:
            timing[key] = int(alignment.get(key, timing[key]))
        return timing

    def _apply_profile(self) -> None:
        """Apply the selected profile's timing settings to the controls."""
        if self.active_profile in profiles_api.list_profiles():
            profile = profiles_api.get_profile(self.active_profile)
            alignment = profiles_api.profile_settings(self.active_profile)["alignment"]
            self.max_chars.set(str(alignment.get("max_chars", 30)))
            self.max_duration.set(str(int(alignment.get("max_duration_ms", 10000) / 1000)))
            hold = int(alignment.get("end_pad_ms", 500))
            holds = [300, 500, 1000, 1500]
            index = holds.index(hold) if hold in holds else 1
            self.end_hold.current(index)
            self.lead_ms = int(alignment.get("lead_ms", 600))
            if self.mode_box.cget("values"):
                self.mode_box.current(1 if alignment.get("mode") == "manual" else 0)
        else:
            profile = {}
        self.profile_info.configure(text=(profile or {}).get("description", ""))
        extras = (profile or {}).get("extras", {})
        self.intro_seconds.set(str(int(extras.get("intro_seconds", 0))))
        # projects_root e legado: usado apenas para localizar o logo final,
        # nunca para trocar a pasta de projetos.
        root = Path((profile or {}).get("projects_root") or "")
        logo_name = extras.get("outro_image_file")
        if logo_name and (root / logo_name).is_file():
            self.logo_var.set(str(root / logo_name))
        else:
            self.logo_var.set("")
        self.bg_var.set("")
        self._update_manual_visibility()
        self._update_preview()

    def _update_preview(self) -> None:
        """Desenha uma prévia aproximada com a fonte/cor/posicao do perfil."""
        if not hasattr(self, "preview"):
            return
        profile = self.profiles.get("profiles", {}).get(self.active_profile, {})
        k = (profile.get("settings", {}).get("karaoke") or {})
        bg_name = self.bg_var.get().strip()
        bg_path = Path(bg_name) if bg_name else None
        if not bg_path or not bg_path.is_file():
            first = self.batch_list.get(0) if self.batch_list.size() else ""
            if first:
                bg_path = next((p for p in Path(first).parent.glob("FUNDO*")
                                if p.suffix.lower() in {".png", ".jpg", ".jpeg"}), None)
        size = (320, 180)
        try:
            if bg_path and bg_path.is_file():
                base = Image.open(bg_path).convert("RGB").resize(size)
            else:
                base = Image.new("RGB", size, (255, 243, 232))
            draw = ImageDraw.Draw(base)
            font_size = max(10, int(int(k.get("font_size", 84)) * size[1] / 1080 * 1.6))
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except OSError:
                font = ImageFont.load_default()
            color = k.get("normal_color", "#000000")
            lines = ["Caminhando eu vou para Canaã", "Se você não vai... lhe convido a ir",
                     "Glória a Deus, Caminhando eu vou para Canaã."]
            y = int(size[1] * 0.45)
            for line in lines:
                draw.text((size[0] // 2, y), line, fill=color, font=font, anchor="mm")
                y += font_size + 4
            logo = self.logo_var.get().strip()
            if logo and Path(logo).is_file():
                mark_image = Image.open(logo).convert("RGBA")
                mark_image.thumbnail((80, 80))
                base.paste(mark_image, (size[0] - 88, 8), mark_image)
            image = ImageTk.PhotoImage(base)
            self.preview.configure(image=image)
            self.preview.image = image
        except Exception:
            pass

    def _profile_changed(self, _event=None) -> None:
        name = self.profile_box.get()
        # Dono unico: a fachada grava profiles.json.active e settings.profile.
        self.app.set_active_profile(name)
        self._apply_profile()

    def _browse_logo(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[(t("image_filetype"), "*.png *.jpg *.jpeg *.webp")])
        if path:
            self.logo_var.set(path)
            self._update_preview()

    def _browse_bg(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[(t("image_filetype"), "*.png *.jpg *.jpeg *.webp")])
        if path:
            self.bg_var.set(path)
            self._update_preview()

    # ---- lote -----------------------------------------------------------
    def _add_batch(self) -> None:
        files = filedialog.askopenfilenames(filetypes=[
            (t("media_filetype"),
             "*.mp4 *.mov *.mkv *.avi *.mpeg *.mpg *.mp3 *.wav *.m4a *.aac *.flac *.ogg")])
        for item in files:
            if item not in self.batch_list.get(0, END):
                self.batch_list.insert(END, item)
        self._update_preview()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title=t("select_hymns_folder"))
        if not folder:
            return
        root = Path(folder)
        extensions = {".mp4", ".mov", ".mkv", ".avi", ".mpeg", ".mpg",
                      ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in extensions:
                item = str(path)
                if item not in self.batch_list.get(0, END):
                    self.batch_list.insert(END, item)
        self._update_preview()

    def _scene_name_for(self, folder: Path, count: int,
                        template: str | None = None) -> str:
        # ``template`` vem do snapshot capturado na thread principal; nunca
        # ler StringVar/widget do Tk a partir da thread do lote.
        template = (template or "").strip() or "{pasta}"
        name = template.replace("{pasta}", folder.name)
        if count == 1 and "{pasta}" not in template:
            name = template
        invalid = '<>:"/\\|?*'
        return "".join(ch for ch in name if ch not in invalid).strip() or folder.name

    def _write_history(self, folder: Path, outputs: list[str],
                       warnings: list[str]) -> None:
        path = folder / "history.csv"
        is_new = not path.exists()
        try:
            with path.open("a", encoding="utf-8-sig", newline="") as handle:
                handle.write(
                    ("" if is_new else "\n")
                    + f"{datetime.now():%Y-%m-%d %H:%M:%S};{self.active_profile};"
                    + ";".join(Path(o).name for o in outputs)
                    + ";" + " | ".join(warnings)[:300]
                )
        except OSError:
            pass

    def _record_history(self, folders, outputs: list[str],
                        warnings: list[str]) -> None:
        """Grava history.csv uma vez por pasta, sem duplicar a mesma linha."""
        seen: set[str] = set()
        for folder in folders:
            if folder is None:
                continue
            folder = Path(folder)
            try:
                key = str(folder.resolve())
            except OSError:
                key = str(folder)
            if key in seen:
                continue
            seen.add(key)
            self._write_history(folder, outputs, warnings)

    def _update_project_outputs(self, project_dir: str | Path | None,
                                outputs: dict[str, str] | None = None,
                                **details) -> None:
        """Atualiza ``projeto.json`` de forma aditiva (preserva chaves/BOM)."""
        if not project_dir:
            return
        meta_path = Path(project_dir) / "projeto.json"
        if not meta_path.is_file():
            return
        fields: dict[str, object] = {}
        if outputs:
            fields["outputs"] = dict(outputs)
        fields.update(details)
        try:
            update_project_meta(meta_path, **fields)
        except (OSError, ValueError):
            pass

    def _remove_batch(self) -> None:
        for index in reversed(self.batch_list.curselection()):
            self.batch_list.delete(index)

    def _clear_batch(self) -> None:
        self.batch_list.delete(0, END)

    def _append_qc(self, text: str) -> None:
        self.qc_text.configure(state="normal")
        self.qc_text.insert(END, text + "\n")
        self.qc_text.see(END)
        self.qc_text.configure(state="disabled")

    def _generate_batch(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        items = list(self.batch_list.get(0, END))
        if not items:
            messagebox.showinfo(APP_NAME, t("need_media"))
            return
        self.qc_text.configure(state="normal")
        self.qc_text.delete("1.0", END)
        self.qc_text.configure(state="disabled")
        # Snapshot imutavel capturado NA thread principal: a thread do lote
        # nunca deve ler StringVar/Widgets do Tk.
        snapshot = {
            "bg": self.bg_var.get(),
            "logo": self.logo_var.get(),
            "transition": self.transition_var.get(),
            "name_template": self.name_var.get(),
            "profile": self.active_profile,
            "scene": self.app.scene_snapshot(),
        }
        self.cancel_event.clear()
        self.cancel_button.pack(side=RIGHT)
        self.worker = threading.Thread(
            target=self._batch_worker, args=(items, snapshot), daemon=True
        )
        self.worker.start()

    def _batch_worker(self, items: list[str], snapshot: dict) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(VILM_APP_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        bg_value = snapshot["bg"].strip()
        logo_value = snapshot["logo"].strip()
        transition = snapshot["transition"]
        name_template = snapshot["name_template"]
        has_scene = bool(snapshot["scene"]["layers"]) \
            if "layers" in snapshot["scene"] else False
        cancelled = False
        for item in items:
            if self.cancel_event.is_set():
                cancelled = True
                break
            media = Path(item)
            folder = media.parent
            lyrics = folder / "letra.txt"
            if not lyrics.is_file():
                self.app.emit("batch_status", t("batch_no_lyrics", folder=folder.name))
                continue
            images = [p for p in folder.iterdir()
                      if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
            title = next((p for p in images if "FUNDO" not in p.name.upper()), None)
            bg = Path(bg_value) if bg_value else next(
                (p for p in images if "FUNDO" in p.name.upper()), None)
            playback = next((p for p in folder.iterdir()
                             if p.is_file() and "PLAYBACK" in p.name.upper()), None)
            logo = Path(logo_value) if logo_value else None
            saida = folder / "saida"
            saida.mkdir(parents=True, exist_ok=True)
            output = saida / (
                self._scene_name_for(folder, len(items), template=name_template) + ".mp4"
            )
            self.app.emit("batch_status", t("batch_processing", folder=folder.name))
            work = folder / "trabalho" / "cli"
            cmd = [str(console_python(sys.executable)), "-m", "lyrics_aligner",
                   "make", str(media), str(lyrics), "-o", str(output),
                   "--profile", snapshot["profile"], "--work", str(work)]
            if title:
                cmd += ["--title-image", str(title)]
            if bg and bg.is_file():
                cmd += ["--background", str(bg)]
            if logo and logo.is_file():
                cmd += ["--logo", str(logo)]
            if playback:
                cmd += ["--playback", str(playback)]
            if transition:
                cmd += ["--transition", transition, "--transition-duration", "0.4"]
            if has_scene:
                from .scene import save_scene
                scene_file = work / "scene.json"
                save_scene(scene_file, snapshot["scene"])
                cmd += ["--scene", str(scene_file)]
            try:
                result = run_job(cmd, env=env,
                                 cancel_requested=self.cancel_event.is_set)
                if self.cancel_event.is_set():
                    cancelled = True
                    break
                if result.returncode != 0:
                    self.app.emit(
                        "batch_status",
                        t("batch_failed", folder=folder.name,
                          detail=(result.output or "")[-300:]))
                    continue
                self.app.emit("batch_status", t("batch_ok", folder=folder.name))
                report_path = work / "qc.json"
                report = json.loads(report_path.read_text(encoding="utf-8")) \
                    if report_path.is_file() else {}
                warnings = list(report["warnings"]) if "warnings" in report else []
                for warning in warnings:
                    self.app.emit("qc", f"{folder.name}: {warning}")
                outputs = report["outputs"] if "outputs" in report else [str(output)]
                project_root = self.app.project_root_for(media)
                self._record_history([folder, saida, project_root], outputs, warnings)
                self._update_project_outputs(
                    project_root,
                    outputs={
                        "desktop_batch_video": Path(output).name,
                        "desktop_batch_qc": "qc.json",
                    },
                    desktop_batch={
                        "profile": snapshot["profile"],
                        "folder": folder.name,
                        "video": Path(output).name,
                        "qc": "qc.json",
                        "warnings": warnings,
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    },
                )
            except JobCancelled:
                self.app.emit("batch_status", t("batch_cancelled", folder=folder.name))
                cancelled = True
                break
            except Exception as exc:
                self.app.emit(
                    "batch_status",
                    t("batch_failed", folder=folder.name, detail=exc))
        self.app.emit("batch_done", "cancelado" if cancelled else None)

    # ---- midia, faixa e letra -------------------------------------------
    def _browse_media(self) -> None:
        selected = filedialog.askopenfilename(
            title=t("browse"),
            filetypes=[
                (t("audio_media_label"),
                 "*.mp4 *.mov *.mkv *.avi *.m4a *.aac *.mp3 *.wav *.flac *.ogg *.opus *.webm"),
                (t("all_files_filetype"), "*.*"),
            ],
        )
        if selected:
            self._load_media(Path(selected))

    def _load_media(self, path: Path) -> None:
        self.media_path = path
        self.app.bind_project(path)
        self.media_name.configure(text=path.name)
        self.set_status(t("loading"))
        self.peaks = []
        self._draw_waveform()
        threading.Thread(target=self._waveform_worker, args=(path,), daemon=True).start()

    def _waveform_worker(self, path: Path) -> None:
        try:
            duration, peaks = waveform_peaks(path, bins=900)
            self.app.emit("waveform", (path, duration, peaks))
        except Exception as exc:
            self.app.emit("media_error", (path, str(exc)))

    def _range_mode_changed(self) -> None:
        if self.range_mode.get() == "full":
            self.range_start = 0.0
            self.range_end = self.duration
        self._update_range_readout()
        self._draw_waveform()

    def _wave_press(self, event) -> None:
        if self.duration <= 0:
            return
        self.range_mode.set("selection")
        self.drag_start_x = float(event.x)
        position = self._x_to_time(event.x)
        self.range_start = position
        self.range_end = position
        self._draw_waveform()

    def _wave_drag(self, event) -> None:
        if self.drag_start_x is None or self.duration <= 0:
            return
        first = self._x_to_time(self.drag_start_x)
        second = self._x_to_time(event.x)
        self.range_start, self.range_end = sorted((first, second))
        self._update_range_readout()
        self._draw_waveform()

    def _wave_release(self, event) -> None:
        self._wave_drag(event)
        self.drag_start_x = None

    def _x_to_time(self, x: float) -> float:
        width = max(1, self.waveform.winfo_width())
        return max(0.0, min(self.duration, float(x) / width * self.duration))

    def _draw_waveform(self) -> None:
        canvas = self.waveform
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        middle = height / 2
        if self.peaks:
            step = width / len(self.peaks)
            for index, peak in enumerate(self.peaks):
                x = index * step
                amplitude = max(1.0, peak * (height * 0.44))
                canvas.create_line(x, middle - amplitude, x, middle + amplitude,
                                   fill="#79d8a5")
        if self.duration > 0 and self.range_mode.get() == "selection":
            left = self.range_start / self.duration * width
            right = self.range_end / self.duration * width
            canvas.create_rectangle(0, 0, left, height, fill="#101318",
                                    stipple="gray50", outline="")
            canvas.create_rectangle(right, 0, width, height, fill="#101318",
                                    stipple="gray50", outline="")
            canvas.create_line(left, 0, left, height, fill="#ffd166", width=2)
            canvas.create_line(right, 0, right, height, fill="#ffd166", width=2)

    def _update_range_readout(self) -> None:
        end = self.duration if self.range_mode.get() == "full" else self.range_end
        start = 0.0 if self.range_mode.get() == "full" else self.range_start
        self.range_readout.configure(
            text=f"{_format_time(start)} — {_format_time(end)}")

    def _load_lyrics(self) -> None:
        selected = filedialog.askopenfilename(
            title=t("load"),
            filetypes=[(t("text_filetype"), "*.txt"),
                       (t("all_files_filetype"), "*.*")])
        if selected:
            text = Path(selected).read_text(encoding="utf-8-sig")
            self.lyrics.delete("1.0", END)
            self.lyrics.insert("1.0", text)

    def _update_manual_visibility(self) -> None:
        if self.mode_box.current() == 1:
            self.manual_frame.pack(fill=X, pady=(8, 0), before=self.guide)
        else:
            self.manual_frame.pack_forget()

    def _selected_device(self) -> str:
        if self.app.backend in {"cuda", "mps"} and self.device_box.current() == 0:
            return self.app.backend
        return "cpu"

    # ---- geracao de SRT -------------------------------------------------
    def _generate(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if self.media_path is None or not self.media_path.is_file():
            messagebox.showinfo(APP_NAME, t("need_media"))
            return
        lyrics = self.lyrics.get("1.0", END).strip()
        if not lyrics:
            messagebox.showinfo(APP_NAME, t("need_lyrics"))
            return
        selected = self.range_mode.get() == "selection"
        if selected and self.range_end - self.range_start < 1.0:
            messagebox.showinfo(APP_NAME, t("bad_range"))
            return
        output_dir = Path.home() / "Documents" / APP_NAME
        project_root = self.app.project_root_for(self.media_path)
        if project_root is not None:
            output_dir = project_root / "saida"
        output_dir.mkdir(parents=True, exist_ok=True)
        default_name = f"{self.media_path.stem}-{datetime.now():%Y%m%d-%H%M%S}.srt"
        output = filedialog.asksaveasfilename(
            title=t("save_title"),
            initialdir=str(output_dir),
            initialfile=default_name,
            defaultextension=".srt",
            filetypes=[(t("subtitle_filetype"), "*.srt")],
        )
        if not output:
            return

        handle, lyrics_name = tempfile.mkstemp(prefix="vilm-lyrics-", suffix=".txt")
        os.close(handle)
        lyrics_path = Path(lyrics_name)
        lyrics_path.write_text(lyrics, encoding="utf-8")
        self.cancel_event.clear()
        self.last_output = None
        self.generate_button.pack_forget()
        self.cancel_button.pack(side=RIGHT)
        self.open_folder_button.pack_forget()
        self.progress.start(12)
        self.set_status(t("processing"))
        self._set_log("")
        options = {
            "max_chars": self.max_chars.get(),
            "max_duration": self.max_duration.get(),
            "end_hold_index": self.end_hold.current(),
            "lead_ms": self.lead_ms,
            "automatic": self.mode_box.current() == 0,
            "device": self._selected_device(),
            "range_start": self.range_start,
            "range_end": self.range_end,
        }
        self.worker = threading.Thread(
            target=self._alignment_worker,
            args=(lyrics_path, Path(output), selected, options),
            daemon=True,
        )
        self.worker.start()

    def _alignment_worker(self, lyrics_path: Path, output: Path,
                          selected: bool, options: dict) -> None:
        assert self.media_path is not None
        output = Path(output)
        media = Path(self.media_path)
        # Automatico: tempos do perfil ativo (profiles_api).
        # Manual: valores configurados pelo usuario na thread principal.
        overrides: dict[str, object] = {}
        if options["automatic"]:
            timing = self._profile_timing()
            overrides.update({
                "auto_segment": True,
                "max_chars": timing["max_chars"],
                "lead_ms": timing["lead_ms"],
                "max_duration_ms": timing["max_duration_ms"],
                "end_pad_ms": timing["end_pad_ms"],
            })
        else:
            overrides.update({
                "max_chars": options["max_chars"],
                "lead_ms": int(options["lead_ms"]),
                "max_duration_ms": int(options["max_duration"]) * 1000,
                "end_pad_ms": [300, 500, 1000, 1500][int(options["end_hold_index"])],
            })
        overrides["min_gap_ms"] = 80
        overrides["timeline_anchor"] = True
        if selected:
            overrides.update({
                "range_start": f"{float(options['range_start']):.6f}",
                "range_end": f"{float(options['range_end']):.6f}",
                "partial_range": True,
            })
        project_dir = self.app.project_root_for(media)
        service_root = project_dir or output.parent
        work = Path(service_root) / "trabalho" / "cli"
        lyrics_text = Path(lyrics_path).read_text(encoding="utf-8-sig")
        previous_device = os.environ.get("LYRICS_ALIGNER_DEVICE")
        os.environ["LYRICS_ALIGNER_DEVICE"] = str(options.get("device") or "cpu")
        try:
            result = generate_alignment(
                project_dir=service_root,
                media=media,
                lyrics_text=lyrics_text,
                profile_name=self.active_profile,
                output=output,
                work=work,
                overrides=overrides,
                progress=lambda line: self.app.emit("log", line),
                cancel_requested=self.cancel_event.is_set,
                strict_qc=False,
            )
            srt = Path(_result_value(result, "srt") or output)
            qc = _result_value(result, "qc") or {}
            warnings = list(_result_value(result, "warnings")
                            or (qc.get("warnings") if isinstance(qc, dict) else None)
                            or [])
            self.last_warnings = warnings
            # QC sempre visivel: o servico ja roda o relatorio mesmo sem strict.
            for warning in warnings:
                self.app.emit("qc", warning)
            qc_path = _result_value(result, "qc_path") or work / "qc.json"
            cues = _result_value(result, "cues") or []
            self._record_history(
                [srt.parent, media.parent, project_dir], [str(srt)], warnings)
            self._update_project_outputs(
                project_dir,
                outputs={
                    "desktop_alignment_srt": srt.name,
                    "desktop_alignment_qc": Path(qc_path).name,
                },
                desktop_alignment={
                    "profile": self.active_profile,
                    "srt": srt.name,
                    "qc": Path(qc_path).name,
                    "warnings": warnings,
                    "cues": len(cues),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
            self.app.emit("complete", srt)
        except JobCancelled:
            output.unlink(missing_ok=True)
            self.app.emit("cancelled", None)
        except Exception as exc:
            output.unlink(missing_ok=True)
            self.app.emit("failed", str(exc))
        finally:
            if previous_device is None:
                os.environ.pop("LYRICS_ALIGNER_DEVICE", None)
            else:
                os.environ["LYRICS_ALIGNER_DEVICE"] = previous_device
            Path(lyrics_path).unlink(missing_ok=True)

    # ---- cancelamento, log e eventos -----------------------------------
    def _cancel(self) -> None:
        self.cancel_event.set()
        self.set_status(f"{t('cancelled')}…")

    def _finish_job(self, show_folder: bool = False) -> None:
        self.progress.stop()
        self.cancel_button.pack_forget()
        if show_folder:
            self.open_folder_button.pack(side=RIGHT, padx=(0, 8))
        self.generate_button.pack(side=RIGHT)

    def _set_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", END)
        if text:
            self.log.insert(END, text)
        self.log.configure(state="disabled")

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(END, text + "\n")
        self.log.see(END)
        self.log.configure(state="disabled")

    def _open_output_folder(self) -> None:
        if self.last_output is None:
            return
        folder = str(self.last_output.parent)
        # A abertura do explorador e platform-specific e vive na fachada;
        # assim este modulo nao precisa de nenhum executor externo.
        opener = getattr(self.app, "open_folder", None)
        if callable(opener):
            opener(folder)

    # Handlers dos eventos despachados por DesktopApp._poll_events.
    def _on_waveform(self, payload) -> None:
        path, duration, peaks = payload
        if self.media_path == path:
            self.duration = float(duration)
            self.peaks = list(peaks)
            self.range_start = 0.0
            self.range_end = self.duration
            self.range_mode.set("full")
            self._update_range_readout()
            self._draw_waveform()
            self.set_status(t("ready"))

    def _on_media_error(self, payload) -> None:
        path, error = payload
        if self.media_path == path:
            self.media_path = None
            self.media_name.configure(text=t("no_media"))
            self.set_status(t("ready"))
            messagebox.showerror(t("file_error"), error)

    def _on_log(self, payload) -> None:
        self._append_log(str(payload))

    def _on_complete(self, payload) -> None:
        self.last_output = Path(payload)
        self.set_status(f"{t('done')}  {self.last_output}")
        self._finish_job(show_folder=True)

    def _on_cancelled(self, payload=None) -> None:
        self.set_status(t("cancelled"))
        self._finish_job()

    def _on_failed(self, payload) -> None:
        self.set_status(t("failed"))
        self._finish_job()
        messagebox.showerror(t("failed"), str(payload))

    def _on_batch_status(self, payload) -> None:
        self.set_status(str(payload))
        self._append_log(str(payload))

    def _on_qc(self, payload) -> None:
        self._append_qc(str(payload))

    def _on_batch_done(self, payload) -> None:
        if payload:
            self.set_status(t("batch_done_cancelled"))
        else:
            self.set_status(t("batch_done_ok"))
        self.cancel_button.pack_forget()
        self._append_qc(t("batch_end"))


_PRODUCTION_TABS: WeakSet["ProductionTab"] = WeakSet()


def _on_language_changed(lang: str) -> None:
    for tab in list(_PRODUCTION_TABS):
        try:
            tab.refresh_language(lang)
        except Exception:
            pass


on_language_changed(_on_language_changed)
