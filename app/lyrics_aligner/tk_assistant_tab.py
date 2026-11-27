"""Creation assistant tab using OpenCode CLI."""

from __future__ import annotations

from .i18n import mark, on_language_changed, t

import threading
import tkinter as tk
from tkinter import ttk
from weakref import WeakSet

from .opencode_bridge import find_opencode, list_models, run_prompt
from .tk_tooltip import add_tooltip


class AssistantTab:
    def __init__(self, parent: ttk.Frame, app) -> None:
        self.parent = parent
        self.app = app
        # Cache the CLI lookup: the language hook only rewrites the label,
        # it never probes the system again.
        self._oc = find_opencode()
        self._cli_label: ttk.Label | None = None
        self._build()
        _ASSISTANT_TABS.add(self)

    def _cli_text(self) -> str:
        """Full label text; the fixed prefix is intentional in every language."""
        return f"OpenCode CLI: {self._oc if self._oc else t('opencode_not_found')}"

    def _build(self) -> None:
        info = ttk.LabelFrame(self.parent, text=t("assistant_title"))
        mark(info, "assistant_title")
        info.pack(fill="x", padx=8, pady=6)

        # The value after the fixed prefix is rebuilt by the language hook;
        # no user-editable state lives in this label.
        self._cli_label = ttk.Label(info, text=self._cli_text())
        self._cli_label.pack(anchor="w", padx=4, pady=2)

        model_frame = ttk.Frame(info)
        model_frame.pack(fill="x", padx=4, pady=2)
        lbl_model = ttk.Label(model_frame, text=t("model_label"))
        mark(lbl_model, "model_label")
        lbl_model.pack(side="left")
        self.v_model = tk.StringVar(value="")
        self.model_combo = ttk.Combobox(model_frame, textvariable=self.v_model, width=40)
        self.model_combo.pack(side="left", padx=4)
        b_lm = ttk.Button(model_frame, text=t("list_models"), command=self._load_models)
        mark(b_lm, "list_models")
        b_lm.pack(side="left", padx=4)
        add_tooltip(b_lm, t("tip_model_list"))

        # Ferramentas de letra
        tools_frame = ttk.LabelFrame(self.parent, text=t("lyrics_tools"))
        mark(tools_frame, "lyrics_tools")
        tools_frame.pack(fill="x", padx=8, pady=6)
        for key, command in (
            ("detect_refrains", self._detect_refrains),
            ("spell_check", self._spell_check),
            ("translate_en", lambda: self._translate("English")),
        ):
            button = ttk.Button(tools_frame, text=t(key), command=command)
            mark(button, key)
            button.pack(side="left", padx=4)

        prompt_frame = ttk.LabelFrame(self.parent, text=t("prompt_label"))
        mark(prompt_frame, "prompt_label")
        prompt_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.prompt_text = tk.Text(prompt_frame, height=6, wrap=tk.WORD)
        self.prompt_text.pack(fill="both", expand=True, padx=4, pady=4)

        btn = ttk.Frame(self.parent)
        btn.pack(fill="x", padx=8, pady=4)
        b_send = ttk.Button(btn, text=t("send"), command=self._run)
        mark(b_send, "send")
        b_send.pack(side="left", padx=4)
        add_tooltip(b_send, t("tip_send"))
        self.progress = ttk.Progressbar(btn, mode="indeterminate", length=120)
        self.progress.pack(side="left", padx=8)

        resp_frame = ttk.LabelFrame(self.parent, text=t("response_label"))
        mark(resp_frame, "response_label")
        resp_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.resp_text = tk.Text(resp_frame, height=12, state=tk.DISABLED, wrap=tk.WORD)
        self.resp_text.pack(fill="both", expand=True, padx=4, pady=4)

    def _load_models(self) -> None:
        models = list_models()
        self.model_combo["values"] = models
        if models and not self.v_model.get():
            self.v_model.set(models[0])

    @staticmethod
    def _lyrics_from(value: object) -> str:
        """Accept either a Text-like widget or a plain string from a facade."""
        if value is None:
            return ""
        getter = getattr(value, "get", None)
        if callable(getter):
            try:
                return str(getter("1.0", tk.END)).strip()
            except Exception:
                return ""
        return str(value).strip() if isinstance(value, str) else ""

    def _get_lyrics(self) -> str:
        """Ler a letra pela fachada da casca, sem exigir aba construida."""
        getter = getattr(self.app, "lyrics_text", None)
        if callable(getter):
            try:
                lyrics = self._lyrics_from(getter())
            except Exception:
                lyrics = ""
            if lyrics:
                return lyrics
        for name in ("pipeline_tab", "production_tab"):
            tab = getattr(self.app, name, None)
            if tab is None:
                continue
            widget = getattr(tab, "lyrics_text", None)
            if callable(widget) and not hasattr(widget, "get"):
                try:
                    widget = widget()
                except Exception:
                    continue
            lyrics = self._lyrics_from(widget)
            if lyrics:
                return lyrics
        return ""

    def _detect_refrains(self) -> None:
        lyrics = self._get_lyrics()
        if not lyrics:
            self._show(t("paste_pipeline_first"))
            return
        from .assistant_helpers import detect_repeated_verses, format_repeated_report
        from lyrics_aligner.models import Cue
        cues = [Cue(text=line, start=0, end=1) for line in lyrics.splitlines() if line.strip()]
        groups = detect_repeated_verses(cues)
        self._show(format_repeated_report(groups, cues))

    def _spell_check(self) -> None:
        lyrics = self._get_lyrics()
        if not lyrics:
            self._show(t("paste_pipeline_first"))
            return
        from .assistant_helpers import spell_check_prompt
        from .opencode_bridge import run_prompt
        prompt = spell_check_prompt(lyrics)

        def worker():
            try:
                resp = run_prompt(prompt, model=self.v_model.get() or None)
                self.app.root.after(0, lambda: self._show(resp))
            except Exception as exc:
                self.app.root.after(0, lambda: self._show(t("error_msg", exc=exc)))

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _translate(self, lang: str) -> None:
        lyrics = self._get_lyrics()
        if not lyrics:
            self._show(t("paste_pipeline_first"))
            return
        from .assistant_helpers import translate_lyrics_prompt
        from .opencode_bridge import run_prompt
        prompt = translate_lyrics_prompt(lyrics, lang)

        def worker():
            try:
                resp = run_prompt(prompt, model=self.v_model.get() or None)
                self.app.root.after(0, lambda: self._show(resp))
            except Exception as exc:
                self.app.root.after(0, lambda: self._show(t("error_msg", exc=exc)))

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _run(self) -> None:
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            return

        def worker() -> None:
            self.app.root.after(0, lambda: self.progress.start(10))
            try:
                resp = run_prompt(prompt, model=self.v_model.get() or None)
                self.app.root.after(0, lambda: self._show(resp))
            except Exception as exc:
                self.app.root.after(0, lambda: self._show(t("error_msg", exc=exc)))
            finally:
                self.app.root.after(0, lambda: self.progress.stop())

        threading.Thread(target=worker, daemon=True).start()

    def _show(self, text: str) -> None:
        self.resp_text.config(state=tk.NORMAL)
        self.resp_text.delete("1.0", tk.END)
        self.resp_text.insert(tk.END, text)
        self.resp_text.config(state=tk.DISABLED)


# Module level hook: the CLI label contains a fixed prefix, so it cannot be
# driven by a single i18n key.  Instances are held weakly and probed safely.
_ASSISTANT_TABS: WeakSet["AssistantTab"] = WeakSet()


def _on_language_changed(_lang: str) -> None:
    for tab in list(_ASSISTANT_TABS):
        try:
            if tab._cli_label is not None and tab._cli_label.winfo_exists():
                tab._cli_label.config(text=tab._cli_text())
        except Exception:
            pass


on_language_changed(_on_language_changed)
