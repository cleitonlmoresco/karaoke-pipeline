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
from tkinter import (BOTH, END, LEFT, RIGHT, X, Y, Canvas, Listbox, StringVar,
                     Text, Tk, filedialog, messagebox)
from tkinter import ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from . import profiles_api, settings
from .media import waveform_peaks
from .platform_paths import application_data_root
from .process_runner import JobCancelled, console_python, run_job
from .project import update_project_meta
from .services.align import generate_alignment
from .i18n import (
    available_languages,
    language_label,
    mark as mark_widget,
    on_language_changed,
    retranslate,
    set_language,
    t,
)


APP_NAME = "Vilm Lyrics Aligner"
APP_DIR = application_data_root()
# Caminhos legados mantidos apenas para compatibilidade de importacao; a
# leitura/gravacao de preferencias passa pelo modulo ``settings``.
CONFIG_PATH = APP_DIR / "config.json"
PREFERENCES_PATH = APP_DIR / "desktop-preferences.json"
VILM_APP_PATH = Path(__file__).resolve().parents[2] / "app"
AUTOSAVE_PATH = APP_DIR / "scene-autosave.json"


def _job_python() -> str:
    """Compatibilidade: o interprete do job de audio vive no modulo de audio."""
    from .tk_audio_tab import _job_python as _module_job_python
    return _module_job_python()


def _production_module():
    """Importa o modulo de producao sem exigir uma instancia da aba."""
    from . import tk_production_tab as _module
    return _module


def _format_time(seconds: float) -> str:
    """Compatibilidade: o formatador vive no modulo de producao."""
    return _production_module()._format_time(seconds)


def _find_project_root(path: str | Path | None) -> Path | None:
    """Suba pelos pais de ``path`` ate encontrar um ``projeto.json``.

    Nada e escrito: a funcao apenas localiza a raiz de um projeto existente.
    Quando nao ha projeto, retorna ``None`` e os chamadores usam o comportamento
    legado (ao lado da midia).
    """
    if path is None:
        return None
    candidate = Path(path).expanduser()
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


def _audio_defaults() -> dict[str, object]:
    """Compatibilidade: os defaults de audio vivem no modulo de audio."""
    from .tk_audio_tab import _audio_defaults as _module_defaults
    return _module_defaults()


def _result_value(result, key: str, default=None):
    """Compatibilidade: o leitor de resultado vive no modulo de producao."""
    return _production_module()._result_value(result, key, default)


