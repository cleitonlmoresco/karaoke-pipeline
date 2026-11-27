"""Cue editor tab for the tkinter frontend.

Provides a Treeview of cues with editable start/end/text, delete, insert,
split, merge, undo/redo, validation, confidence display and re-derivation.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, ttk
from weakref import WeakSet

from .i18n import find_key, mark, on_language_changed, t
from .models import Cue
from .timeline_validation import has_errors, validate_timeline
from .tk_tooltip import add_tooltip

# Treeview columns whose captions are language dependent.  "idx" and "conf"
# stay as stable abbreviations: no existing i18n key covers them and inventing
# new keys is out of scope for this task.
_HEADING_KEYS = {
    "idx": None,
    "start": "start_label",
    "end": "end_label",
    "conf": None,
    "text": "field_text",
}
_HEADING_FALLBACK = {"idx": "IDX", "conf": "CONF"}


class CueEditorTab:
    def __init__(self, parent: ttk.Frame, app) -> None:
        self.parent = parent
        self.app = app
        self.cues: list[Cue] = []
        self._undo_stack: list[list[Cue]] = []
        self._redo_stack: list[list[Cue]] = []
        self.srt_path: str | None = None
        self._binds_registered = False
        self._build()
        _CUE_TABS.add(self)

    def _build(self) -> None:
        bar = ttk.Frame(self.parent)
        bar.pack(fill="x", padx=8, pady=6)
        for key, command, tip_key in (
            ("load_srt", self._load_from_srt, "tip_load_cues"),
            ("delete_selected", self._delete_selected, "tip_delete_cue"),
            ("insert_cue", self._insert_cue, "tip_insert_cue"),
            ("split_cue", self._split_selected, "tip_split_cue"),
            ("merge_next", self._merge_selected, "tip_merge_cue"),
            ("validate", self._validate, "tip_validate"),
            ("realign_cue", self._realign_selected, "tip_realign"),
            ("play_cue", self._play_selected, "tip_play"),
            ("undo_button", self._undo, "tip_undo"),
            ("redo_button", self._redo, "tip_redo"),
        ):
            button = ttk.Button(bar, text=t(key), command=command)
            mark(button, key)
            button.pack(side="left", padx=4)
            add_tooltip(button, t(tip_key))

        # Treeview with confidence column
        columns = ("idx", "start", "end", "conf", "text")
        self.tree = ttk.Treeview(self.parent, columns=columns, show="headings", height=16)
        for col, width in zip(columns, (50, 80, 80, 60, 360)):
            key = _HEADING_KEYS.get(col)
            self.tree.heading(col, text=t(key) if key else _HEADING_FALLBACK.get(col, col))
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=8, pady=6)

        # Tag colors for confidence
        self.tree.tag_configure("high", background="#E8F5E9")
        self.tree.tag_configure("med", background="#FFF9C4")
        self.tree.tag_configure("low", background="#FFCDD2")

        form = ttk.LabelFrame(self.parent, text=t("edit_cue"))
        mark(form, "edit_cue")
        form.pack(fill="x", padx=8, pady=6)
        lbl_start = ttk.Label(form, text=t("start_label"))
        mark(lbl_start, "start_label")
        lbl_start.grid(row=0, column=0, padx=4, pady=2)
        self.v_start = tk.StringVar()
        ttk.Entry(form, textvariable=self.v_start, width=10).grid(row=0, column=1, padx=4, pady=2)
        lbl_end = ttk.Label(form, text=t("end_label"))
        mark(lbl_end, "end_label")
        lbl_end.grid(row=0, column=2, padx=4, pady=2)
        self.v_end = tk.StringVar()
        ttk.Entry(form, textvariable=self.v_end, width=10).grid(row=0, column=3, padx=4, pady=2)
        lbl_text = ttk.Label(form, text=t("field_text"))
        mark(lbl_text, "field_text")
        lbl_text.grid(row=0, column=4, padx=4, pady=2)
        self.v_text = tk.StringVar()
        ttk.Entry(form, textvariable=self.v_text, width=40).grid(row=0, column=5, padx=4, pady=2)
        b_app = ttk.Button(form, text=t("apply"), command=self._apply_edit)
        mark(b_app, "apply")
        b_app.grid(row=0, column=6, padx=4, pady=2)
        add_tooltip(b_app, t("tip_apply_cue"))

        self.status = ttk.Label(self.parent, text="", foreground="blue")
        self.status.pack(padx=8, pady=4, anchor="w")

        # Atalhos e DnD na primeira abertura, sem depender do hook de idioma.
        self._install_bindings()

    def _install_bindings(self) -> None:
        """Registra atalhos, DnD e selecao uma unica vez por instancia."""
        if getattr(self, "_binds_registered", False):
            return
        self.parent.bind("<Delete>", lambda e: self._delete_selected())
        self.parent.bind("<Insert>", lambda e: self._insert_cue())
        self.parent.bind("<F5>", lambda e: self._validate())
        self.parent.bind("<Control-z>", lambda e: self._undo())
        self.parent.bind("<Control-y>", lambda e: self._redo())
        self._setup_dnd()
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self._binds_registered = True

    def _apply_headings(self) -> None:
        """Reapply translated Treeview headings (not backed by a text option)."""
        for col, key in _HEADING_KEYS.items():
            if key:
                self.tree.heading(col, text=t(key))
        # O hook de idioma tambem garante os atalhos; a guarda evita duplica-los.
        self._install_bindings()

    # ---- data ----
    def set_cues(self, cues: list[Cue]) -> None:
        self._push_undo()
        self.cues = list(cues)
        self._refresh()

    def _push_undo(self) -> None:
        self._undo_stack.append([Cue(c.text, c.start, c.end) for c in self.cues])
        self._redo_stack.clear()
        # Limit history size
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)

    def _undo(self) -> None:
        if not self._undo_stack:
            self.status.config(text=t("nothing_undo"), foreground="blue")
            return
        self._redo_stack.append([Cue(c.text, c.start, c.end) for c in self.cues])
        self.cues = self._undo_stack.pop()
        self._refresh()
        self._autosave()
        self.status.config(text=t("undone"), foreground="green")

    def _redo(self) -> None:
        if not self._redo_stack:
            self.status.config(text=t("nothing_redo"), foreground="blue")
            return
        self._undo_stack.append([Cue(c.text, c.start, c.end) for c in self.cues])
        self.cues = self._redo_stack.pop()
        self._refresh()
        self._autosave()
        self.status.config(text=t("redone"), foreground="green")

    def _conf_tag(self, cue: Cue) -> str:
        conf = getattr(cue, "confidence", None)
        if conf is None:
            return ""
        if conf >= 0.75:
            return "high"
        if conf >= 0.45:
            return "med"
        return "low"

    def _refresh(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for i, cue in enumerate(self.cues, start=1):
            conf = getattr(cue, "confidence", None)
            conf_str = f"{conf:.2f}" if conf is not None else "-"
            tag = self._conf_tag(cue)
            self.tree.insert("", "end", iid=str(i), values=(i, f"{cue.start:.2f}", f"{cue.end:.2f}", conf_str, cue.text), tags=(tag,))

    def _on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0]) - 1
        if 0 <= idx < len(self.cues):
            cue = self.cues[idx]
            self.v_start.set(f"{cue.start:.2f}")
            self.v_end.set(f"{cue.end:.2f}")
            self.v_text.set(cue.text)

    def _apply_edit(self) -> None:
        sel = self.tree.selection()
        if not sel:
            self.status.config(text=t("select_cue"), foreground="red")
            return
        idx = int(sel[0]) - 1
        if not (0 <= idx < len(self.cues)):
            return
        try:
            start = float(self.v_start.get())
            end = float(self.v_end.get())
            text = self.v_text.get().strip()
            if not text:
                raise ValueError(t("text_empty"))
            if end < start:
                raise ValueError(t("end_before_start"))
            self._push_undo()
            self.cues[idx] = Cue(text=text, start=start, end=end)
            self._refresh()
            self._autosave()
            self.status.config(text=t("cue_updated"), foreground="green")
        except Exception as exc:
            self.status.config(text=t("config_error", exc=exc), foreground="red")

    def _delete_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            self.status.config(text=t("select_cue"), foreground="red")
            return
        idx = int(sel[0]) - 1
        if 0 <= idx < len(self.cues):
            self._push_undo()
            del self.cues[idx]
            self._refresh()
            self._autosave()
            self.status.config(text=t("cue_deleted"), foreground="green")

    def _insert_cue(self) -> None:
        try:
            start = float(self.v_start.get() or "0")
            end = float(self.v_end.get() or "1")
            text = self.v_text.get().strip() or t("new_line")
            if end < start:
                raise ValueError(t("end_before_start"))
            self._push_undo()
            self.cues.append(Cue(text=text, start=start, end=end))
            self._refresh()
            self._autosave()
            self.status.config(text=t("cue_inserted"), foreground="green")
        except Exception as exc:
            self.status.config(text=t("config_error", exc=exc), foreground="red")

    def _split_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            self.status.config(text=t("select_cue"), foreground="red")
            return
        idx = int(sel[0]) - 1
        if not (0 <= idx < len(self.cues)):
            return
        cue = self.cues[idx]
        mid = (cue.start + cue.end) / 2.0
        words = cue.text.split()
        half = len(words) // 2
        left_text = " ".join(words[:half]) if half > 0 else cue.text
        right_text = " ".join(words[half:]) if half > 0 else cue.text
        self._push_undo()
        self.cues[idx] = Cue(text=left_text, start=cue.start, end=mid)
        self.cues.insert(idx + 1, Cue(text=right_text, start=mid, end=cue.end))
        self._refresh()
        self._autosave()
        self.status.config(text=t("cue_split"), foreground="green")

    def _merge_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            self.status.config(text=t("select_cue"), foreground="red")
            return
        idx = int(sel[0]) - 1
        if not (0 <= idx < len(self.cues) - 1):
            self.status.config(text=t("select_cue_next"), foreground="red")
            return
        self._push_undo()
        a = self.cues[idx]
        b = self.cues[idx + 1]
        merged = Cue(text=a.text + " " + b.text, start=a.start, end=b.end)
        self.cues[idx] = merged
        del self.cues[idx + 1]
        self._refresh()
        self._autosave()
        self.status.config(text=t("cues_merged"), foreground="green")

    def _validate(self) -> None:
        issues = validate_timeline(self.cues)
        if issues:
            msgs = [f"{i.severity.upper()} L{i.line}: {i.message}" for i in issues]
            self.status.config(text=" | ".join(msgs)[:200], foreground="red")
        else:
            self.status.config(text=t("timeline_ok"), foreground="green")

    def _realign_selected(self) -> None:
        import threading
        from tkinter import filedialog, messagebox

        sel = self.tree.selection()
        if not sel:
            self.status.config(text=t("select_cue"), foreground="red")
            return
        idx = int(sel[0]) - 1
        if not (0 <= idx < len(self.cues)):
            return
        cue = self.cues[idx]
        audio = filedialog.askopenfilename(title=t("select_audio_video"))
        if not audio:
            return

        def worker():
            try:
                from .segment_realign import realign_segment

                proposal = realign_segment(audio, cue.start, cue.end, cue.text)
                if proposal is None:
                    self.status.config(text=t("realign_none"), foreground="red")
                    return
                msg = t(
                    "realign_proposal",
                    s=proposal["start"],
                    e=proposal["end"],
                    words=" ".join(w["text"] for w in proposal["words"]),
                )
                apply = messagebox.askyesno(t("realign_title"), msg)
                if apply:
                    self._push_undo()
                    self.cues[idx] = Cue(text=cue.text, start=proposal["start"], end=proposal["end"])
                    self._refresh()
                    self._autosave()
                    self.v_start.set(f"{proposal['start']:.2f}")
                    self.v_end.set(f"{proposal['end']:.2f}")
                    self.status.config(text=t("realign_applied"), foreground="green")
                else:
                    self.status.config(text=t("realign_rejected"), foreground="blue")
            except Exception as exc:
                self.status.config(text=t("error_msg", exc=exc), foreground="red")

        threading.Thread(target=worker, daemon=True).start()

    def _play_selected(self) -> None:
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        from tkinter import filedialog

        sel = self.tree.selection()
        if not sel:
            self.status.config(text=t("select_cue"), foreground="red")
            return
        idx = int(sel[0]) - 1
        if not (0 <= idx < len(self.cues)):
            return
        cue = self.cues[idx]
        audio = filedialog.askopenfilename(title=t("select_audio_video"))
        if not audio:
            return

        def worker():
            try:
                tmp = Path(tempfile.gettempdir()) / f"vilm_cue_{idx}.wav"
                ffmpeg = "ffmpeg"
                # find ffmpeg
                import shutil
                ff = shutil.which("ffmpeg")
                if not ff:
                    ff = r"C:\Users\IEAD Midia Canarana\AppData\Local\VilmPipeline\tools\ffmpeg.exe"
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
                       "-ss", f"{max(0, cue.start - 0.3):.3f}", "-i", audio,
                       "-t", f"{cue.end - cue.start + 0.6:.3f}",
                       "-vn", "-acodec", "pcm_s16le", str(tmp)]
                subprocess.run(cmd, check=False, capture_output=True)
                if tmp.is_file():
                    os.startfile(str(tmp))
                    self.status.config(text=t("playing_cue", n=idx + 1), foreground="green")
                else:
                    self.status.config(text=t("audio_extract_failed"), foreground="red")
            except Exception as exc:
                self.status.config(text=t("error_msg", exc=exc), foreground="red")

        import threading
        threading.Thread(target=worker, daemon=True).start()


    def _save_srt_as(self) -> None:
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            title=t("save_srt_title"),
            defaultextension=".srt",
            filetypes=[("SRT", "*.srt")],
            initialfile="lyrics.srt",
        )
        if not path:
            return
        self._write_srt(path)
        self.srt_path = path
        self.status.config(text=t("srt_saved", path=path), foreground="green")

    def _write_srt(self, path: str) -> None:
        from .srt import render_srt
        from pathlib import Path
        Path(path).write_text(render_srt(self.cues), encoding="utf-8-sig")

    def _project_srt_path(self) -> str | None:
        """SRT do projeto pela fachada da casca, quando ela existir."""
        getter = getattr(self.app, "project_srt", None)
        if not callable(getter):
            return None
        try:
            value = getter()
        except Exception:
            return None
        if value is None:
            return None
        try:
            return str(os.fspath(value)) or None
        except TypeError:
            return None

    def _autosave_path(self) -> str | None:
        # A fachada do projeto e a fonte preferida; o caminho do dialogo
        # permanece como fallback quando a casca ainda nao a oferece.
        return self._project_srt_path() or self.srt_path

    def _autosave(self) -> None:
        # Safe autosave: only when an explicit SRT path is known.
        path = self._autosave_path()
        if not path:
            return
        try:
            self._write_srt(path)
        except Exception:
            pass
    def _load_from_srt(self) -> None:
        from tkinter import filedialog
        import re

        path = filedialog.askopenfilename(title=t("select_srt_title"), filetypes=[("SRT", "*.srt")])
        if not path:
            return
        self.srt_path = path
        text = open(path, encoding="utf-8-sig").read()
        pattern = re.compile(
            r"(\d+)\n(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\n(.*?)(?=\n\d+\n|\Z)",
            re.DOTALL,
        )
        cues: list[Cue] = []
        for m in pattern.finditer(text):
            def to_sec(h, m_, s, ms):
                return int(h) * 3600 + int(m_) * 60 + int(s) + int(ms) / 1000.0

            start = to_sec(m.group(2), m.group(3), m.group(4), m.group(5))
            end = to_sec(m.group(6), m.group(7), m.group(8), m.group(9))
            body = m.group(10).strip().replace("\n", " ")
            if body:
                cues.append(Cue(text=body, start=start, end=end))
        self.set_cues(cues)
        self.status.config(text=t("cues_loaded", n=len(cues)), foreground="green")

    def _setup_dnd(self) -> None:
        # Try tkinterdnd2 if available
        try:
            import tkinterdnd2  # noqa: F401
            # If available, bind drop events
            self.parent.drop_target_register("DND_Files")  # type: ignore
            self.parent.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore
        except Exception:
            pass

    def _on_drop(self, event) -> None:
        try:
            path = event.data.strip().strip("{}")
            if path.lower().endswith(".srt"):
                self._load_from_path(path)
        except Exception:
            pass

    def _load_from_path(self, path: str) -> None:
        import re

        text = open(path, encoding="utf-8-sig").read()
        pattern = re.compile(
            r"(\d+)\n(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})\n(.*?)(?=\n\d+\n|\Z)",
            re.DOTALL,
        )
        cues: list[Cue] = []
        for m in pattern.finditer(text):
            def to_sec(h, m_, s, ms):
                return int(h) * 3600 + int(m_) * 60 + int(s) + int(ms) / 1000.0

            start = to_sec(m.group(2), m.group(3), m.group(4), m.group(5))
            end = to_sec(m.group(6), m.group(7), m.group(8), m.group(9))
            body = m.group(10).strip().replace("\n", " ")
            if body:
                cues.append(Cue(text=body, start=start, end=end))
        self.srt_path = path
        self.set_cues(cues)
        self.status.config(text=t("cues_loaded_dnd", n=len(cues)), foreground="green")


# Module level hook: one registration per import, weak references to instances.
_CUE_TABS: WeakSet["CueEditorTab"] = WeakSet()


def _on_language_changed(_lang: str) -> None:
    for tab in list(_CUE_TABS):
        try:
            if not tab.tree.winfo_exists():
                continue
            tab._apply_headings()
            if tab.status.winfo_exists():
                current = str(tab.status.cget("text"))
                key = find_key(current)
                if key:
                    tab.status.config(text=t(key))
        except Exception:
            pass


on_language_changed(_on_language_changed)
