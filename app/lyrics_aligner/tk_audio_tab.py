"""Ferramentas de audio extraidas de ``desktop.py`` (F3-I3).

O modulo e dono dos widgets de audio e do job de audio.  Todas as dependencias
de outros blocos passam pela fachada ``app`` (``selected_batch_item``,
``media_path``, ``project_root_for``, ``set_status``, ``emit``, ``busy_any`` e
``production_busy``): nenhum widget alheio e acessado diretamente.

A traducao e aplicada na criacao com ``t()`` + ``mark()`` e reaplicada por
``refresh_language()``, registrada como hook global via WeakSet (o mesmo padrao dos
demais modulos de aba).  O modulo nunca altera o idioma global e nunca grava
preferencias.
"""

from __future__ import annotations

import os
import sys
import threading
import weakref
from pathlib import Path
from tkinter import END, LEFT, X, BooleanVar, StringVar, Text, messagebox
from tkinter import ttk
from weakref import WeakSet

from .i18n import mark, on_language_changed, t
from .process_runner import JobCancelled, console_python, run_job

APP_NAME = "Vilm Lyrics Aligner"
# Raiz de ``app``: fica um nivel acima de lyrics_aligner, como em desktop.py.
VILM_APP_PATH = Path(__file__).resolve().parents[2] / "app"


def _ui_scale(widget):
    """Provider opcional no root da janela; ``None`` = escala neutra."""
    try:
        return getattr(widget.winfo_toplevel(), "ui_scale", None)
    except Exception:
        return None


def _scaled_font(widget, family, size, *styles):
    """Fonte escalada pelo provider, ou a fonte original se ele nao existir."""
    provider = _ui_scale(widget)
    font_fn = getattr(provider, "font", None)
    if callable(font_fn):
        try:
            return tuple(font_fn(family, size, *styles))
        except Exception:
            pass
    return (family, size, *styles)


def _job_python() -> str:
    """Interprete de console usado pelo job de audio (nunca pythonw)."""
    return str(console_python(sys.executable))


def _audio_defaults() -> dict[str, object]:
    """Defaults de audio em codigo: NUNCA gravados em profiles.json."""
    return {
        "denoise": 8,
        "voice_gain": 1.6,
        "music_gain": 1.0,
        "lufs": -16.0,
        "enhance_preset": "voz",
        "normalize": True,
    }


