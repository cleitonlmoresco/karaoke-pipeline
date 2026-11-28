"""Casca tkinter nativa do Vilm Lyrics Aligner (Fase 3).

Sete abas planas, uma unica cabecalho (Projeto / Perfil / Idioma) e uma unica
fachada compartilhada.  As abas Projetos, Legendas, Configuracoes e Assistente
sao construidas na abertura; Producao, Cena e Audio entram sob demanda, pela
identidade do frame selecionado no Notebook.

O idioma tem um unico funil (:meth:`VilmTkApp.apply_language`): validar,
``set_language``, uma escrita de ``ui_language``, ``retranslate(root)`` e
status.  Nenhuma aba escreve a preferencia de idioma.

Uso::

    python -m lyrics_aligner.tkinter_app
"""

from __future__ import annotations

# pythonw.exe has sys.stdout/sys.stderr = None; backends call isatty() on them.
from .gui_streams import ensure_gui_streams

ensure_gui_streams()

import copy
import os
import queue
import threading
import tkinter as tk
import weakref
from pathlib import Path
from tkinter import ttk
from typing import Any, Mapping

from . import i18n as i18n_module
from . import profiles_api
from . import settings as settings_module
from .i18n import available_languages, language_label, mark, t
from .platform_paths import application_data_root
from .project_context import ProjectContext
from .ui_scale import UiScale

APP_VERSION = "1.3.0"

# Ordem publica das 7 abas.  Todas as chaves existem em pt/en/ko (mesmo numero
# de chaves por idioma; a contagem exata e mantida por i18n.py).
TAB_SPECS = (
    ("tab_projects", "tab_projects"),
    ("tab_production", "tab_production"),
    ("tab_cue_editor", "tab_cue_editor"),
    ("tab_scene", "tab_scene"),
    ("tab_audio", "tab_audio"),
    ("tab_settings", "tab_settings"),
    ("tab_assistant", "tab_assistant"),
)

_TYPING_CLASSES = {
    "Text", "Entry", "TEntry", "Spinbox", "TSpinbox", "Combobox", "TCombobox",
}


class _SettingsFacade(dict):
    """Preferencias em memoria: dict para leitura e callable para a fachada."""

    def __call__(self) -> dict:
        return dict(self)