class DesktopApp:
    def __init__(self, root: Tk, parent=None, embedded: bool = False) -> None:
        self.root = root
        self.parent = parent if parent is not None else root
        self.embedded = embedded
        # ``settings_path`` permite isolar testes; por padrao o destino e o
        # settings.json resolvido por lyrics_aligner.settings.
        self.settings_path: str | Path | None = None
        self.preferences = settings.load_settings(self._settings_target())
        self.language = str(self.preferences.get("ui_language") or "pt")
        if self.language not in available_languages():
            self.language = "pt"
        set_language(self.language)
        self.config = self.preferences
        self.backend = str(self.config.get("backend") or "cpu").casefold()
        # Producao: estado e widgets vivem em tk_production_tab.ProductionTab,
        # criado em _build(); as propriedades abaixo apenas o expoem.
        self.production_tab = None
        # audio_worker / audio_cancel_event vivem em tk_audio_tab.AudioTab e
        # sao expostos aqui por propriedades somente-leitura (ver fachada).
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.profiles = profiles_api.load_profiles()
        # Caminho opcional de profiles.json, desviavel em testes isolados.
        self.profiles_path: str | Path | None = None
        self.active_profile = str(
            self.preferences.get("profile") or self.profiles.get("active") or ""
        )
        # Cena: estado e widgets vivem em tk_scene_tab.SceneTab, criado em
        # _build(); as propriedades abaixo apenas o expoem pela fachada.
        self.scene_tab = None

        if not embedded:
            root.title(APP_NAME)
            root.geometry("1100x760")
            root.minsize(900, 640)
        self._configure_style()
        self._build()
        self._apply_language()
        # Re-apply the Karaoke tab labels after every retranslation, without
        # touching the language itself or persisting anything here.
        on_language_changed(self._apply_language)
        self._setup_shortcuts()
        root.after(100, self._poll_events)

    def _typing(self) -> bool:
        widget = self.root.focus_get()
        return widget is not None and widget.winfo_class() in {
            "Text", "Entry", "TEntry", "Spinbox", "TSpinbox", "Combobox", "TCombobox"
        }

    def _settings_target(self) -> str | Path | None:
        """Destino opcional do settings.json (uso em testes isolados)."""
        if self.settings_path is not None:
            return self.settings_path
        return os.environ.get("LYRICS_ALIGNER_SETTINGS_PATH") or None

    def _save_preferences(self, **updates) -> None:
        """Wrapper fino sobre settings.update_settings (gravacao mesclada).

        Mantem o dict em memoria sincronizado para que trocar o idioma nao
        apague o perfil (e vice-versa). Nenhum arquivo legado e reescrito.
        """
        try:
            self.preferences = settings.update_settings(
                updates, self._settings_target()
            )
        except OSError:
            self.preferences.update(updates)

    def _setup_shortcuts(self) -> None:
        """Atalhos no estilo de estacoes de edicao (Space, Delete, Ctrl+S/Z/Y)."""
        self.root.bind_all("<Control-s>", lambda _e: self._scene_save())
        self.root.bind_all("<Control-z>", lambda _e: None if self._typing() else self._scene_undo())
        self.root.bind_all("<Control-y>", lambda _e: None if self._typing() else self._scene_redo())
        self.root.bind_all("<Delete>", lambda _e: None if self._typing() else self._scene_remove())
        self.root.bind_all("<space>", lambda _e: None if self._typing() else self._scene_preview())

    # ---- fachada da producao (tk_production_tab.ProductionTab) ----------
    # O modulo e dono do estado e dos widgets; aqui so ha acesso
    # somente-leitura/escrita para manter as assinaturas da casca existente.
    @property
    def media_path(self) -> Path | None:
        return self.production_tab.media_path if self.production_tab is not None else None

    @media_path.setter
    def media_path(self, value) -> None:
        if self.production_tab is not None:
            self.production_tab.media_path = value

    @property
    def duration(self) -> float:
        return self.production_tab.duration if self.production_tab is not None else 0.0

    @duration.setter
    def duration(self, value: float) -> None:
        if self.production_tab is not None:
            self.production_tab.duration = value

    @property
    def peaks(self) -> list[float]:
        return self.production_tab.peaks if self.production_tab is not None else []

    @peaks.setter
    def peaks(self, value: list[float]) -> None:
        if self.production_tab is not None:
            self.production_tab.peaks = value

    @property
    def range_start(self) -> float:
        return self.production_tab.range_start if self.production_tab is not None else 0.0

    @range_start.setter
    def range_start(self, value: float) -> None:
        if self.production_tab is not None:
            self.production_tab.range_start = value

    @property
    def range_end(self) -> float:
        return self.production_tab.range_end if self.production_tab is not None else 0.0

    @range_end.setter
    def range_end(self, value: float) -> None:
        if self.production_tab is not None:
            self.production_tab.range_end = value

    @property
    def drag_start_x(self) -> float | None:
        return self.production_tab.drag_start_x if self.production_tab is not None else None

    @drag_start_x.setter
    def drag_start_x(self, value) -> None:
        if self.production_tab is not None:
            self.production_tab.drag_start_x = value

    @property
    def worker(self) -> threading.Thread | None:
        return self.production_tab.worker if self.production_tab is not None else None

    @worker.setter
    def worker(self, value) -> None:
        if self.production_tab is not None:
            self.production_tab.worker = value

    @property
    def cancel_event(self) -> threading.Event | None:
        return self.production_tab.cancel_event if self.production_tab is not None else None

    @cancel_event.setter
    def cancel_event(self, value) -> None:
        if self.production_tab is not None:
            self.production_tab.cancel_event = value

    @property
    def last_output(self) -> Path | None:
        return self.production_tab.last_output if self.production_tab is not None else None

    @last_output.setter
    def last_output(self, value) -> None:
        if self.production_tab is not None:
            self.production_tab.last_output = value

    @property
    def lead_ms(self) -> int:
        return self.production_tab.lead_ms if self.production_tab is not None else 600

    @lead_ms.setter
    def lead_ms(self, value: int) -> None:
        if self.production_tab is not None:
            self.production_tab.lead_ms = value

    @property
    def last_warnings(self) -> list[str]:
        return self.production_tab.last_warnings if self.production_tab is not None else []

    @last_warnings.setter
    def last_warnings(self, value: list[str]) -> None:
        if self.production_tab is not None:
            self.production_tab.last_warnings = value

    def set_active_profile(self, name: str) -> str:
        """Dono unico da ativacao: profiles.json.active + settings.profile."""
        profiles_path = getattr(self, "profiles_path", None)
        settings_path = self._settings_target()
        result = profiles_api.set_active_profile(
            name, profiles_path=profiles_path, settings_path=settings_path
        )
        self.profiles = profiles_api.load_profiles(profiles_path)
        self.active_profile = name
        self.preferences["profile"] = name
        return result

    def open_folder(self, folder: str) -> None:
        """Abre uma pasta no explorador; implementacao platform-specific."""
        if os.name == "nt":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    def __getattr__(self, name: str):
        """Compatibilidade: atributos de widget dos modos agora nos modulos.

        As propriedades acima cobrem o estado; este fallback cobre os widgets
        (``status``, ``batch_list``, ``profile_box``, ...) que a casca antiga
        expunha diretamente, sem duplicar nenhum widget na fachada.
        """
        for tab_name in ("production_tab", "audio_tab", "scene_tab"):
            tab = self.__dict__.get(tab_name)
            if tab is not None:
                try:
                    return getattr(tab, name)
                except AttributeError:
                    pass
        raise AttributeError(name)

    # ---- fachada para os modulos --------------------------------------
    # Os tab modules nao acessam widgets alheios: estes wrappers finos sao a
    # unica ponte entre eles e o estado desta aba.
    def set_status(self, text: str) -> None:
        self.production_tab.status.configure(text=text)

    def emit(self, kind: str, payload=None) -> None:
        self.events.put((kind, payload))

    def selected_batch_item(self) -> str | None:
        return self.production_tab.selected_batch_item()

    def project_root_for(self, path: str | Path | None = None) -> Path | None:
        return self._project_root_for(path)

    # ---- fachada da cena (tk_scene_tab.SceneTab) -------------------------
    # O modulo de cena e dono do estado e dos widgets; aqui so ha acesso
    # somente-leitura/escrita e snapshots para o lote.
    @property
    def scene(self) -> dict:
        return self.scene_tab.scene

    @scene.setter
    def scene(self, value: dict) -> None:
        self.scene_tab.scene = value

    @property
    def scene_project_dir(self) -> Path | None:
        return self.scene_tab.scene_project_dir

    @scene_project_dir.setter
    def scene_project_dir(self, value: Path | None) -> None:
        self.scene_tab.scene_project_dir = value

    @property
    def _scene_ready(self) -> bool:
        return self.scene_tab._scene_ready

    @_scene_ready.setter
    def _scene_ready(self, value: bool) -> None:
        self.scene_tab._scene_ready = value

    @property
    def scene_history(self) -> list[dict]:
        return self.scene_tab.scene_history

    @property
    def scene_future(self) -> list[dict]:
        return self.scene_tab.scene_future

    def scene_snapshot(self) -> dict:
        """Copia imutavel da cena para o snapshot do lote (thread principal)."""
        return copy.deepcopy(self.scene_tab.scene)

    def scene_autosave_path(self) -> Path:
        """Caminho global de autosave (desviavel em testes headless)."""
        return AUTOSAVE_PATH

    def background_image(self) -> Path | None:
        """Imagem de fundo escolhida no lote, sem acessar widget alheio."""
        return self.production_tab.background_image()

    def first_batch_item(self) -> str | None:
        """Primeiro item do lote, sem acessar widget alheio."""
        return self.production_tab.first_batch_item()

    def append_qc(self, text: str) -> None:
        self.production_tab._append_qc(text)

    def bind_project(self, path: str | Path | None = None) -> bool:
        """Fachada publica para set_media na casca de 7 abas (F3-I2)."""
        return self.scene_tab.bind_project(path)

    def production_busy(self) -> bool:
        """Job principal da producao ativo (o job de audio nao conta)."""
        return self.production_tab.production_busy() if self.production_tab else False

    def busy_any(self) -> bool:
        """Qualquer outro job do app ativo; AudioTab usa isso no guarda."""
        if self.production_busy():
            return True
        for owner in (self.parent, self.root):
            check = getattr(owner, "busy_any", None)
            if callable(check):
                try:
                    if check():
                        return True
                except Exception:
                    pass
        return False

    @property
    def audio_worker(self) -> threading.Thread | None:
        tab = getattr(self, "audio_tab", None)
        return tab.audio_worker if tab is not None else None

    @property
    def audio_cancel_event(self) -> threading.Event | None:
        tab = getattr(self, "audio_tab", None)
        return tab.audio_cancel_event if tab is not None else None

    # ---- ferramentas de audio (wrappers para tk_audio_tab.AudioTab) ----
    def _audio_use_selected(self) -> None:
        self.audio_tab._audio_use_selected()

    def _audio_path(self) -> Path | None:
        return self.audio_tab._audio_path()

    def _audio_env(self) -> dict[str, str]:
        return self.audio_tab._audio_env()

    def _audio_command(self, args: list[str]) -> list[str]:
        return self.audio_tab._audio_command(args)

    def _audio_start(self, command: list[str]) -> None:
        self.audio_tab._audio_start(command)

    def _audio_job(self, command: list[str]) -> None:
        self.audio_tab._audio_job(command)

    def _audio_finish(self, status: str | None = None) -> None:
        self.audio_tab._audio_finish(status)

    def _audio_cancel(self) -> None:
        self.audio_tab._audio_cancel()

    def _audio_append_log(self, text: str) -> None:
        self.audio_tab._audio_append_log(text)

    def _project_root_for(self, path: str | Path | None = None) -> Path | None:
        """Raiz do projeto que contem ``path`` (por padrao, a midia atual)."""
        if path is None:
            path = self.media_path or self.last_output or Path.cwd()
        return _find_project_root(path)

    def _audio_fields(self) -> dict[str, object]:
        return self.audio_tab._audio_fields()

    def _audio_field_args(self) -> list[str]:
        return self.audio_tab._audio_field_args()

    def _audio_output_paths(self, media: Path, suffix: str | None
                            ) -> tuple[Path, Path | None]:
        return self.audio_tab._audio_output_paths(media, suffix)

    def _audio_run(self, args: list[str] | None = None, suffix: str = "audio") -> None:
        self.audio_tab._audio_run(args, suffix)

    def _audio_analyze(self) -> None:
        self.audio_tab._audio_analyze()

    def _audio_instrumental(self) -> None:
        self.audio_tab._audio_instrumental()

    def _audio_voice(self, gain: float) -> None:
        self.audio_tab._audio_voice(gain)

    def _audio_denoise(self) -> None:
        self.audio_tab._audio_denoise()

    def _audio_enhance(self) -> None:
        self.audio_tab._audio_enhance()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", foreground="#666666")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 8))

    def _build(self) -> None:
        outer = ttk.Frame(self.parent, padding=12)
        outer.pack(fill=BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=X, pady=(0, 10))
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side=LEFT)
        self.language_box = ttk.Combobox(header, state="readonly", width=14,
                                         values=[language_label(code) for code in available_languages()])
        self.language_box.pack(side=RIGHT)
        self.language_box.current(max(0, available_languages().index(self.language)))
        self.language_box.bind("<<ComboboxSelected>>", self._language_changed)

        # Bloco de producao movido para tk_production_tab.ProductionTab; os
        # wrappers abaixo preservam todos os bindings e a casca existente.
        from .tk_production_tab import ProductionTab
        self.production_tab = ProductionTab(self, outer)

        # Bloco de audio movido para tk_audio_tab.AudioTab; os wrappers
        # _audio_* abaixo preservam todos os bindings e a casca existente.
        from .tk_audio_tab import AudioTab
        self.audio_tab = AudioTab(self, outer)

        # Bloco de cena movido para tk_scene_tab.SceneTab; os 28 wrappers
        # _scene_* / _snap / _draw_timeline / _timeline_* abaixo preservam todos
        # os bindings e a casca existente (5 abas).
        from .tk_scene_tab import SceneTab
        self.scene_tab = SceneTab(self, outer)

        self._apply_profile()

    # ---- producao: wrappers para tk_production_tab.ProductionTab -------
    # Assinaturas identicas as do modulo: bindings e casca permanecem, sem
    # logica duplicada nesta fachada.
    def _profile_timing(self) -> dict[str, int]:
        return self.production_tab._profile_timing()

    def _apply_profile(self) -> None:
        self.production_tab._apply_profile()

    def _update_preview(self) -> None:
        self.production_tab._update_preview()

    def _profile_changed(self, _event=None) -> None:
        self.production_tab._profile_changed(_event)

    def _browse_logo(self) -> None:
        self.production_tab._browse_logo()

    def _browse_bg(self) -> None:
        self.production_tab._browse_bg()

    def _add_batch(self) -> None:
        self.production_tab._add_batch()

    def _add_folder(self) -> None:
        self.production_tab._add_folder()

    def _scene_name_for(self, folder: Path, count: int,
                        template: str | None = None) -> str:
        return self.production_tab._scene_name_for(folder, count, template)

    def _write_history(self, folder: Path, outputs: list[str],
                       warnings: list[str]) -> None:
        self.production_tab._write_history(folder, outputs, warnings)

    def _record_history(self, folders, outputs: list[str],
                        warnings: list[str]) -> None:
        self.production_tab._record_history(folders, outputs, warnings)

    def _update_project_outputs(self, project_dir: str | Path | None,
                                outputs: dict[str, str] | None = None,
                                **details) -> None:
        self.production_tab._update_project_outputs(project_dir, outputs, **details)

    def _remove_batch(self) -> None:
        self.production_tab._remove_batch()

    def _clear_batch(self) -> None:
        self.production_tab._clear_batch()

    def _append_qc(self, text: str) -> None:
        self.production_tab._append_qc(text)

    def _generate_batch(self) -> None:
        self.production_tab._generate_batch()

    def _batch_worker(self, items: list[str], snapshot: dict) -> None:
        self.production_tab._batch_worker(items, snapshot)

    # ---- editor de cena: 28 wrappers para tk_scene_tab.SceneTab ---------
    # Assinaturas identicas as do modulo: atalhos e bindings continuam
    # acionando o modulo, sem logica de cena duplicada nesta fachada.
    def _scene_push(self) -> None:
        self.scene_tab._scene_push()

    def _snap(self, value: float) -> float:
        return self.scene_tab._snap(value)

    def _scene_autosave_path(self) -> Path:
        return self.scene_tab._scene_autosave_path()

    def _scene_bind_project(self, path: str | Path | None = None) -> bool:
        return self.scene_tab._scene_bind_project(path)

    def _scene_view(self) -> tuple[int, int, float, float]:
        return self.scene_tab._scene_view()

    def _scene_autosave(self) -> None:
        self.scene_tab._scene_autosave()

    def _scene_load_autosave(self) -> None:
        self.scene_tab._scene_load_autosave()

    def _scene_redraw(self) -> None:
        self.scene_tab._scene_redraw()

    def _draw_timeline(self) -> None:
        self.scene_tab._draw_timeline()

    def _scene_format_changed(self, _event=None) -> None:
        self.scene_tab._scene_format_changed(_event)

    def _scene_select(self, _event=None) -> None:
        self.scene_tab._scene_select(_event)

    def _scene_apply_props(self) -> None:
        self.scene_tab._scene_apply_props()

    def _scene_press(self, event) -> None:
        self.scene_tab._scene_press(event)

    def _scene_drag(self, event) -> None:
        self.scene_tab._scene_drag(event)

    def _scene_release(self, _event=None) -> None:
        self.scene_tab._scene_release(_event)

    def _timeline_press(self, event) -> None:
        self.scene_tab._timeline_press(event)

    def _timeline_drag(self, event) -> None:
        self.scene_tab._timeline_drag(event)

    def _timeline_release(self, _event=None) -> None:
        self.scene_tab._timeline_release(_event)

    def _scene_new(self) -> None:
        self.scene_tab._scene_new()

    def _scene_open(self) -> None:
        self.scene_tab._scene_open()

    def _scene_save(self) -> None:
        self.scene_tab._scene_save()

    def _scene_add_image(self) -> None:
        self.scene_tab._scene_add_image()

    def _scene_add_text(self) -> None:
        self.scene_tab._scene_add_text()

    def _scene_remove(self) -> None:
        self.scene_tab._scene_remove()

    def _scene_undo(self) -> None:
        self.scene_tab._scene_undo()

    def _scene_redo(self) -> None:
        self.scene_tab._scene_redo()

    def _scene_fx_dialog(self) -> None:
        self.scene_tab._scene_fx_dialog()

    def _scene_preview(self) -> None:
        self.scene_tab._scene_preview()

    def _set_language_text(self, widget, key: str) -> None:
        """Assign a translated text and register the widget for retranslation."""
        widget.configure(text=t(key))
        mark_widget(widget, key)

    def _sync_language_box(self) -> None:
        codes = available_languages()
        if self.language in codes:
            self.language_box.current(codes.index(self.language))

    def _apply_language(self, _lang: str | None = None) -> None:
        """Reassign Karaoke tab labels.  Registered as a retranslation hook.

        It never changes the global language and never writes settings: that
        is the job of the single funnel (``VilmTkApp.apply_language`` when
        embedded, ``DesktopApp.apply_language`` when standalone).
        """
        if _lang in available_languages():
            self.language = _lang
        self._sync_language_box()
        self.production_tab.refresh_language(_lang)
        # Rotulos de cena e audio pertencem aos modulos; cada um reaplica os
        # seus sem tocar na cena, na selecao ou nos valores digitados.
        self.scene_tab.refresh_language()
        self.audio_tab.refresh_language()

    def apply_language(self, code: str) -> bool:
        """Standalone equivalent of the shell funnel (one settings write)."""
        if code not in available_languages():
            return False
        set_language(code)
        self.language = code
        self._save_preferences(ui_language=code)
        self._sync_language_box()
        retranslate(self.root)
        self.set_status(t("language_saved", lang=language_label(code)))
        return True

    def _language_changed(self, _event=None) -> None:
        codes = available_languages()
        index = self.language_box.current()
        code = codes[index] if 0 <= index < len(codes) else "pt"
        funnel = getattr(self.parent, "apply_language", None) if self.embedded else None
        if callable(funnel):
            funnel(code)
        else:
            self.apply_language(code)

    # ---- midia/faixa/letra/SRT: wrappers de producao -------------------
    def _browse_media(self) -> None:
        self.production_tab._browse_media()

    def _load_media(self, path: Path) -> None:
        self.production_tab._load_media(path)

    def _waveform_worker(self, path: Path) -> None:
        self.production_tab._waveform_worker(path)

    def _range_mode_changed(self) -> None:
        self.production_tab._range_mode_changed()

    def _wave_press(self, event) -> None:
        self.production_tab._wave_press(event)

    def _wave_drag(self, event) -> None:
        self.production_tab._wave_drag(event)

    def _wave_release(self, event) -> None:
        self.production_tab._wave_release(event)

    def _x_to_time(self, x: float) -> float:
        return self.production_tab._x_to_time(x)

    def _draw_waveform(self) -> None:
        self.production_tab._draw_waveform()

    def _update_range_readout(self) -> None:
        self.production_tab._update_range_readout()

    def _load_lyrics(self) -> None:
        self.production_tab._load_lyrics()

    def _update_manual_visibility(self) -> None:
        self.production_tab._update_manual_visibility()

    def _selected_device(self) -> str:
        return self.production_tab._selected_device()

    def _generate(self) -> None:
        self.production_tab._generate()

    def _alignment_worker(self, lyrics_path: Path, output: Path,
                          selected: bool, options: dict) -> None:
        self.production_tab._alignment_worker(lyrics_path, output, selected, options)

    def _cancel(self) -> None:
        self.production_tab._cancel()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "waveform":
                    self.production_tab._on_waveform(payload)
                elif kind == "media_error":
                    self.production_tab._on_media_error(payload)
                elif kind == "log":
                    self.production_tab._on_log(payload)
                elif kind == "complete":
                    self.production_tab._on_complete(payload)
                elif kind == "cancelled":
                    self.production_tab._on_cancelled(payload)
                elif kind == "failed":
                    self.production_tab._on_failed(payload)
                elif kind == "batch_status":
                    self.production_tab._on_batch_status(payload)
                elif kind == "qc":
                    self.production_tab._on_qc(payload)
                elif kind == "batch_done":
                    self.production_tab._on_batch_done(payload)
                elif kind == "audio_log":
                    self._audio_append_log(str(payload))
                elif kind == "audio_done":
                    text = str(payload or "").strip()
                    if text:
                        self._audio_append_log(text)
                    self._audio_finish()
                elif kind == "audio_cancelled":
                    self.set_status(t("cancelled"))
                    self._audio_finish(status=t("cancelled"))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _finish_job(self, show_folder: bool = False) -> None:
        self.production_tab._finish_job(show_folder)

    def _set_log(self, text: str) -> None:
        self.production_tab._set_log(text)

    def _append_log(self, text: str) -> None:
        self.production_tab._append_log(text)

    def _format_time(self, seconds: float) -> str:
        return self.production_tab._format_time(seconds)

    def _result_value(self, result, key: str, default=None):
        return self.production_tab._result_value(result, key, default)

    def _open_output_folder(self) -> None:
        self.production_tab._open_output_folder()



def main() -> int:
    root = Tk()
    DesktopApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