class AudioTab:
    """Secao de audio da aba de producao (Karaoke)."""

    def __init__(self, app, parent) -> None:
        self.app = app
        self.parent = parent
        self.audio_worker: threading.Thread | None = None
        self.audio_cancel_event = threading.Event()
        self._language_widgets: list[tuple[object, str]] = []
        self.build(parent)
        _AUDIO_TABS.add(self)

    # ---- construcao -----------------------------------------------------
    def _register_text(self, widget, key: str):
        """Cria/marca um rotulo traduzivel e o guarda para refresh_language."""
        mark(widget, key)
        self._language_widgets.append((widget, key))
        return widget

    def _label(self, parent, key: str, **kwargs):
        return self._register_text(ttk.Label(parent, text=t(key), **kwargs), key)

    def _button(self, parent, key: str, command, **kwargs):
        return self._register_text(ttk.Button(parent, text=t(key), command=command, **kwargs), key)

    def build(self, parent) -> None:
        """Monta o LabelFrame de audio dentro do parent fornecido por DesktopApp."""
        audio_frame = ttk.LabelFrame(parent, text=t("audio_title"), padding=8)
        self._register_text(audio_frame, "audio_title")
        self.audio_frame = audio_frame
        audio_frame.pack(fill=X, pady=(10, 0))

        arow = ttk.Frame(audio_frame)
        arow.pack(fill=X)
        self.audio_media_label = self._label(arow, "audio_media_label")
        self.audio_media_label.pack(side=LEFT)
        self.audio_media = StringVar()
        ttk.Entry(arow, textvariable=self.audio_media, width=70).pack(side=LEFT, padx=4)
        self._button(arow, "use_selected", self._audio_use_selected).pack(side=LEFT)
        self._button(arow, "analyze_button", self._audio_analyze).pack(
            side=LEFT, padx=(8, 0))

        arow2 = ttk.Frame(audio_frame)
        arow2.pack(fill=X, pady=(6, 0))
        self._button(arow2, "instrumental_button", self._audio_instrumental).pack(side=LEFT)
        self._button(arow2, "voice_plus", lambda: self._audio_voice(1.6)).pack(
            side=LEFT, padx=4)
        self._button(arow2, "voice_minus", lambda: self._audio_voice(0.6)).pack(side=LEFT)
        self._button(arow2, "reduce_noise", self._audio_denoise).pack(side=LEFT, padx=4)
        self._button(arow2, "normalize_enhance", self._audio_enhance).pack(side=LEFT)

        # Campos editaveis: valores padrao ficam em _audio_defaults() e nunca
        # sao gravados em profiles.json.
        defaults_audio = _audio_defaults()
        arow3 = ttk.Frame(audio_frame)
        arow3.pack(fill=X, pady=(6, 0))
        fields = (
            ("audio_denoise", "audio_denoise_var", "denoise", 6),
            ("audio_voice_gain", "audio_voice_gain_var", "voice_gain", 6),
            ("audio_music_gain", "audio_music_gain_var", "music_gain", 6),
            ("audio_lufs", "audio_lufs_var", "lufs", 7),
        )
        for key, attr, default_key, width in fields:
            label = self._label(arow3, key)
            label.pack(side=LEFT)
            setattr(self, key + "_label", label)
            setattr(self, attr, StringVar(value=str(defaults_audio[default_key])))
            ttk.Entry(arow3, textvariable=getattr(self, attr), width=width).pack(
                side=LEFT, padx=(4, 12))

        self.audio_enhance_preset_label = self._label(arow3, "audio_enhance_preset")
        self.audio_enhance_preset_label.pack(side=LEFT)
        self.audio_enhance_preset_var = StringVar(
            value=str(defaults_audio["enhance_preset"]))
        ttk.Combobox(arow3, state="readonly", width=8,
                     textvariable=self.audio_enhance_preset_var,
                     values=["voz", "musica"]).pack(side=LEFT, padx=4)
        self.audio_normalize_var = BooleanVar(value=bool(defaults_audio["normalize"]))
        self.audio_normalize_label = self._register_text(
            ttk.Checkbutton(arow3, text=t("audio_normalize"),
                            variable=self.audio_normalize_var),
            "audio_normalize",
        )
        self.audio_normalize_label.pack(side=LEFT, padx=(12, 0))

        # Botao de cancelamento e progresso aparecem somente durante um job.
        self.audio_cancel_button = self._button(
            arow2, "cancel", self._audio_cancel)
        self.audio_progress = ttk.Progressbar(audio_frame, mode="indeterminate")
        self.audio_log = Text(
            audio_frame, height=4, wrap="word",
            font=_scaled_font(audio_frame, "Consolas", 9),
        )
        self.audio_log.pack(fill=X, pady=(6, 0))
        self._register_scale()

    # ---- escala visual (somente a fonte do log) -------------------------
    def _apply_audio_font(self) -> None:
        """Reaplica a fonte BASE escalada; widget destruido e ignorado."""
        log = getattr(self, "audio_log", None)
        if log is None:
            return
        try:
            if not log.winfo_exists():
                return
            log.configure(font=_scaled_font(log, "Consolas", 9))
        except Exception:
            pass

    def _register_scale(self) -> None:
        """Um callback por instancia, sem segurar a aba viva (weakref)."""
        if getattr(self, "_scale_registered", False):
            return
        provider = _ui_scale(self.audio_log)
        on_apply = getattr(provider, "on_apply", None)
        if not callable(on_apply):
            return
        tab_ref = weakref.ref(self)

        def _reapply():
            tab = tab_ref()
            if tab is not None:
                tab._apply_audio_font()

        on_apply(_reapply)
        self._scale_registered = True

    def refresh_language(self, _lang: str | None = None) -> None:
        """Reaplica os rotulos de audio sem alterar valores digitados."""
        for widget, key in list(self._language_widgets):
            try:
                if widget is None or not widget.winfo_exists():
                    continue
                widget.configure(text=t(key))
            except Exception:
                pass

    # ---- ferramentas de audio ------------------------------------------
    def _audio_notify(self, text: str) -> None:
        """Status + log pela fachada, sem exigir metodos opcionais do app."""
        setter = getattr(self.app, "set_status", None)
        if callable(setter):
            try:
                setter(str(text))
            except Exception:
                pass
        try:
            self._audio_append_log(str(text))
        except Exception:
            pass

    def _audio_use_selected(self) -> None:
        # A aba Producao pode ainda nao ter sido construida (abinas lazy).
        ensure = getattr(self.app, "_ensure_production", None)
        if callable(ensure):
            try:
                ensure()
            except Exception as exc:
                self._audio_notify(t("error_msg", exc=exc))
                return
        item = self.app.selected_batch_item()
        if item:
            self.audio_media.set(str(item))
            return
        media = self.app.media_path
        if media:
            self.audio_media.set(str(media))
            return
        # Sem midia selecionada: aviso explicito, sem preencher o campo com "".
        message = t("audio_no_media")
        messagebox.showinfo(APP_NAME, message)
        self._audio_notify(message)

    def _audio_path(self) -> Path | None:
        value = self.audio_media.get().strip()
        path = Path(value) if value else None
        if path is None or not path.is_file():
            messagebox.showinfo(APP_NAME, t("audio_no_media"))
            return None
        return path

    def _audio_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(VILM_APP_PATH)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _audio_command(self, args: list[str]) -> list[str]:
        # pythonw.exe nao tem saida redirecionavel; usar sempre o console.
        return [_job_python(), "-m", "lyrics_aligner", "audio", *args]

    def _audio_start(self, command: list[str]) -> None:
        """Dispara o job em thread separada e retorna imediatamente para a UI."""
        if self.audio_worker and self.audio_worker.is_alive():
            return
        self.audio_cancel_event.clear()
        self.audio_log.configure(state="normal")
        self.audio_log.delete("1.0", END)
        self.audio_log.insert(END, t("audio_processing") + "\n")
        self.audio_log.configure(state="disabled")
        self.audio_cancel_button.pack(side=LEFT, padx=(4, 0))
        self.audio_progress.pack(fill=X, pady=(6, 0))
        self.audio_progress.start(12)
        self.app.set_status(t("processing"))
        self.audio_worker = threading.Thread(
            target=self._audio_job, args=(command,), daemon=True
        )
        self.audio_worker.start()

    def _audio_job(self, command: list[str]) -> None:
        collected: list[str] = []
        try:
            result = run_job(
                command,
                env=self._audio_env(),
                cancel_requested=self.audio_cancel_event.is_set,
                on_output=lambda line: self.app.emit("audio_log", line),
            )
            collected.append(result.output)
        except JobCancelled:
            # Cancelamento explicito nao e erro: sem messagebox.
            self.app.emit("audio_cancelled", None)
            return
        except Exception as exc:
            collected.append(str(exc))
        self.app.emit("audio_done", "".join(collected)[-4000:])

    def _audio_finish(self, status: str | None = None) -> None:
        """Encerra o job de audio.

        Por padrao (``status=None``) restaura "Pronto", como nos fluxos
        complete/failed. O ramo de cancelamento passa o texto de "Cancelado"
        para que ele nao seja sobrescrito por "Pronto" aqui.
        """
        self.audio_progress.stop()
        self.audio_progress.pack_forget()
        self.audio_cancel_button.pack_forget()
        if not self.app.busy_any() and not self.app.production_busy():
            self.app.set_status(t("ready") if status is None else status)

    def _audio_cancel(self) -> None:
        self.audio_cancel_event.set()
        self.app.set_status(f"{t('cancelled')}…")

    def _audio_append_log(self, text: str) -> None:
        self.audio_log.configure(state="normal")
        self.audio_log.insert(END, text + "\n")
        self.audio_log.see(END)
        self.audio_log.configure(state="disabled")

    def _audio_fields(self) -> dict[str, object]:
        """Valores editaveis da secao de audio (defaults em codigo)."""
        defaults = _audio_defaults()
        fields = dict(defaults)
        for key, attr in (
            ("denoise", "audio_denoise_var"),
            ("voice_gain", "audio_voice_gain_var"),
            ("music_gain", "audio_music_gain_var"),
            ("lufs", "audio_lufs_var"),
            ("enhance_preset", "audio_enhance_preset_var"),
            ("normalize", "audio_normalize_var"),
        ):
            var = getattr(self, attr, None)
            if var is None:
                continue
            if key == "normalize":
                fields[key] = bool(var.get())
                continue
            value = str(var.get()).strip()
            if not value:
                continue
            if key == "enhance_preset":
                fields[key] = value if value in {"voz", "musica"} else defaults[key]
            else:
                fields[key] = value
        return fields

    def _audio_field_args(self) -> list[str]:
        """Flags comuns montadas a partir dos campos editaveis de audio."""
        fields = self._audio_fields()
        args = [
            "--denoise", str(fields["denoise"]),
            "--voice-gain", str(fields["voice_gain"]),
            "--lufs", str(fields["lufs"]),
            "--enhance", str(fields["enhance_preset"]),
            "--music-gain", str(fields["music_gain"]),
        ]
        if fields["normalize"]:
            args.append("--normalize")
        return args

    def _audio_output_paths(self, media: Path, suffix: str | None
                            ) -> tuple[Path, Path | None]:
        """Destino do audio: dentro do projeto quando existe, senao o legado.

        A entrada pode ser um ``.wav``: nesse caso o nome de saida e sempre
        diferente para nao sobrescrever a propria midia analisada.
        """
        root = self.app.project_root_for(media)
        tail = " - " + suffix if suffix else " - analise"
        if root is not None:
            audio_dir = root / "trabalho" / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            output = audio_dir / (media.stem + tail + ".wav")
            if output.name == media.name and output.parent == media.parent:
                output = audio_dir / (media.stem + tail + " - saida.wav")
            return output, audio_dir / "_work"
        output = media.with_name(media.stem + tail + ".wav")
        if output.name == media.name:
            output = media.with_name(media.stem + tail + " - saida.wav")
        return output, media.parent / "trabalho" / "audio"

    def _audio_run(self, args: list[str] | None = None, suffix: str = "audio") -> None:
        media = self._audio_path()
        if media is None:
            return
        output, work = self._audio_output_paths(media, suffix)
        flags = list(args) if args is not None else self._audio_field_args()
        command = [str(media), "-o", str(output)]
        if work is not None:
            command.extend(("--work", str(work)))
        self._audio_start(self._audio_command([*command, *flags]))

    def _audio_analyze(self) -> None:
        media = self._audio_path()
        if media is None:
            return
        output, work = self._audio_output_paths(media, None)
        flags = [*self._audio_field_args(), "--analyze"]
        command = [str(media), "-o", str(output)]
        if work is not None:
            command.extend(("--work", str(work)))
        self._audio_start(self._audio_command([*command, *flags]))

    def _audio_instrumental(self) -> None:
        self._audio_run(["--mode", "instrumental"], "instrumental")

    def _audio_voice(self, gain: float) -> None:
        fields = self._audio_fields()
        args = ["--mode", "mix", "--voice-gain", str(gain),
                "--music-gain", str(fields["music_gain"]),
                "--lufs", str(fields["lufs"])]
        if fields["normalize"]:
            args.append("--normalize")
        self._audio_run(args, "voz")

    def _audio_denoise(self) -> None:
        fields = self._audio_fields()
        args = ["--denoise", str(fields["denoise"])]
        if fields["normalize"]:
            args.append("--normalize")
        self._audio_run(args, "limpo")

    def _audio_enhance(self) -> None:
        self._audio_run(suffix="melhorado")


# Mesmo padrao dos demais tab modules: instancias fracas e hook unico, com
# try/except para nunca quebrar a retraducao por causa de uma aba fechada.
_AUDIO_TABS: WeakSet["AudioTab"] = WeakSet()


def _on_language_changed(lang: str) -> None:
    for tab in list(_AUDIO_TABS):
        try:
            tab.refresh_language(lang)
        except Exception:
            pass


on_language_changed(_on_language_changed)