class VilmTkApp:
    def __init__(self, root: tk.Tk, settings_path=None, profiles_path=None) -> None:
        self.root = root
        # Destinos isolados (usados pelos testes); por padrao os arquivos reais.
        self.settings_path = (
            settings_path
            or os.environ.get("LYRICS_ALIGNER_SETTINGS_PATH")
            or None
        )
        self.profiles_path = (
            profiles_path
            or os.environ.get("LYRICS_ALIGNER_PROFILES_PATH")
            or None
        )
        self.preferences = settings_module.load_settings(self.settings_path)
        self.config = self.preferences
        self.settings = _SettingsFacade(self.preferences)
        self.backend = str(self.preferences.get("backend") or "cpu").casefold()
        self.profiles = profiles_api.load_profiles(self.profiles_path)
        self.active_profile = str(
            self.preferences.get("profile") or self.profiles.get("active") or ""
        )

        language = str(self.preferences.get("ui_language", "pt") or "pt")
        self.ui_language = language if language in available_languages() else "pt"
        i18n_module.set_language(self.ui_language)

        # Contexto de projeto e unico e somente leitura na descoberta.
        self.project_context = ProjectContext(settings_path=self.settings_path)

        # Abas: eager (4) e lazy (3).  Nenhuma e construida por indice fixo.
        self.projects_tab = None
        self.production_tab = None
        self.cue_editor_tab = None
        self.scene_tab = None
        self.audio_tab = None
        self.settings_tab = None
        self.assistant_tab = None
        self.pipeline_tools = None
        self._production_events_bound = False
        self._audio_events_bound = False

        # Fila de eventos e registradores da fachada.
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._handlers: dict[str, list] = {}

        # Texto de letra compartilhado, sem widget proprio (assistente/ferramentas).
        self._lyrics = ""
        self._project_items: dict[str, object] = {}

        # Escala visual capturada ANTES de geometry/tema: os tamanhos BASE das
        # named fonts sao guardados aqui, sem acumular sobre valores escalados.
        # Nenhuma chave nova em settings.json e nenhum controle manual de zoom.
        self.ui_scale = UiScale(root)
        root.ui_scale = self.ui_scale
        self.ui_scale.on_apply(self._apply_scaled_theme)

        self.root.title(t("app_title"))
        mark(self.root, "app_title", kind="title")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.geometry("1100x720")
        self.root.minsize(900, 600)
        try:
            cfg = settings_module.load_settings(self.settings_path)
            w = cfg.get("window_width", 1100)
            h = cfg.get("window_height", 720)
            self.root.geometry(f"{w}x{h}")
            self._apply_theme(cfg)
        except Exception:
            pass

        # Ordem de packing: cabecalho fixo, notebook, status na base.  O log e
        # eager e fica oculto para que log() funcione antes de qualquer aba.
        self._build_menu()
        self._build_statusbar()
        self._build_log()
        self._build_header()
        self._build_notebook()
        self._build_eager_tabs()
        self._setup_shortcuts()
        self._register_language_hook()
        # A janela ja existe: so agora inscreve o debounce de <Configure>.
        self.ui_scale.bind_root()
        self.root.after(100, self._poll_events)

    # ---- funil unico de idioma ------------------------------------------
    def _register_language_hook(self) -> None:
        """Atualiza o seletor do cabecalho dentro de ``retranslate``.

        O hook nao altera o idioma global e nao grava nada; ele apenas espelha
        o codigo ja aplicado no Combobox (rotulos de idioma nao são chaves i18n).
        """
        app_ref = weakref.ref(self)

        def hook(lang: str) -> None:
            app = app_ref()
            if app is None:
                return
            try:
                box = getattr(app, "language_box", None)
                if box is not None and box.winfo_exists():
                    box.set(language_label(lang))
            except Exception:
                pass

        i18n_module.on_language_changed(hook)

    def apply_language(self, code: str) -> bool:
        """Aplica ``code`` com exatamente uma escrita de ``ui_language``."""
        codes = available_languages()
        if code not in codes:
            self.set_status(t("error_msg", exc="idioma invalido"))
            return False
        i18n_module.set_language(code)
        saved = True
        failure = ""
        try:
            updated = settings_module.update_settings(
                {"ui_language": code}, self.settings_path
            )
            if isinstance(updated, dict):
                self.preferences.update(updated)
                self.settings.update(updated)
        except (OSError, ValueError) as exc:
            # Nao informar sucesso quando a gravacao da preferencia falhou.
            saved = False
            failure = t("error_msg", exc=exc)
            self.log(failure)
        self.ui_language = code
        i18n_module.retranslate(self.root)
        if saved:
            self.set_status(t("language_saved", lang=language_label(code)))
        else:
            self.set_status(failure or t("error_msg", exc="falha ao salvar"))
        return saved

    # ---- menu -----------------------------------------------------------
    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label=t("menu_exit"), command=self.root.destroy)
        mark(file_menu, "menu_exit", kind="menu", index=file_menu.index("end"))
        menubar.add_cascade(label=t("menu_file"), menu=file_menu)
        mark(menubar, "menu_file", kind="menu", index=menubar.index("end"))

        config_menu = tk.Menu(menubar, tearoff=0)
        config_menu.add_command(label=t("config_window_title"), command=self._open_config)
        mark(config_menu, "config_window_title", kind="menu", index=config_menu.index("end"))
        config_menu.add_command(label=t("menu_advanced"), command=self._open_advanced_config)
        mark(config_menu, "menu_advanced", kind="menu", index=config_menu.index("end"))
        # O log e sempre criado; o menu apenas mostra/oculta o painel ja existente.
        self._log_var = tk.BooleanVar(value=True)
        config_menu.add_checkbutton(
            label=t("log_section"), variable=self._log_var, command=self._toggle_log
        )
        mark(config_menu, "log_section", kind="menu", index=config_menu.index("end"))
        menubar.add_cascade(label=t("menu_config"), menu=config_menu)
        mark(menubar, "menu_config", kind="menu", index=menubar.index("end"))

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label=t("menu_about"), command=self._show_about)
        mark(help_menu, "menu_about", kind="menu", index=help_menu.index("end"))
        menubar.add_cascade(label=t("menu_help"), menu=help_menu)
        mark(menubar, "menu_help", kind="menu", index=menubar.index("end"))
        self.root.config(menu=menubar)

    def _open_config(self) -> None:
        from .tk_config_window import ConfigWindow

        ConfigWindow(self.root, self)

    def _open_advanced_config(self) -> None:
        from .tk_advanced_settings import AdvancedSettingsWindow

        AdvancedSettingsWindow(self.root, self)

    def _show_about(self) -> None:
        import tkinter.messagebox as mb

        mb.showinfo(t("menu_about"), t("about_text"))

    # ---- status / log ---------------------------------------------------
    def _build_statusbar(self) -> None:
        self.status_var = tk.StringVar(value=t("status_ready"))
        mark(self.status_var, "status_ready", kind="variable")
        self.status_bar = ttk.Label(
            self.root, textvariable=self.status_var, anchor="w"
        )
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=6, pady=2)
        self.ui_scale.register_pack(self.status_bar, 6, 2)

    def _on_ui_thread(self) -> bool:
        return threading.current_thread() is threading.main_thread()

    def set_status(self, text: str) -> None:
        value = str(text)
        if self._on_ui_thread():
            self.status_var.set(value)
        else:
            self.root.after(0, lambda: self.status_var.set(value))

    def _build_log(self) -> None:
        """Cria e empacota o log uma unica vez, antes de qualquer aba.

        O painel fica entre o notebook e a barra de status, com altura de tres
        linhas: ``app.log()`` e visivel sem roubar espaco das abas.  O menu
        Configuracoes -> Log permite oculta-lo e mostra-lo novamente.
        """
        frame = ttk.Frame(self.root)
        label = ttk.Label(frame, text=t("log_section"))
        mark(label, "log_section")
        label.pack(side=tk.LEFT, anchor="w", padx=(0, 6))
        self.log_text = tk.Text(frame, height=3, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        frame.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 2))
        self.ui_scale.register_pack(frame, 6, (0, 2))
        self._log_frame = frame
        self._log_visible = True

    def _toggle_log(self) -> None:
        """Mostra/oculta o painel de log sem destruir seu conteudo."""
        visible = bool(self._log_var.get()) if hasattr(self, "_log_var") else not getattr(
            self, "_log_visible", True
        )
        frame = getattr(self, "_log_frame", None)
        if frame is None:
            return
        if visible and not frame.winfo_manager():
            # ``after=status_bar`` preserva a ordem original: status na base,
            # log imediatamente acima dele.
            # Repack manual precisa dos mesmos paddings escalados; sem isso o
            # log voltaria aos valores BASE ao ser mostrado de novo.
            frame.pack(side=tk.BOTTOM, fill=tk.X,
                       padx=self.ui_scale.scale_pad(6),
                       pady=self.ui_scale.scale_pad((0, 2)),
                       after=self.status_bar)
            frame.tkraise()
        elif not visible and frame.winfo_manager():
            frame.pack_forget()
        self._log_visible = bool(frame.winfo_manager())

    def log(self, text: str) -> None:
        value = str(text)

        def append() -> None:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, value + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        if self._on_ui_thread():
            append()
        else:
            self.root.after(0, append)

    # ---- cabecalho unico ------------------------------------------------
    def _build_header(self) -> None:
        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=6, pady=(6, 2))
        self.ui_scale.register_pack(header, 6, (6, 2))
        self.header_frame = header

        label = ttk.Label(header, text=t("header_project"))
        mark(label, "header_project")
        label.pack(side=tk.LEFT, padx=(4, 2))
        self._project_items = {}
        names: list[str] = []
        for info in self.project_context.discover():
            display = str(info.name or info.id)
            if display in self._project_items:
                display = f"{display} — {info.id}"
            self._project_items[display] = info
            names.append(display)
        self.project_box = ttk.Combobox(
            header, state="readonly", width=34, values=names
        )
        if names:
            self.project_box.current(0)
        self.project_box.bind("<<ComboboxSelected>>", self._on_project_selected)
        mark(self.project_box, "header_project")
        self.project_box.pack(side=tk.LEFT, padx=(2, 12))

        label = ttk.Label(header, text=t("header_profile"))
        mark(label, "header_profile")
        label.pack(side=tk.LEFT, padx=(4, 2))
        profile_names = profiles_api.list_profiles(self.profiles_path)
        self.profile_box = ttk.Combobox(
            header, state="readonly", width=26, values=profile_names
        )
        if self.active_profile in profile_names:
            self.profile_box.current(profile_names.index(self.active_profile))
        elif profile_names:
            self.active_profile = profile_names[0]
            self.profile_box.current(0)
        self.profile_box.bind("<<ComboboxSelected>>", self._on_profile_selected)
        mark(self.profile_box, "header_profile")
        self.profile_box.pack(side=tk.LEFT, padx=(2, 12))

        label = ttk.Label(header, text=t("header_language"))
        mark(label, "header_language")
        label.pack(side=tk.LEFT, padx=(4, 2))
        codes = available_languages()
        self.language_box = ttk.Combobox(
            header,
            state="readonly",
            width=14,
            values=[language_label(code) for code in codes],
        )
        if self.ui_language in codes:
            self.language_box.current(codes.index(self.ui_language))
        self.language_box.bind("<<ComboboxSelected>>", self._on_language_selected)
        mark(self.language_box, "header_language")
        self.language_box.pack(side=tk.LEFT, padx=2)

    def _on_project_selected(self, _event=None) -> None:
        info = self._project_items.get(str(self.project_box.get()))
        if info is None:
            return
        try:
            self.project_context.activate(info.path)
        except (ValueError, FileNotFoundError, OSError) as exc:
            message = t("projects_status_activate_fail", exc=exc)
            self.set_status(message)
            self.log(message)
            return
        message = t("projects_status_selected", name=info.name)
        self.set_status(message)
        self.log(message)
        # ``activate`` ja chama notify(); o bus alcanca abas ainda nao inscritas.
        self.emit("project_activated", str(info.path))
        scene = self.scene_tab
        if scene is not None:
            try:
                scene.bind_project(info.path)
            except Exception:
                pass

    def _on_profile_selected(self, _event=None) -> None:
        self.set_active_profile(str(self.profile_box.get()))

    def _on_language_selected(self, _event=None) -> None:
        display = str(self.language_box.get())
        for code in available_languages():
            if language_label(code) == display:
                self.apply_language(code)
                return

    # ---- notebook de 7 abas --------------------------------------------
    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.ui_scale.register_pack(self.notebook, 6, 6)
        for index, (attribute, key) in enumerate(TAB_SPECS):
            frame = ttk.Frame(self.notebook)
            setattr(self, attribute, frame)
            self.notebook.add(frame, text=t(key))
            mark(self.notebook, key, kind="tab", index=index)

        # Aliases estaveis (inclui o nome legendas e os nomes da casca antiga).
        self.tab_legendas = self.tab_cue_editor
        self.pipeline_frame = self.tab_cue_editor
        self.settings_frame = self.tab_settings
        self.cue_editor_frame = self.tab_cue_editor
        self.assistant_frame = self.tab_assistant
        self.production_frame = self.tab_production
        self.scene_frame = self.tab_scene
        self.audio_frame = self.tab_audio
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _on_tab_changed(self, _event=None) -> None:
        """Constroi a aba lazy cujo *widget* esta selecionado (sem indices)."""
        selected = self.notebook.select()
        if selected == str(self.tab_production):
            self._ensure_production()
        elif selected == str(self.tab_scene):
            self._ensure_scene()
        elif selected == str(self.tab_audio):
            self._ensure_audio()

    # ---- construcao eager ----------------------------------------------
    def _build_eager_tabs(self) -> None:
        from .tk_projects_tab import ProjectsTab
        from .tk_cue_editor import CueEditorTab
        from .tk_pipeline_tab import PipelineTab
        from .tk_settings_tab import SettingsTab
        from .tk_assistant_tab import AssistantTab

        self.projects_tab = ProjectsTab(self, self.tab_projects)
        self.cue_editor_tab = CueEditorTab(self.tab_cue_editor, self)
        # Bloco G vive dentro de Legendas, sem midia/letra/align/burn/log/progresso.
        self.pipeline_tools = PipelineTab(
            self.tab_cue_editor, self, tools_only=True
        )
        self.settings_tab = SettingsTab(
            self.tab_settings, self, self.profiles_path, self.settings_path
        )
        self.assistant_tab = AssistantTab(self.tab_assistant, self)

    # ---- construcao lazy (por identidade de widget) --------------------
    def _ensure_production(self):
        if self.production_tab is None:
            from .tk_production_tab import ProductionTab

            self.production_tab = ProductionTab(self, self.tab_production)
            self._bind_production_events()
        return self.production_tab

    def _bind_production_events(self) -> None:
        if self._production_events_bound or self.production_tab is None:
            return
        tab = self.production_tab
        for kind, method in (
            ("waveform", "_on_waveform"),
            ("media_error", "_on_media_error"),
            ("log", "_on_log"),
            ("complete", "_on_complete"),
            ("cancelled", "_on_cancelled"),
            ("failed", "_on_failed"),
            ("batch_status", "_on_batch_status"),
            ("qc", "_on_qc"),
            ("batch_done", "_on_batch_done"),
        ):
            handler = getattr(tab, method, None)
            if callable(handler):
                self.on(kind, handler)
        self._production_events_bound = True

    def _ensure_scene(self):
        if self.scene_tab is None:
            from .tk_scene_tab import SceneTab

            self.scene_tab = SceneTab(self, self.tab_scene)
        return self.scene_tab

    def _ensure_audio(self):
        if self.audio_tab is None:
            from .tk_audio_tab import AudioTab

            self.audio_tab = AudioTab(self, self.tab_audio)
            self._bind_audio_events()
        return self.audio_tab

    def _bind_audio_events(self) -> None:
        if self._audio_events_bound or self.audio_tab is None:
            return
        tab = self.audio_tab
        self.on("audio_log", tab._audio_append_log)
        self.on("audio_done", self._on_audio_done)
        self.on("audio_cancelled", self._on_audio_cancelled)
        self._audio_events_bound = True

    def _on_audio_done(self, payload) -> None:
        text = str(payload or "").strip()
        if text:
            self.audio_tab._audio_append_log(text)
        self.audio_tab._audio_finish()

    def _on_audio_cancelled(self, _payload=None) -> None:
        self.set_status(t("cancelled"))
        self.audio_tab._audio_finish(status=t("cancelled"))

    # ---- fila de eventos -----------------------------------------------
    def emit(self, kind: str, payload=None) -> None:
        self.events.put((str(kind), payload))

    def on(self, kind: str, handler) -> None:
        bucket = self._handlers.setdefault(str(kind), [])
        if handler not in bucket:
            bucket.append(handler)

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                for handler in list(self._handlers.get(kind, ())):
                    try:
                        handler(payload)
                    except Exception as exc:
                        # Nunca engole o erro: registra o handler e continua a fila.
                        name = getattr(handler, "__name__", repr(handler))
                        message = (
                            f"Erro no handler do evento '{kind}' "
                            f"({name}): {type(exc).__name__}: {exc}"
                        )
                        try:
                            self.log(message)
                        except Exception:
                            pass
                        try:
                            self.set_status(t("error_msg", exc=exc))
                        except Exception:
                            pass
                        continue
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    # ---- atalhos globais com guarda de aba ativa -----------------------
    def _setup_shortcuts(self) -> None:
        for sequence, method in (
            ("<Control-s>", self._scene_save),
            ("<Control-z>", self._scene_undo),
            ("<Control-y>", self._scene_redo),
            ("<Delete>", self._scene_remove),
            ("<space>", self._scene_preview),
        ):
            # Uma unica inscricao por sequencia; os binds locais do editor de
            # cues continuam valendo em Legendas (a aba ativa filtra aqui).
            self.root.bind_all(
                sequence, lambda _event, action=method: self._scene_shortcut(action)
            )

    def _scene_shortcut(self, action) -> None:
        if self.is_typing():
            return
        if self.notebook.select() != str(self.tab_scene):
            return
        self._ensure_scene()
        try:
            action()
        except Exception as exc:
            self.set_status(t("error_msg", exc=exc))

    def _scene_save(self) -> None:
        if self.scene_tab is not None:
            self.scene_tab._scene_save()

    def _scene_undo(self) -> None:
        if self.scene_tab is not None:
            self.scene_tab._scene_undo()

    def _scene_redo(self) -> None:
        if self.scene_tab is not None:
            self.scene_tab._scene_redo()

    def _scene_remove(self) -> None:
        if self.scene_tab is not None:
            self.scene_tab._scene_remove()

    def _scene_preview(self) -> None:
        if self.scene_tab is not None:
            self.scene_tab._scene_preview()

    # ---- fachada compartilhada -----------------------------------------
    def is_typing(self) -> bool:
        try:
            widget = self.root.focus_get()
        except Exception:
            return False
        if widget is None:
            return False
        try:
            return str(widget.winfo_class()) in _TYPING_CLASSES
        except Exception:
            return False

    # Alias legado usado por codigo que chamava ``app._typing()``.
    def _typing(self) -> bool:
        return self.is_typing()

    def get_setting(self, key: str, default=None):
        return settings_module.get_setting(key, default, self.settings_path)

    @property
    def production_app(self):
        """Alias antigo: a producao agora e a aba Producao."""
        return self.production_tab

    @production_app.setter
    def production_app(self, value) -> None:
        self.production_tab = value

    @property
    def production_frame_alias(self):
        return self.tab_production

    @property
    def media_path(self):
        return self.production_tab.media_path if self.production_tab else None

    @media_path.setter
    def media_path(self, value) -> None:
        if self.production_tab is not None:
            self.production_tab.media_path = value

    def production_busy(self) -> bool:
        return self.production_tab.production_busy() if self.production_tab else False

    def busy_any(self) -> bool:
        return self.production_busy()

    def selected_batch_item(self):
        return self.production_tab.selected_batch_item() if self.production_tab else None

    def background_image(self):
        return self.production_tab.background_image() if self.production_tab else None

    def first_batch_item(self):
        return self.production_tab.first_batch_item() if self.production_tab else None

    def append_qc(self, text: str) -> None:
        if self.production_tab is not None:
            self.production_tab._append_qc(str(text))
        else:
            self.log(str(text))

    def scene_snapshot(self) -> dict:
        if self.scene_tab is None:
            return {}
        return copy.deepcopy(self.scene_tab.scene)

    def scene_autosave_path(self) -> Path:
        return application_data_root() / "scene-autosave.json"

    def bind_project(self, path=None) -> bool:
        scene = self.scene_tab
        if scene is None:
            return False
        try:
            return bool(scene.bind_project(path))
        except Exception:
            return False

    def _find_project_root(self, path=None) -> Path | None:
        if path is None:
            path = self.media_path or Path.cwd()
        candidate = Path(str(path)).expanduser()
        try:
            candidate = candidate.resolve()
        except OSError:
            candidate = candidate.absolute()
        if candidate.is_file():
            candidate = candidate.parent
        for _ in range(12):
            if (candidate / "projeto.json").is_file():
                return candidate
            parent = candidate.parent
            if parent == candidate:
                break
            candidate = parent
        return None

    def project_root_for(self, path=None) -> Path | None:
        active = self.project_context.active_dir
        if path is not None:
            try:
                resolved = Path(str(path)).expanduser().resolve()
            except OSError:
                resolved = None
            if active is not None and resolved is not None:
                try:
                    resolved.relative_to(active.resolve())
                    return active
                except ValueError:
                    pass
            found = self._find_project_root(path)
            if found is not None:
                return found
        if active is not None:
            return active
        if path is None:
            return self._find_project_root(self.media_path or Path.cwd())
        return None

    def open_folder(self, path) -> None:
        if os.name == "nt" and hasattr(os, "startfile"):
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
            except OSError as exc:
                self.log(t("open_auto_error", exc=exc))

    def set_active_profile(
        self, name: str, *, settings_payload: Mapping[str, Any] | None = None
    ) -> bool:
        """Unico dono da troca de perfil (grava active + profile juntos).

        ``settings_payload`` (quando fornecido) e gravado na mesma unica chamada
        a ``update_settings``, com ``profile`` sobrescrito para ``name``.  A
        sincronizacao em memoria (preferences/settings/combo) continua aqui.
        """
        name = str(name or "").strip()
        if not name:
            return False
        try:
            profiles_api.set_active_profile(
                name,
                profiles_path=self.profiles_path,
                settings_path=self.settings_path,
                settings_payload=settings_payload,
            )
        except (ValueError, OSError) as exc:
            self.set_status(t("error_msg", exc=exc))
            return False
        self.active_profile = name
        self.preferences["profile"] = name
        self.settings["profile"] = name
        production = self.production_tab
        if production is not None:
            production.active_profile = name
            names = profiles_api.list_profiles(self.profiles_path)
            combo = getattr(production, "profile_box", None)
            if combo is not None and name in names:
                try:
                    combo.current(names.index(name))
                except Exception:
                    pass
        self.set_status(t("profile_loaded", name=name))
        return True

    def cues(self):
        tab = self.cue_editor_tab
        return list(tab.cues) if tab is not None and tab.cues else []

    def set_cues(self, cues) -> bool:
        tab = self.cue_editor_tab
        if tab is None:
            return False
        try:
            tab.set_cues(list(cues or []))
            return True
        except Exception:
            return False

    def project_srt(self):
        path = self.project_context.srt()
        return path if path is not None else None

    def lyrics_text(self) -> str:
        """Texto de letra da fachada (aceito como string pelo assistente)."""
        return self._lyrics

    def set_lyrics_text(self, text: str) -> None:
        self._lyrics = str(text or "")

    def __getattr__(self, name: str):
        """Compatibilidade: delega nomes legados as abas ja construidas."""
        if name.startswith("_"):
            raise AttributeError(name)
        for tab_name in (
            "production_tab", "scene_tab", "audio_tab", "cue_editor_tab",
            "projects_tab", "settings_tab", "assistant_tab", "pipeline_tools",
        ):
            tab = self.__dict__.get(tab_name)
            if tab is not None:
                try:
                    return getattr(tab, name)
                except AttributeError:
                    continue
        raise AttributeError(name)

    # ---- tema / fechamento ---------------------------------------------
    def _apply_scaled_theme(self) -> None:
        """Reaplica o tema com a fonte escalada, sem gravar e sem retranslate.

        Chamado pelo ``UiScale.on_apply`` depois que fonts/paddings ja foram
        reconfigurados.  A escala e in place: nenhuma aba e reconstruida e o
        funil de idioma (``retranslate``) nao e tocado.
        """
        self._apply_theme(self.preferences)

    def _apply_theme(self, cfg: dict) -> None:
        try:
            bg = cfg.get("bg_color", "#F4EFE0")
            fg = cfg.get("fg_color", "#1A1A1A")
            accent = cfg.get("accent_color", "#043D20")
            btn_bg = cfg.get("button_bg", "#EDE8DA")
            font_family = cfg.get("font_family", "Segoe UI")
            font_size = cfg.get("font_size", 10)
            self.root.configure(bg=bg)
            style = ttk.Style()
            style.configure(".", background=bg, foreground=fg,
                            font=self.ui_scale.font(font_family, font_size))
            style.configure("TFrame", background=bg)
            style.configure("TLabel", background=bg, foreground=fg)
            style.configure("TButton", background=btn_bg, foreground=fg)
            style.configure("TEntry", fieldbackground="#FFFFFF", foreground=fg)
            style.configure("TCombobox", fieldbackground="#FFFFFF", foreground=fg)
            style.configure("TLabelframe", background=bg, foreground=accent)
            style.configure("TLabelframe.Label", background=bg, foreground=accent)
        except Exception:
            pass

    def _on_close(self) -> None:
        try:
            geo = self.root.geometry().split("+")[0]
            if "x" in geo:
                w, h = geo.split("x")
                settings_module.update_settings(
                    {"window_width": int(w), "window_height": int(h)},
                    self.settings_path,
                )
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    VilmTkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
