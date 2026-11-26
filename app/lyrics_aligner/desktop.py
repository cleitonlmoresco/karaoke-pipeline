from __future__ import annotations

import copy
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from tkinter import (BOTH, END, LEFT, RIGHT, X, Y, BooleanVar, Canvas, Checkbutton,
                     DoubleVar, IntVar, Listbox, Scale, StringVar, Text, Tk, Toplevel,
                     filedialog, messagebox)
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from .karaoke_jobs import load_profiles
from .media import waveform_peaks
from .platform_paths import application_data_root
from .process_runner import JobCancelled, run_streaming_process


APP_NAME = "Vilm Lyrics Aligner"
APP_DIR = application_data_root()
CONFIG_PATH = APP_DIR / "config.json"
PREFERENCES_PATH = APP_DIR / "desktop-preferences.json"
VILM_APP_PATH = Path(__file__).resolve().parents[2] / "app"
AUTOSAVE_PATH = APP_DIR / "scene-autosave.json"

TEXT = {
    "en": {
        "profile": "Karaoke profile (pre-set)",
        "profile_help": "Choosing a profile applies its timing and style settings to new subtitles.",
        "media": "1. Media",
        "drop": "Choose the video or audio that matches the edit where subtitles will be used.",
        "browse": "Choose media…",
        "no_media": "No media selected",
        "range": "2. Processing range",
        "full": "Full file",
        "selection": "Selected waveform range",
        "range_help": "For a long file, drag across the waveform. Only the selected range is processed.",
        "lyrics": "3. Original lyrics",
        "lyrics_hint": "Paste the full lyrics. Vilm will locate the lines heard in a selected range.",
        "load": "Load TXT…",
        "mode": "4. Subtitle style",
        "auto": "Auto mode (recommended)",
        "manual": "Manual (advanced)",
        "max_chars": "Max characters",
        "max_duration": "Max duration (sec)",
        "end_hold": "End hold",
        "device": "Processing device",
        "generate": "Generate SRT",
        "cancel": "Cancel",
        "ready": "Ready",
        "loading": "Reading media and drawing waveform…",
        "processing": "Processing locally. Full songs can take several minutes.",
        "done": "SRT created successfully.",
        "open_folder": "Open output folder",
        "need_media": "Choose a media file first.",
        "need_lyrics": "Paste the original lyrics first.",
        "bad_range": "Select a range of at least one second.",
        "save_title": "Save subtitle file",
        "file_error": "Could not read this media file",
        "failed": "Generation failed",
        "cancelled": "Cancelled",
        "guide": "Complex edited timeline? Use the NLE integration when available. Otherwise export a lightweight full-timeline reference audio file, then select only the needed range here.",
    },
    "ko": {
        "profile": "카라오케 프로필 (프리셋)",
        "profile_help": "프로필을 선택하면 새 자막에 해당 타이밍·스타일 설정이 적용됩니다.",
        "media": "1. 미디어",
        "drop": "자막을 사용할 편집본과 오디오 타이밍이 같은 영상 또는 음원을 선택하세요.",
        "browse": "영상·음원 선택…",
        "no_media": "선택한 미디어 없음",
        "range": "2. 처리 범위",
        "full": "전체 파일",
        "selection": "파형에서 선택한 구간",
        "range_help": "긴 파일은 파형을 드래그하세요. 선택한 구간만 AI가 처리합니다.",
        "lyrics": "3. 원문 가사",
        "lyrics_hint": "전체 가사를 붙여넣으세요. 선택 구간에서 들리는 줄은 Vilm이 찾습니다.",
        "load": "가사 TXT 불러오기…",
        "mode": "4. 자막 구성",
        "auto": "자동 모드 (권장)",
        "manual": "수동(고급) 모드",
        "max_chars": "최대 글자 수",
        "max_duration": "최대 표시(초)",
        "end_hold": "끝 여운",
        "device": "처리 장치",
        "generate": "SRT 생성",
        "cancel": "취소",
        "ready": "대기 중",
        "loading": "미디어를 읽고 파형을 만드는 중…",
        "processing": "로컬에서 처리 중입니다. 한 곡은 몇 분 걸릴 수 있습니다.",
        "done": "SRT를 생성했습니다.",
        "open_folder": "출력 폴더 열기",
        "need_media": "먼저 영상 또는 음원을 선택해 주세요.",
        "need_lyrics": "먼저 원문 가사를 붙여넣어 주세요.",
        "bad_range": "1초 이상의 구간을 선택해 주세요.",
        "save_title": "자막 파일 저장",
        "file_error": "미디어 파일을 읽지 못했습니다",
        "failed": "생성 실패",
        "cancelled": "취소됨",
        "guide": "복잡하게 편집된 타임라인은 가능하면 NLE 연동을 사용하세요. 연동할 수 없다면 가벼운 전체 타임라인 참고용 오디오를 출력한 뒤 필요한 구간만 여기서 선택하세요.",
    },
}


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes, remainder = divmod(seconds, 60.0)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remainder:05.2f}"
    return f"{minutes:02d}:{remainder:05.2f}"


class DesktopApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.preferences = _load_json(PREFERENCES_PATH)
        self.language = self.preferences.get("ui_language", "en")
        if self.language not in TEXT:
            self.language = "en"
        self.config = _load_json(CONFIG_PATH)
        self.backend = str(self.config.get("backend", "cpu")).casefold()
        self.media_path: Path | None = None
        self.duration = 0.0
        self.peaks: list[float] = []
        self.range_start = 0.0
        self.range_end = 0.0
        self.drag_start_x: float | None = None
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.last_output: Path | None = None
        self.profiles = load_profiles()
        self.active_profile = str(
            self.preferences.get("profile") or self.profiles.get("active") or ""
        )
        self.lead_ms = 600

        root.title(APP_NAME)
        root.geometry("860x720")
        root.minsize(720, 620)
        self._configure_style()
        self._build()
        self._apply_language()
        self._setup_shortcuts()
        root.after(100, self._poll_events)

    def tr(self, key: str) -> str:
        return TEXT[self.language][key]

    def _typing(self) -> bool:
        widget = self.root.focus_get()
        return widget is not None and widget.winfo_class() in {
            "Text", "Entry", "TEntry", "Spinbox", "TSpinbox", "Combobox", "TCombobox"
        }

    def _setup_shortcuts(self) -> None:
        """Atalhos no estilo de estacoes de edicao (Space, Delete, Ctrl+S/Z/Y)."""
        self.root.bind_all("<Control-s>", lambda _e: self._scene_save())
        self.root.bind_all("<Control-z>", lambda _e: None if self._typing() else self._scene_undo())
        self.root.bind_all("<Control-y>", lambda _e: None if self._typing() else self._scene_redo())
        self.root.bind_all("<Delete>", lambda _e: None if self._typing() else self._scene_remove())
        self.root.bind_all("<space>", lambda _e: None if self._typing() else self._scene_preview())

    # ---- ferramentas de audio ------------------------------------------
    def _audio_use_selected(self) -> None:
        selection = self.batch_list.curselection()
        if selection:
            self.audio_media.set(self.batch_list.get(selection[0]))
        elif self.media_path:
            self.audio_media.set(str(self.media_path))

    def _audio_path(self) -> Path | None:
        value = self.audio_media.get().strip()
        path = Path(value) if value else None
        if path is None or not path.is_file():
            messagebox.showinfo(APP_NAME, "Escolha uma mídia para processar.")
            return None
        return path

    def _audio_run(self, args: list[str], suffix: str) -> None:
        media = self._audio_path()
        if media is None:
            return
        output = media.with_name(media.stem + " - " + suffix + ".wav")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(VILM_APP_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        python = Path(sys.executable)
        if python.name.casefold() == "pythonw.exe":
            python = python.with_name("python.exe")
        command = [str(python), "-m", "lyrics_aligner", "audio", str(media),
                   "-o", str(output), "--work", str(media.parent / "trabalho" / "audio"), *args]
        self.audio_log.configure(state="normal")
        self.audio_log.delete("1.0", END)
        self.audio_log.insert(END, "Processando áudio…\n")
        self.audio_log.configure(state="disabled")
        self.root.update_idletasks()
        try:
            result = subprocess.run(command, env=env, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
            text = (result.stdout or "") + "\n" + (result.stderr or "")
        except Exception as exc:
            text = str(exc)
        self.audio_log.configure(state="normal")
        self.audio_log.insert(END, text[-4000:])
        self.audio_log.see(END)
        self.audio_log.configure(state="disabled")

    def _audio_analyze(self) -> None:
        media = self._audio_path()
        if media is None:
            return
        env = os.environ.copy(); env["PYTHONPATH"] = str(VILM_APP_PATH)
        result = subprocess.run(
            [sys.executable, "-m", "lyrics_aligner", "audio", str(media),
             "-o", str(media.with_suffix(".wav")), "--analyze"],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.audio_log.configure(state="normal")
        self.audio_log.delete("1.0", END)
        self.audio_log.insert(END, result.stdout or result.stderr)
        self.audio_log.configure(state="disabled")

    def _audio_instrumental(self) -> None:
        self._audio_run(["--mode", "instrumental"], "instrumental")

    def _audio_voice(self, gain: float) -> None:
        self._audio_run(["--mode", "mix", "--voice-gain", str(gain),
                         "--music-gain", "1.0", "--normalize"], "voz")

    def _audio_denoise(self) -> None:
        self._audio_run(["--denoise", "8", "--normalize"], "limpo")

    def _audio_enhance(self) -> None:
        self._audio_run(["--enhance", "voz", "--normalize"], "melhorado")

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 8))

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill=BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=X, pady=(0, 10))
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side=LEFT)
        self.language_box = ttk.Combobox(header, state="readonly", width=12, values=["English", "한국어"])
        self.language_box.pack(side=RIGHT)
        self.language_box.current(0 if self.language == "en" else 1)
        self.language_box.bind("<<ComboboxSelected>>", self._language_changed)

        profile_frame = ttk.Frame(outer)
        profile_frame.pack(fill=X, pady=(0, 8))
        self.profile_label = ttk.Label(profile_frame, style="Section.TLabel")
        self.profile_label.pack(anchor="w")
        self.profile_info = ttk.Label(profile_frame, style="Hint.TLabel", wraplength=900)
        self.profile_info.pack(anchor="w", pady=(2, 4))
        profile_row = ttk.Frame(profile_frame)
        profile_row.pack(fill=X)
        names = list(self.profiles.get("profiles", {}).keys())
        self.profile_box = ttk.Combobox(profile_row, state="readonly", width=36, values=names)
        self.profile_box.pack(side=LEFT)
        if self.active_profile in names:
            self.profile_box.current(names.index(self.active_profile))
        elif names:
            self.active_profile = names[0]
            self.profile_box.current(0)
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_changed)

        self.media_label = ttk.Label(outer, style="Section.TLabel")
        self.media_label.pack(anchor="w")
        self.media_hint = ttk.Label(outer, style="Hint.TLabel", wraplength=900)
        self.media_hint.pack(anchor="w", pady=(2, 6))
        media_row = ttk.Frame(outer)
        media_row.pack(fill=X)
        self.media_name = ttk.Label(media_row, relief="sunken", padding=8)
        self.media_name.pack(side=LEFT, fill=X, expand=True)
        self.browse_button = ttk.Button(media_row, command=self._browse_media)
        self.browse_button.pack(side=RIGHT, padx=(8, 0))

        self.range_label = ttk.Label(outer, style="Section.TLabel")
        self.range_label.pack(anchor="w", pady=(14, 0))
        range_modes = ttk.Frame(outer)
        range_modes.pack(fill=X, pady=(3, 4))
        self.range_mode = StringVar(value="full")
        self.full_radio = ttk.Radiobutton(range_modes, variable=self.range_mode, value="full", command=self._range_mode_changed)
        self.full_radio.pack(side=LEFT)
        self.selection_radio = ttk.Radiobutton(range_modes, variable=self.range_mode, value="selection", command=self._range_mode_changed)
        self.selection_radio.pack(side=LEFT, padx=(20, 0))
        self.range_readout = ttk.Label(range_modes, text="00:00.00 — 00:00.00")
        self.range_readout.pack(side=RIGHT)
        self.waveform = Canvas(outer, height=88, background="#20242b", highlightthickness=1, highlightbackground="#555b66")
        self.waveform.pack(fill=X)
        self.waveform.bind("<Button-1>", self._wave_press)
        self.waveform.bind("<B1-Motion>", self._wave_drag)
        self.waveform.bind("<ButtonRelease-1>", self._wave_release)
        self.waveform.bind("<Configure>", lambda _event: self._draw_waveform())
        self.range_hint = ttk.Label(outer, style="Hint.TLabel", wraplength=900)
        self.range_hint.pack(anchor="w", pady=(4, 0))

        self.lyrics_label = ttk.Label(outer, style="Section.TLabel")
        self.lyrics_label.pack(anchor="w", pady=(14, 0))
        self.lyrics_hint = ttk.Label(outer, style="Hint.TLabel", wraplength=900)
        self.lyrics_hint.pack(anchor="w", pady=(2, 4))
        self.lyrics = Text(outer, height=7, wrap="word", undo=True, font=("Segoe UI", 10))
        self.lyrics.pack(fill=BOTH, expand=True)
        self.load_button = ttk.Button(outer, command=self._load_lyrics)
        self.load_button.pack(anchor="e", pady=(5, 0))

        options = ttk.Frame(outer)
        options.pack(fill=X, pady=(12, 0))
        self.mode_label = ttk.Label(options, style="Section.TLabel")
        self.mode_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.mode_box = ttk.Combobox(options, state="readonly", width=25)
        self.mode_box.grid(row=0, column=1, sticky="ew")
        self.mode_box.bind("<<ComboboxSelected>>", lambda _event: self._update_manual_visibility())
        self.device_label = ttk.Label(options)
        self.device_label.grid(row=0, column=2, sticky="e", padx=(24, 8))
        devices = (
            ["CUDA · NVIDIA", "CPU"]
            if self.backend == "cuda"
            else ["Apple GPU · Metal", "CPU"]
            if self.backend == "mps"
            else ["CPU"]
        )
        self.device_box = ttk.Combobox(options, state="readonly", width=20, values=devices)
        self.device_box.grid(row=0, column=3, sticky="ew")
        self.device_box.current(0)
        options.columnconfigure(1, weight=1)
        options.columnconfigure(3, weight=1)

        self.manual_frame = ttk.Frame(outer)
        ttk.Label(self.manual_frame, text="").grid(row=0, column=0)
        self.max_chars_label = ttk.Label(self.manual_frame)
        self.max_chars_label.grid(row=0, column=1, padx=(0, 5))
        self.max_chars = StringVar(value="30")
        ttk.Spinbox(self.manual_frame, from_=1, to=80, width=6, textvariable=self.max_chars).grid(row=0, column=2)
        self.max_duration_label = ttk.Label(self.manual_frame)
        self.max_duration_label.grid(row=0, column=3, padx=(18, 5))
        self.max_duration = StringVar(value="10")
        ttk.Spinbox(self.manual_frame, from_=3, to=30, width=6, textvariable=self.max_duration).grid(row=0, column=4)
        self.end_hold_label = ttk.Label(self.manual_frame)
        self.end_hold_label.grid(row=0, column=5, padx=(18, 5))
        self.end_hold = ttk.Combobox(self.manual_frame, state="readonly", width=10, values=["0.3 s", "0.5 s", "1.0 s", "1.5 s"])
        self.end_hold.grid(row=0, column=6)
        self.end_hold.current(1)

        self.guide = ttk.Label(outer, style="Hint.TLabel", wraplength=900)
        self.guide.pack(fill=X, pady=(10, 6))
        action_row = ttk.Frame(outer)
        action_row.pack(fill=X)
        self.status = ttk.Label(action_row)
        self.status.pack(side=LEFT, fill=X, expand=True)
        self.open_folder_button = ttk.Button(action_row, command=self._open_output_folder)
        self.cancel_button = ttk.Button(action_row, command=self._cancel)
        self.generate_button = ttk.Button(action_row, style="Primary.TButton", command=self._generate)
        self.generate_button.pack(side=RIGHT)
        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.pack(fill=X, pady=(7, 0))
        self.log = Text(outer, height=4, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.pack(fill=X, pady=(6, 0))

        batch = ttk.LabelFrame(outer, text="Vídeo karaokê (lote)", padding=8)
        batch.pack(fill=X, pady=(10, 0))
        top = ttk.Frame(batch); top.pack(fill=X)
        ttk.Label(top, text="Intro (s)").pack(side=LEFT)
        self.intro_seconds = StringVar(value="4")
        ttk.Spinbox(top, from_=0, to=10, width=5, textvariable=self.intro_seconds).pack(side=LEFT, padx=(4, 14))
        ttk.Label(top, text="Logo final").pack(side=LEFT)
        self.logo_var = StringVar()
        ttk.Entry(top, textvariable=self.logo_var, width=36).pack(side=LEFT, padx=4)
        ttk.Button(top, text="…", width=3, command=self._browse_logo).pack(side=LEFT)
        ttk.Label(top, text="Fundo").pack(side=LEFT, padx=(14, 0))
        self.bg_var = StringVar()
        ttk.Entry(top, textvariable=self.bg_var, width=22).pack(side=LEFT, padx=4)
        ttk.Button(top, text="…", width=3, command=self._browse_bg).pack(side=LEFT)

        name_row = ttk.Frame(batch); name_row.pack(fill=X, pady=(6, 0))
        ttk.Label(name_row, text="Nome do arquivo").pack(side=LEFT)
        self.name_var = StringVar(value="{pasta}")
        ttk.Entry(name_row, textvariable=self.name_var, width=44).pack(side=LEFT, padx=4)
        ttk.Label(name_row, text="({pasta} = nome da pasta do hino)",
                  style="Hint.TLabel").pack(side=LEFT)
        self.transition_var = StringVar(value="")
        ttk.Combobox(name_row, state="readonly", width=12, textvariable=self.transition_var,
                     values=["", "fade", "dissolve", "wipeleft", "slideright", "smoothup"]
                     ).pack(side=RIGHT)
        ttk.Label(name_row, text="Transição").pack(side=RIGHT, padx=(8, 2))

        mid = ttk.Frame(batch); mid.pack(fill=X, pady=(8, 0))
        self.preview = ttk.Label(mid, relief="sunken", background="#20242b", width=320)
        self.preview.pack(side=LEFT, fill=Y)
        list_frame = ttk.Frame(mid); list_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(10, 0))
        self.batch_list = Listbox(list_frame, height=5)
        self.batch_list.pack(side=LEFT, fill=BOTH, expand=True)
        buttons = ttk.Frame(list_frame); buttons.pack(side=RIGHT, fill=Y, padx=(6, 0))
        ttk.Button(buttons, text="Adicionar", command=self._add_batch).pack(fill=X)
        ttk.Button(buttons, text="Adicionar pasta", command=self._add_folder).pack(fill=X, pady=4)
        ttk.Button(buttons, text="Remover", command=self._remove_batch).pack(fill=X, pady=4)
        ttk.Button(buttons, text="Limpar", command=self._clear_batch).pack(fill=X)
        ttk.Button(buttons, text="Gerar vídeos", style="Primary.TButton",
                   command=self._generate_batch).pack(fill=X, pady=(12, 0))

        ttk.Label(batch, text="Controle de qualidade (preflight)").pack(anchor="w", pady=(8, 0))
        self.qc_text = Text(batch, height=5, wrap="word", font=("Consolas", 9))
        self.qc_text.pack(fill=X)

        audio_frame = ttk.LabelFrame(outer, text="Áudio (voz, playback, ruído, qualidade)", padding=8)
        audio_frame.pack(fill=X, pady=(10, 0))
        arow = ttk.Frame(audio_frame); arow.pack(fill=X)
        ttk.Label(arow, text="Mídia").pack(side=LEFT)
        self.audio_media = StringVar()
        ttk.Entry(arow, textvariable=self.audio_media, width=70).pack(side=LEFT, padx=4)
        ttk.Button(arow, text="Usar selecionada", command=self._audio_use_selected).pack(side=LEFT)
        ttk.Button(arow, text="Analisar", command=self._audio_analyze).pack(side=LEFT, padx=(8, 0))
        arow2 = ttk.Frame(audio_frame); arow2.pack(fill=X, pady=(6, 0))
        ttk.Button(arow2, text="Extrair instrumental", command=self._audio_instrumental).pack(side=LEFT)
        ttk.Button(arow2, text="Voz +", command=lambda: self._audio_voice(1.6)).pack(side=LEFT, padx=4)
        ttk.Button(arow2, text="Voz −", command=lambda: self._audio_voice(0.6)).pack(side=LEFT)
        ttk.Button(arow2, text="Reduzir ruído", command=self._audio_denoise).pack(side=LEFT, padx=4)
        ttk.Button(arow2, text="Normalizar / melhorar", command=self._audio_enhance).pack(side=LEFT)
        self.audio_log = Text(audio_frame, height=4, wrap="word", font=("Consolas", 9))
        self.audio_log.pack(fill=X, pady=(6, 0))

        scene_frame = ttk.LabelFrame(outer, text="Composição de cena (imagens e texto)", padding=8)
        scene_frame.pack(fill=X, pady=(10, 0))
        tools = ttk.Frame(scene_frame); tools.pack(fill=X)
        ttk.Button(tools, text="Nova cena", command=self._scene_new).pack(side=LEFT)
        ttk.Button(tools, text="Abrir JSON…", command=self._scene_open).pack(side=LEFT, padx=4)
        ttk.Button(tools, text="Salvar JSON…", command=self._scene_save).pack(side=LEFT)
        ttk.Button(tools, text="+ Imagem", command=self._scene_add_image).pack(side=LEFT, padx=(14, 0))
        ttk.Button(tools, text="+ Texto", command=self._scene_add_text).pack(side=LEFT, padx=4)
        ttk.Button(tools, text="Remover", command=self._scene_remove).pack(side=LEFT)
        ttk.Button(tools, text="Desfazer", command=self._scene_undo).pack(side=LEFT, padx=(14, 0))
        ttk.Button(tools, text="Refazer", command=self._scene_redo).pack(side=LEFT, padx=4)
        ttk.Button(tools, text="Efeitos…", command=self._scene_fx_dialog).pack(side=RIGHT, padx=(0, 6))
        ttk.Button(tools, text="Prévia", command=self._scene_preview).pack(side=RIGHT)
        self.snap_var = BooleanVar(value=True)
        Checkbutton(tools, text="Snap", variable=self.snap_var).pack(side=RIGHT, padx=(0, 6))
        self.grid_var = IntVar(value=20)
        ttk.Spinbox(tools, from_=5, to=200, width=5, textvariable=self.grid_var).pack(side=RIGHT, padx=(0, 4))
        ttk.Label(tools, text="Formato").pack(side=RIGHT, padx=(8, 2))
        self.format_var = StringVar(value="1920x1080")
        self.format_box = ttk.Combobox(tools, state="readonly", width=12,
                                       textvariable=self.format_var,
                                       values=list({"1920x1080", "1280x720", "3840x2160",
                                                    "1080x1920", "1080x1080"}))
        self.format_box.pack(side=RIGHT)
        self.format_box.bind("<<ComboboxSelected>>", self._scene_format_changed)

        stage = ttk.Frame(scene_frame); stage.pack(fill=X, pady=(6, 0))
        self.scene_canvas = Canvas(stage, width=480, height=270, background="#1a1d24",
                                   highlightthickness=1, highlightbackground="#555b66")
        self.scene_canvas.pack(side=LEFT)
        self.scene_canvas.bind("<Button-1>", self._scene_press)
        self.scene_canvas.bind("<B1-Motion>", self._scene_drag)
        self.scene_canvas.bind("<ButtonRelease-1>", self._scene_release)
        self.preview_label = ttk.Label(stage, relief="sunken", background="#20242b", width=300)
        self.preview_label.pack(side=LEFT, fill=BOTH, expand=True, padx=(10, 0))

        self.scene_list = Listbox(stage, height=6, width=24)
        self.scene_list.pack(side=LEFT, fill=Y, padx=(10, 0))
        self.scene_list.bind("<<ListboxSelect>>", self._scene_select)

        props = ttk.Frame(scene_frame); props.pack(fill=X, pady=(6, 0))
        self.pvars = {key: StringVar() for key in
                      ("x", "y", "w", "h", "start", "end", "fade_in", "fade_out",
                       "text", "font_size", "color", "file")}
        fields = [("x", "X"), ("y", "Y"), ("w", "Largura"), ("h", "Altura"),
                  ("start", "Entra (s)"), ("end", "Sai (s)"),
                  ("fade_in", "Fade in"), ("fade_out", "Fade out"),
                  ("text", "Texto"), ("font_size", "Fonte"), ("color", "Cor")]
        for index, (key, label) in enumerate(fields):
            row = index // 6; col = (index % 6) * 2
            ttk.Label(props, text=label).grid(row=row, column=col, sticky="w", padx=(0, 4), pady=2)
            width = 30 if key == "text" else 10
            ttk.Entry(props, textvariable=self.pvars[key], width=width).grid(
                row=row, column=col + 1, sticky="ew", pady=2)
        ttk.Button(props, text="Aplicar", command=self._scene_apply_props).grid(
            row=2, column=0, columnspan=12, pady=(6, 0))
        props.columnconfigure(11, weight=1)

        self.timeline = Canvas(scene_frame, height=90, background="#252932",
                               highlightthickness=1, highlightbackground="#555b66")
        self.timeline.pack(fill=X, pady=(8, 0))
        self.timeline.bind("<Button-1>", self._timeline_press)
        self.timeline.bind("<B1-Motion>", self._timeline_drag)
        self.timeline.bind("<ButtonRelease-1>", self._timeline_release)

        self.scene = {"version": 1, "width": 1920, "height": 1080, "layers": []}
        self.scene_history: list[dict] = []
        self.scene_future: list[dict] = []
        self.selected_layer: int | None = None
        self.drag_mode: str | None = None
        self._scene_load_autosave()
        self._scene_redraw()

        self._apply_profile()

    def _apply_profile(self) -> None:
        """Apply the selected profile's timing settings to the controls."""
        profile = self.profiles.get("profiles", {}).get(self.active_profile)
        settings = (profile or {}).get("settings", {})
        alignment = settings.get("alignment", {})
        if alignment:
            self.max_chars.set(str(alignment.get("max_chars", 30)))
            self.max_duration.set(str(int(alignment.get("max_duration_ms", 10000) / 1000)))
            hold = int(alignment.get("end_pad_ms", 500))
            holds = [300, 500, 1000, 1500]
            index = holds.index(hold) if hold in holds else 1
            self.end_hold.current(index)
            self.lead_ms = int(alignment.get("lead_ms", 600))
            if self.mode_box.cget("values"):
                self.mode_box.current(1 if alignment.get("mode") == "manual" else 0)
        self.profile_info.configure(text=(profile or {}).get("description", ""))
        extras = (profile or {}).get("extras", {})
        if hasattr(self, "intro_seconds"):
            self.intro_seconds.set(str(int(extras.get("intro_seconds", 0))))
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
        bg_name = self.bg_var.get().strip() if hasattr(self, "bg_var") else ""
        bg_path = Path(bg_name) if bg_name else None
        if not bg_path or not bg_path.is_file():
            first = self.batch_list.get(0) if hasattr(self, "batch_list") and self.batch_list.size() else ""
            if first:
                bg_path = next((p for p in Path(first).parent.glob("FUNDO*") if p.suffix.lower() in {".png", ".jpg", ".jpeg"}), None)
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
            logo = self.logo_var.get().strip() if hasattr(self, "logo_var") else ""
            if logo and Path(logo).is_file():
                mark = Image.open(logo).convert("RGBA")
                mark.thumbnail((80, 80))
                base.paste(mark, (size[0] - 88, 8), mark)
            image = ImageTk.PhotoImage(base)
            self.preview.configure(image=image)
            self.preview.image = image
        except Exception:
            pass

    def _profile_changed(self, _event=None) -> None:
        name = self.profile_box.get()
        self.active_profile = name
        PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFERENCES_PATH.write_text(
            json.dumps({**self.preferences, "profile": name}), encoding="utf-8"
        )
        self._apply_profile()

    def _browse_logo(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Imagem", "*.png *.jpg *.jpeg *.webp")])
        if path:
            self.logo_var.set(path)
            self._update_preview()

    def _browse_bg(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Imagem", "*.png *.jpg *.jpeg *.webp")])
        if path:
            self.bg_var.set(path)
            self._update_preview()

    def _add_batch(self) -> None:
        files = filedialog.askopenfilenames(filetypes=[
            ("Mídia", "*.mp4 *.mov *.mkv *.avi *.mpeg *.mpg *.mp3 *.wav *.m4a *.aac *.flac *.ogg")])
        for item in files:
            if item not in self.batch_list.get(0, END):
                self.batch_list.insert(END, item)
        self._update_preview()

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Pasta com hinos")
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

    def _scene_name_for(self, folder: Path, count: int) -> str:
        template = self.name_var.get().strip() or "{pasta}"
        name = template.replace("{pasta}", folder.name)
        if count == 1 and "{pasta}" not in template:
            name = template
        invalid = '<>:"/\\|?*'
        return "".join(ch for ch in name if ch not in invalid).strip() or folder.name

    def _write_history(self, folder: Path, outputs: list[str], warnings: list[str]) -> None:
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
            messagebox.showinfo(APP_NAME, self.tr("need_media"))
            return
        self.qc_text.configure(state="normal")
        self.qc_text.delete("1.0", END)
        self.qc_text.configure(state="disabled")
        self.cancel_event.clear()
        self.worker = threading.Thread(target=self._batch_worker, args=(items,), daemon=True)
        self.worker.start()

    def _batch_worker(self, items: list[str]) -> None:
        python = Path(sys.executable)
        if python.name.casefold() == "pythonw.exe":
            console = python.with_name("python.exe")
            if console.is_file():
                python = console
        env = os.environ.copy()
        env["PYTHONPATH"] = str(VILM_APP_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        for item in items:
            if self.cancel_event.is_set():
                break
            media = Path(item)
            folder = media.parent
            lyrics = folder / "letra.txt"
            if not lyrics.is_file():
                self.events.put(("batch_status", f"ERRO: sem letra.txt em {folder.name}"))
                continue
            images = [p for p in folder.iterdir()
                      if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
            title = next((p for p in images if "FUNDO" not in p.name.upper()), None)
            bg = Path(self.bg_var.get()) if self.bg_var.get().strip() else next(
                (p for p in images if "FUNDO" in p.name.upper()), None)
            playback = next((p for p in folder.iterdir()
                             if p.is_file() and "PLAYBACK" in p.name.upper()), None)
            logo = Path(self.logo_var.get()) if self.logo_var.get().strip() else None
            saida = folder / "saida"
            saida.mkdir(parents=True, exist_ok=True)
            output = saida / (self._scene_name_for(folder, len(items)) + ".mp4")
            self.events.put(("batch_status", f"Processando {folder.name}…"))
            work = folder / "trabalho" / "cli"
            cmd = [str(python), "-m", "lyrics_aligner", "make", str(media), str(lyrics),
                   "-o", str(output), "--profile", self.active_profile, "--work", str(work)]
            if title: cmd += ["--title-image", str(title)]
            if bg and bg.is_file(): cmd += ["--background", str(bg)]
            if logo and logo.is_file(): cmd += ["--logo", str(logo)]
            if playback: cmd += ["--playback", str(playback)]
            if self.transition_var.get():
                cmd += ["--transition", self.transition_var.get(),
                        "--transition-duration", "0.4"]
            if self.scene.get("layers"):
                from .scene import save_scene
                scene_file = work / "scene.json"
                save_scene(scene_file, self.scene)
                cmd += ["--scene", str(scene_file)]
            try:
                result = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace")
                if result.returncode != 0:
                    self.events.put(("batch_status", f"FALHOU {folder.name}: "
                                     + (result.stderr or result.stdout)[-300:]))
                    continue
                self.events.put(("batch_status", f"OK {folder.name}"))
                report_path = work / "qc.json"
                report = json.loads(report_path.read_text(encoding="utf-8")) \
                    if report_path.is_file() else {}
                warnings = list(report.get("warnings", []))
                for warning in warnings:
                    self.events.put(("qc", f"{folder.name}: {warning}"))
                self._write_history(folder, report.get("outputs", [str(output)]), warnings)
            except Exception as exc:
                self.events.put(("batch_status", f"FALHOU {folder.name}: {exc}"))
        self.events.put(("batch_done", None))

    # ---- editor de cena -------------------------------------------------
    def _scene_push(self) -> None:
        self.scene_history.append(copy.deepcopy(self.scene))
        self.scene_future.clear()

    def _snap(self, value: float) -> float:
        if not self.snap_var.get():
            return round(value, 1)
        grid = max(1, int(self.grid_var.get()))
        return float(round(value / grid) * grid)

    def _scene_autosave(self) -> None:
        try:
            AUTOSAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            AUTOSAVE_PATH.write_text(json.dumps(self.scene, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
        except OSError:
            pass

    def _scene_load_autosave(self) -> None:
        if not AUTOSAVE_PATH.is_file():
            return
        try:
            scene = json.loads(AUTOSAVE_PATH.read_text(encoding="utf-8"))
            if isinstance(scene.get("layers"), list):
                self.scene = scene
                self._append_qc("Cena restaurada do autosave.")
        except (OSError, ValueError):
            pass

    def _scene_redraw(self) -> None:
        c = self.scene_canvas
        c.delete("all")
        scale = 480 / 1920
        c.create_rectangle(0, 0, 480, 270, fill="#2b303b", outline="")
        if self.snap_var.get():
            grid = max(1, int(self.grid_var.get())) * scale
            gx = grid
            while gx < 480:
                c.create_line(gx, 0, gx, 270, fill="#333944")
                gx += grid
            gy = grid
            while gy < 270:
                c.create_line(0, gy, 480, gy, fill="#333944")
                gy += grid
        from .scene import safe_bounds
        sw = int(self.scene.get("width", 1920)); sh = int(self.scene.get("height", 1080))
        x1, y1, x2, y2 = safe_bounds(self.scene)
        c.create_rectangle(x1 / sw * 480, y1 / sh * 270, x2 / sw * 480, y2 / sh * 270,
                           outline="#d95f5f", dash=(4, 3))
        scale_x = 480 / sw; scale_y = 270 / sh
        for index, layer in enumerate(self.scene.get("layers", [])):
            x = float(layer.get("x", 0)) * scale_x
            y = float(layer.get("y", 0)) * scale_y
            w = max(6, float(layer.get("w", sw)) * scale_x)
            h = max(6, float(layer.get("h", sh)) * scale_y)
            color = "#4a90d9" if layer.get("type") == "image" else "#d9a441"
            c.create_rectangle(x, y, x + w, y + h, outline=color, width=2, tags=f"layer{index}")
            c.create_rectangle(x + w - 8, y + h - 8, x + w, y + h,
                               fill=color, outline="", tags=f"handle{index}")
            if layer.get("type") == "text":
                c.create_text(x + w / 2, y + h / 2, text=str(layer.get("text", ""))[:22],
                              fill="white", tags=f"layer{index}")
        self.scene_list.delete(0, END)
        for index, layer in enumerate(self.scene.get("layers", [])):
            label = f"{index + 1}. {layer.get('type')}  {layer.get('text') or Path(str(layer.get('file') or '')).name}"
            self.scene_list.insert(END, label[:34])
        self._draw_timeline()
        self._scene_autosave()

    def _draw_timeline(self) -> None:
        c = self.timeline
        c.delete("all")
        width = max(400, c.winfo_width())
        total = max(30.0, max((float(l.get("end") or 0) for l in self.scene.get("layers", [])), default=0) + 5)
        self.timeline_total = total
        self.timeline_px = width / total
        for second in range(0, int(total) + 1, 5):
            x = second * self.timeline_px
            c.create_line(x, 0, x, 90, fill="#3a404b")
            c.create_text(x + 4, 4, text=f"{second}s", anchor="nw", fill="#9aa4b2")
        for index, layer in enumerate(self.scene.get("layers", [])):
            start = float(layer.get("start", 0))
            end = float(layer.get("end") or (start + 5))
            color = "#4a90d9" if layer.get("type") == "image" else "#d9a441"
            y = 22 + index * 13
            c.create_rectangle(start * self.timeline_px, y, end * self.timeline_px, y + 11,
                               fill=color, outline="", tags=f"bar{index}")

    def _scene_format_changed(self, _event=None) -> None:
        from .scene import preset_size
        width, height, margin = preset_size(self.format_var.get())
        self.scene["width"] = width
        self.scene["height"] = height
        self.scene["preset"] = self.format_var.get()
        self.scene["safe_margin"] = margin
        self._scene_push()
        self._scene_redraw()

    def _scene_select(self, _event=None) -> None:
        selection = self.scene_list.curselection()
        if not selection:
            return
        self.selected_layer = int(selection[0])
        layer = self.scene["layers"][self.selected_layer]
        for key in self.pvars:
            self.pvars[key].set(str(layer.get(key, "")))
        self._scene_redraw()

    def _scene_apply_props(self) -> None:
        if self.selected_layer is None:
            return
        self._scene_push()
        layer = self.scene["layers"][self.selected_layer]
        for key in ("x", "y", "w", "h", "start", "fade_in", "fade_out", "font_size"):
            try:
                value = float(self.pvars[key].get())
                if key in ("x", "y", "w", "h"):
                    value = self._snap(value)
                layer[key] = value
            except ValueError:
                pass
        try:
            layer["end"] = float(self.pvars["end"].get())
        except ValueError:
            layer["end"] = None
        layer["text"] = self.pvars["text"].get()
        layer["color"] = self.pvars["color"].get()
        self._scene_redraw()

    def _scene_press(self, event) -> None:
        item = self.scene_canvas.find_withtag("current")
        if not item:
            self.selected_layer = None
            self._scene_redraw()
            return
        tags = self.scene_canvas.gettags(item[0])
        tag = next((t for t in tags if t.startswith("layer") or t.startswith("handle")), None)
        if not tag:
            return
        self.selected_layer = int(tag.replace("layer", "").replace("handle", ""))
        self.drag_mode = "resize" if tag.startswith("handle") else "move"
        self.drag_origin = (event.x, event.y)
        self._scene_push()
        layer = self.scene["layers"][self.selected_layer]
        self.drag_values = {k: float(layer.get(k, 0)) for k in ("x", "y", "w", "h")}
        self.scene_list.selection_clear(0, END)
        self.scene_list.select_set(self.selected_layer)
        for key in self.pvars:
            self.pvars[key].set(str(layer.get(key, "")))

    def _scene_drag(self, event) -> None:
        if self.selected_layer is None or not hasattr(self, "drag_values"):
            return
        scale_x = 480 / int(self.scene.get("width", 1920))
        scale_y = 270 / int(self.scene.get("height", 1080))
        dx = (event.x - self.drag_origin[0]) / scale_x
        dy = (event.y - self.drag_origin[1]) / scale_y
        layer = self.scene["layers"][self.selected_layer]
        origin = self.drag_values
        if self.drag_mode == "move":
            layer["x"] = max(0, self._snap(origin["x"] + dx))
            layer["y"] = max(0, self._snap(origin["y"] + dy))
        else:
            layer["w"] = max(40, self._snap(origin["w"] + dx))
            layer["h"] = max(40, self._snap(origin["h"] + dy))
        self._scene_redraw()

    def _scene_release(self, _event=None) -> None:
        self.drag_mode = None
        if hasattr(self, "drag_values"):
            del self.drag_values

    def _timeline_press(self, event) -> None:
        item = self.timeline.find_withtag("current")
        if not item:
            return
        tag = next((t for t in self.timeline.gettags(item[0]) if t.startswith("bar")), None)
        if not tag:
            return
        self.selected_layer = int(tag.replace("bar", ""))
        self._scene_push()
        layer = self.scene["layers"][self.selected_layer]
        start = float(layer.get("start", 0)); end = float(layer.get("end") or (start + 5))
        self.timeline_origin = (event.x, start, end, end - 5 <= event.x / self.timeline_px <= end + 5)

    def _timeline_drag(self, event) -> None:
        if not hasattr(self, "timeline_origin") or self.selected_layer is None:
            return
        x0, start, end, resize = self.timeline_origin
        delta = (event.x - x0) / self.timeline_px
        layer = self.scene["layers"][self.selected_layer]
        if resize:
            layer["end"] = max(0.5, end + delta)
        else:
            layer["start"] = max(0.0, start + delta)
            layer["end"] = max(layer["start"] + 0.5, end + delta)
        self._draw_timeline()
        for key in ("start", "end"):
            self.pvars[key].set(str(layer.get(key, "")))

    def _timeline_release(self, _event=None) -> None:
        if hasattr(self, "timeline_origin"):
            del self.timeline_origin

    def _scene_new(self) -> None:
        self._scene_push()
        self.scene = {"version": 1, "width": 1920, "height": 1080, "layers": []}
        self.selected_layer = None
        self._scene_redraw()

    def _scene_open(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Scene JSON", "*.json")])
        if not path:
            return
        try:
            from .scene import load_scene, validate_scene
            self._scene_push()
            self.scene = load_scene(path)
            for warning in validate_scene(self.scene):
                self._append_qc("CENA: " + warning)
            self._scene_redraw()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def _scene_save(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("Scene JSON", "*.json")],
                                            initialfile="cena.json")
        if path:
            from .scene import save_scene
            save_scene(path, self.scene)

    def _scene_add_image(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Imagem", "*.png *.jpg *.jpeg *.webp")])
        if not path:
            return
        self._scene_push()
        self.scene["layers"].append({"type": "image", "file": path, "x": 1380, "y": 80,
                                     "w": 460, "h": 460, "start": 0, "end": 4,
                                     "fade_in": 0.3, "fade_out": 0.3})
        self.selected_layer = len(self.scene["layers"]) - 1
        self._scene_redraw()

    def _scene_add_text(self) -> None:
        self._scene_push()
        self.scene["layers"].append({"type": "text", "text": "Novo texto", "x": 960, "y": 800,
                                     "font_size": 64, "color": "#000000",
                                     "start": 0, "end": 5, "fade": 0.3})
        self.selected_layer = len(self.scene["layers"]) - 1
        self._scene_redraw()

    def _scene_remove(self) -> None:
        if self.selected_layer is None:
            return
        self._scene_push()
        self.scene["layers"].pop(self.selected_layer)
        self.selected_layer = None
        self._scene_redraw()

    def _scene_undo(self) -> None:
        if not self.scene_history:
            return
        self.scene_future.append(copy.deepcopy(self.scene))
        self.scene = self.scene_history.pop()
        self._scene_redraw()

    def _scene_redo(self) -> None:
        if not self.scene_future:
            return
        self.scene_history.append(copy.deepcopy(self.scene))
        self.scene = self.scene_future.pop()
        self._scene_redraw()

    def _scene_fx_dialog(self) -> None:
        if self.selected_layer is None or self.selected_layer >= len(self.scene.get("layers", [])):
            messagebox.showinfo(APP_NAME, "Selecione uma camada de imagem.")
            return
        layer = self.scene["layers"][self.selected_layer]
        fx = dict(layer.get("fx") or {})
        top = Toplevel(self.root)
        top.title("Efeitos da imagem")
        top.geometry("430x640")
        row = 0
        ttk.Label(top, text="Remover fundo").grid(row=row, column=0, sticky="w", padx=8, pady=6)
        method = StringVar(value=str(fx.get("remove_background", "none")))
        ttk.Combobox(top, state="readonly", textvariable=method,
                     values=["none", "color", "luma", "saturation", "flood"]).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(top, text="Cor do fundo").grid(row=row, column=0, sticky="w", padx=8)
        color = StringVar(value=str(fx.get("key_color", "#FFFFFF")))
        ttk.Entry(top, textvariable=color, width=14).grid(row=row, column=1, sticky="w")
        row += 1
        scales: dict[str, DoubleVar] = {}
        for key, label, low, high, default in (
            ("tolerance", "Tolerância", 0.02, 0.9, 0.12),
            ("feather", "Penumbra", 0, 8, 1.5),
            ("brightness", "Brilho", 0, 3, 1),
            ("contrast", "Contraste", 0, 3, 1),
            ("saturation", "Saturação", 0, 3, 1),
            ("sharpness", "Nitidez", 0, 3, 1),
            ("blur", "Desfoque", 0, 10, 0),
            ("opacity", "Opacidade", 0, 1, 1),
            ("rotate", "Rotação", -180, 180, 0),
        ):
            ttk.Label(top, text=label).grid(row=row, column=0, sticky="w", padx=8)
            var = DoubleVar(value=float(fx.get(key, default)))
            scales[key] = var
            Scale(top, from_=low, to=high, resolution=(high - low) / 100,
                  orient="horizontal", variable=var, length=230).grid(row=row, column=1)
            row += 1
        checks: dict[str, BooleanVar] = {}
        for key, label in (("grayscale", "Preto e branco"), ("sepia", "Sépia"),
                           ("flip_h", "Espelhar"), ("flip_v", "Inverter"),
                           ("shadow", "Sombra")):
            var = BooleanVar(value=bool(fx.get(key, False)))
            checks[key] = var
            Checkbutton(top, text=label, variable=var).grid(row=row, column=1, sticky="w")
            row += 1

        def apply() -> None:
            self._scene_push()
            new_fx = {"remove_background": method.get(), "key_color": color.get()}
            for key, var in scales.items():
                new_fx[key] = round(float(var.get()), 3)
            for key, var in checks.items():
                new_fx[key] = bool(var.get())
            layer["fx"] = new_fx
            self._scene_redraw()
            self._scene_preview()
            top.destroy()

        ttk.Button(top, text="Aplicar", style="Primary.TButton", command=apply).grid(
            row=row, column=0, columnspan=2, pady=12)

    def _scene_preview(self) -> None:
        if not self.scene.get("layers"):
            return
        try:
            from .scene import render_preview
            background = Path(self.bg_var.get()) if self.bg_var.get().strip() else None
            if not background or not background.is_file():
                first = self.batch_list.get(0) if self.batch_list.size() else ""
                if first:
                    background = next((p for p in Path(first).parent.glob("FUNDO*")), None)
            layer = (self.scene["layers"][self.selected_layer]
                     if self.selected_layer is not None else self.scene["layers"][0])
            time = float(layer.get("start", 0)) + 0.5
            out = Path(tempfile.gettempdir()) / "vilm-scene-preview.png"
            render_preview(scene=self.scene, background=background, time=time, output=out)
            image = ImageTk.PhotoImage(Image.open(out))
            self.preview_label.configure(image=image)
            self.preview_label.image = image
        except Exception as exc:
            self._append_qc("PREVIA: " + str(exc))

    def _apply_language(self) -> None:
        self.profile_label.configure(text=self.tr("profile"))
        self.profile_info.configure(text=self.tr("profile_help") if not self.active_profile else (self.profiles.get("profiles", {}).get(self.active_profile, {}).get("description") or self.tr("profile_help")))
        self.media_label.configure(text=self.tr("media"))
        self.media_hint.configure(text=self.tr("drop"))
        self.browse_button.configure(text=self.tr("browse"))
        if self.media_path is None:
            self.media_name.configure(text=self.tr("no_media"))
        self.range_label.configure(text=self.tr("range"))
        self.full_radio.configure(text=self.tr("full"))
        self.selection_radio.configure(text=self.tr("selection"))
        self.range_hint.configure(text=self.tr("range_help"))
        self.lyrics_label.configure(text=self.tr("lyrics"))
        self.lyrics_hint.configure(text=self.tr("lyrics_hint"))
        self.load_button.configure(text=self.tr("load"))
        self.mode_label.configure(text=self.tr("mode"))
        current_mode = self.mode_box.current()
        self.mode_box.configure(values=[self.tr("auto"), self.tr("manual")])
        self.mode_box.current(max(0, current_mode))
        self.device_label.configure(text=self.tr("device"))
        self.max_chars_label.configure(text=self.tr("max_chars"))
        self.max_duration_label.configure(text=self.tr("max_duration"))
        self.end_hold_label.configure(text=self.tr("end_hold"))
        self.guide.configure(text=self.tr("guide"))
        self.generate_button.configure(text=self.tr("generate"))
        self.cancel_button.configure(text=self.tr("cancel"))
        self.open_folder_button.configure(text=self.tr("open_folder"))
        if not self.worker or not self.worker.is_alive():
            self.status.configure(text=self.tr("ready"))

    def _language_changed(self, _event=None) -> None:
        self.language = "en" if self.language_box.current() == 0 else "ko"
        PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFERENCES_PATH.write_text(json.dumps({"ui_language": self.language}), encoding="utf-8")
        self._apply_language()

    def _browse_media(self) -> None:
        selected = filedialog.askopenfilename(
            title=self.tr("browse"),
            filetypes=[
                ("Media", "*.mp4 *.mov *.mkv *.avi *.m4a *.aac *.mp3 *.wav *.flac *.ogg *.opus *.webm"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            self._load_media(Path(selected))

    def _load_media(self, path: Path) -> None:
        self.media_path = path
        self.media_name.configure(text=path.name)
        self.status.configure(text=self.tr("loading"))
        self.peaks = []
        self._draw_waveform()
        threading.Thread(target=self._waveform_worker, args=(path,), daemon=True).start()

    def _waveform_worker(self, path: Path) -> None:
        try:
            duration, peaks = waveform_peaks(path, bins=900)
            self.events.put(("waveform", (path, duration, peaks)))
        except Exception as exc:
            self.events.put(("media_error", (path, str(exc))))

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
                canvas.create_line(x, middle - amplitude, x, middle + amplitude, fill="#79d8a5")
        if self.duration > 0 and self.range_mode.get() == "selection":
            left = self.range_start / self.duration * width
            right = self.range_end / self.duration * width
            canvas.create_rectangle(0, 0, left, height, fill="#101318", stipple="gray50", outline="")
            canvas.create_rectangle(right, 0, width, height, fill="#101318", stipple="gray50", outline="")
            canvas.create_line(left, 0, left, height, fill="#ffd166", width=2)
            canvas.create_line(right, 0, right, height, fill="#ffd166", width=2)

    def _update_range_readout(self) -> None:
        end = self.duration if self.range_mode.get() == "full" else self.range_end
        start = 0.0 if self.range_mode.get() == "full" else self.range_start
        self.range_readout.configure(text=f"{_format_time(start)} — {_format_time(end)}")

    def _load_lyrics(self) -> None:
        selected = filedialog.askopenfilename(title=self.tr("load"), filetypes=[("Text", "*.txt"), ("All files", "*.*")])
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
        if self.backend in {"cuda", "mps"} and self.device_box.current() == 0:
            return self.backend
        return "cpu"

    def _generate(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if self.media_path is None or not self.media_path.is_file():
            messagebox.showinfo(APP_NAME, self.tr("need_media"))
            return
        lyrics = self.lyrics.get("1.0", END).strip()
        if not lyrics:
            messagebox.showinfo(APP_NAME, self.tr("need_lyrics"))
            return
        selected = self.range_mode.get() == "selection"
        if selected and self.range_end - self.range_start < 1.0:
            messagebox.showinfo(APP_NAME, self.tr("bad_range"))
            return
        output_dir = Path.home() / "Documents" / APP_NAME
        output_dir.mkdir(parents=True, exist_ok=True)
        default_name = f"{self.media_path.stem}-{datetime.now():%Y%m%d-%H%M%S}.srt"
        output = filedialog.asksaveasfilename(
            title=self.tr("save_title"),
            initialdir=str(output_dir),
            initialfile=default_name,
            defaultextension=".srt",
            filetypes=[("SubRip subtitles", "*.srt")],
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
        self.status.configure(text=self.tr("processing"))
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

    def _alignment_worker(self, lyrics_path: Path, output: Path, selected: bool, options: dict) -> None:
        assert self.media_path is not None
        python = Path(sys.executable)
        if python.name.casefold() == "pythonw.exe":
            console_python = python.with_name("python.exe")
            if console_python.is_file():
                python = console_python
        command = [
            str(python), "-m", "lyrics_aligner", "align",
            str(self.media_path), str(lyrics_path), "-o", str(output),
            "--max-chars", str(options["max_chars"]),
            "--lead-ms", str(int(options["lead_ms"])),
            "--max-duration-ms", str(int(options["max_duration"]) * 1000),
            "--end-pad-ms", str([300, 500, 1000, 1500][int(options["end_hold_index"])]),
            "--min-gap-ms", "80",
            "--timeline-anchor",
        ]
        if options["automatic"]:
            command.append("--auto-segment")
        if selected:
            command.extend([
                "--range-start", f"{float(options['range_start']):.6f}",
                "--range-end", f"{float(options['range_end']):.6f}",
                "--partial-range",
            ])
        env = os.environ.copy()
        env["PYTHONPATH"] = str(VILM_APP_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        env["LYRICS_ALIGNER_DEVICE"] = str(options["device"])
        try:
            result = run_streaming_process(
                command,
                env=env,
                cancel_requested=self.cancel_event.is_set,
                on_output=lambda line: self.events.put(("log", line)),
            )
            if result.returncode != 0:
                raise RuntimeError(result.output.strip() or "Alignment failed")
            self.events.put(("complete", output))
        except JobCancelled:
            output.unlink(missing_ok=True)
            self.events.put(("cancelled", None))
        except Exception as exc:
            output.unlink(missing_ok=True)
            self.events.put(("failed", str(exc)))
        finally:
            lyrics_path.unlink(missing_ok=True)

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.status.configure(text=f"{self.tr('cancelled')}…")

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "waveform":
                    path, duration, peaks = payload
                    if self.media_path == path:
                        self.duration = float(duration)
                        self.peaks = list(peaks)
                        self.range_start = 0.0
                        self.range_end = self.duration
                        self.range_mode.set("full")
                        self._update_range_readout()
                        self._draw_waveform()
                        self.status.configure(text=self.tr("ready"))
                elif kind == "media_error":
                    path, error = payload
                    if self.media_path == path:
                        self.media_path = None
                        self.media_name.configure(text=self.tr("no_media"))
                        self.status.configure(text=self.tr("ready"))
                        messagebox.showerror(self.tr("file_error"), error)
                elif kind == "log":
                    self._append_log(str(payload))
                elif kind == "complete":
                    self.last_output = Path(payload)
                    self.status.configure(text=f"{self.tr('done')}  {self.last_output}")
                    self._finish_job(show_folder=True)
                elif kind == "cancelled":
                    self.status.configure(text=self.tr("cancelled"))
                    self._finish_job()
                elif kind == "failed":
                    self.status.configure(text=self.tr("failed"))
                    self._finish_job()
                    messagebox.showerror(self.tr("failed"), str(payload))
                elif kind == "batch_status":
                    self.status.configure(text=str(payload))
                    self._append_log(str(payload))
                elif kind == "qc":
                    self._append_qc(str(payload))
                elif kind == "batch_done":
                    self.status.configure(text="Lote concluído.")
                    self._append_qc("— fim do lote —")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

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
        if os.name == "nt":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])


def main() -> int:
    root = Tk()
    DesktopApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
