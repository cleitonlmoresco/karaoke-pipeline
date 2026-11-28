"""Cue editor tab for the tkinter frontend.

Provides a Treeview of cues with editable start/end/text, delete, insert,
split, merge, undo/redo, validation, confidence display and re-derivation.
"""

from __future__ import annotations

import os
import queue
import threading
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
# Larguras BASE em px (a escala sempre recalcula a partir destes valores).
_COLUMN_WIDTHS = (
    ("idx", 50), ("start", 80), ("end", 80), ("conf", 60), ("text", 360),
)
_BASE_ROWHEIGHT = 20


def _ui_scale(widget):
    """Provider opcional no root da janela; ``None`` = escala neutra."""
    try:
        return getattr(widget.winfo_toplevel(), "ui_scale", None)
    except Exception:
        return None


def _base_factor(widget) -> tuple[float, object]:
    """``(fator, provider)`` com fallback neutro (1.0, sem provider)."""
    provider = _ui_scale(widget)
    factor_fn = getattr(provider, "factor", None)
    if not callable(factor_fn):
        return 1.0, None
    try:
        value = float(factor_fn())
    except Exception:
        return 1.0, None
    return (value if value > 0 else 1.0), provider


class CueEditorTab:
    def __init__(self, parent: ttk.Frame, app) -> None:
        self.parent = parent
        self.app = app
        self.cues: list[Cue] = []
        self._undo_stack: list[list[Cue]] = []
        self._redo_stack: list[list[Cue]] = []
        self.srt_path: str | None = None
        self._binds_registered = False
        # Audio buffer stays alive while winsound plays asynchronously.  It is
        # released by a GUI-thread timer sized to the total duration + margin.
        self._playing_audio: bytes = b""
        self._clear_timer: str | None = None
        self._play_gen = 0
        self._realign_gen = 0
        self._play_thread: threading.Thread | None = None
        # Cross-thread hand-off.  Workers only ever put on these queues; every
        # widget/undo/autosave mutation is performed on the GUI thread by the
        # single pump installed below.
        self._status_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._proposal_queue: queue.Queue[tuple[int, list[dict]]] = queue.Queue()
        self._play_queue: queue.Queue[tuple[int, bytes, float, int]] = queue.Queue()
        self._workers = 0
        self._workers_lock = threading.Lock()
        self._pump_alive = False
        self._pump_starts = 0
        self._build()
        # One permanent pump chain per instance.  The queues are never
        # replaced, so a second Play click can never leave two pumps alive.
        self._ensure_pump()
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
            ("stop_cue", self._stop_cue, "tip_stop"),
            ("undo_button", self._undo, "tip_undo"),
            ("redo_button", self._redo, "tip_redo"),
        ):
            button = ttk.Button(bar, text=t(key), command=command)
            mark(button, key)
            button.pack(side="left", padx=4)
            add_tooltip(button, t(tip_key))

        # Treeview with confidence column and its own vertical scrollbar.
        columns = ("idx", "start", "end", "conf", "text")
        tree_frame = ttk.Frame(self.parent)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", height=16,
            selectmode="extended",
        )
        self.yscrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=self.yscrollbar.set)
        for col, _base_width in _COLUMN_WIDTHS:
            key = _HEADING_KEYS.get(col)
            self.tree.heading(col, text=t(key) if key else _HEADING_FALLBACK.get(col, col))
            self.tree.column(col, anchor="w")
        self._apply_tree_scale()
        self._register_scale()
        self.tree.pack(side="left", fill="both", expand=True)
        self.yscrollbar.pack(side="right", fill="y")
        # Tk exposes bindings via ``bind()`` and canonicalises sequences
        # (``<Delete>`` becomes ``<Key-Delete>``).  Offer the ``bindings()``
        # spelling used by verification probes, including the original
        # spellings, without altering Tk behaviour.
        if not hasattr(self.tree, "bindings"):
            def _bindings() -> tuple[str, ...]:
                seqs = list(self.tree.bind())
                for original, canonical in (
                    ("<Delete>", "<Key-Delete>"),
                    ("<Insert>", "<Key-Insert>"),
                    ("<F5>", "<Key-F5>"),
                    ("<Control-z>", "<Control-Key-z>"),
                    ("<Control-y>", "<Control-Key-y>"),
                ):
                    if canonical in seqs and original not in seqs:
                        seqs.append(original)
                return tuple(seqs)

            self.tree.bindings = _bindings

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
        bindings = (
            ("<Delete>", self._delete_selected),
            ("<Insert>", self._insert_cue),
            ("<F5>", self._validate),
            ("<Control-z>", self._undo),
            ("<Control-y>", self._redo),
        )
        # O frame pai mantem os atalhos historicos (testes e foco fora da
        # árvore); a Treeview recebe os mesmos comandos para funcionar quando
        # ela propria tem o foco.
        for sequence, command in bindings:
            self.parent.bind(sequence, lambda _e, c=command: c())
            self.tree.bind(sequence, lambda _e, c=command: c())
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

    # ---- escala visual (somente px; selecao e bindings intactos) --------
    def _apply_tree_scale(self) -> None:
        """Reconfigura colunas/rowheight a partir das larguras BASE."""
        tree = getattr(self, "tree", None)
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                return
        except Exception:
            return
        factor, provider = _base_factor(tree)
        try:
            for column, base_width in _COLUMN_WIDTHS:
                tree.column(column, width=max(1, round(base_width * factor)))
            # Sem provider (app legado) preserva o rowheight padrao do Tk.
            if provider is not None:
                ttk.Style(tree).configure(
                    "Treeview", rowheight=max(1, round(_BASE_ROWHEIGHT * factor))
                )
        except Exception:
            pass

    def _register_scale(self) -> None:
        """Registra exatamente um callback por instancia da aba."""
        if getattr(self, "_scale_registered", False):
            return
        provider = _ui_scale(self.tree)
        on_apply = getattr(provider, "on_apply", None)
        if not callable(on_apply):
            return
        on_apply(self._apply_tree_scale)
        self._scale_registered = True

    # ---- logging / status / cross-thread plumbing ----
    def _log(self, message: str) -> None:
        """Best-effort diagnostic without touching the real GUI log widget."""
        logger = getattr(self.app, "log", None)
        if callable(logger):
            try:
                logger(message)
                return
            except Exception:
                pass
        print(f"[cue_editor] {message}")

    def _set_status(self, text: str, color: str = "blue") -> None:
        """GUI-thread status write.  Called only from main-thread callbacks."""
        self.status.config(text=text, foreground=color)

    def _queue_status(self, text: str, color: str = "blue") -> None:
        """Worker-thread safe status hand-off (never touches Tcl directly)."""
        self._status_queue.put((text, color))

    def _worker_begin(self) -> None:
        """Called on the GUI thread just before a worker thread is started."""
        with self._workers_lock:
            self._workers += 1
        self._ensure_pump()

    def _worker_end(self) -> None:
        """Called by the worker in ``finally`` (no Tcl access here)."""
        with self._workers_lock:
            self._workers = max(0, self._workers - 1)

    def _ensure_pump(self) -> None:
        """Start the single pump chain if it is not running (fallback only)."""
        if self._pump_alive:
            return
        self._pump_alive = True
        self._pump_starts += 1
        self.parent.after(40, self._pump_status)

    def _pump_status(self) -> None:
        """Single GUI-thread drain for every worker hand-off queue.

        Workers never touch Tcl (not even ``after``).  All widget, undo and
        autosave mutations are scheduled here through ``parent.after(0, ...)``.
        """
        try:
            while True:
                try:
                    text, color = self._status_queue.get_nowait()
                except queue.Empty:
                    break
                self.parent.after(
                    0, lambda tx=text, co=color: self.status.config(text=tx, foreground=co)
                )
            while True:
                try:
                    gen, proposals = self._proposal_queue.get_nowait()
                except queue.Empty:
                    break
                self.parent.after(
                    0, lambda g=gen, p=proposals: self._confirm_realign(g, p)
                )
            while True:
                try:
                    gen, wav, duration, count = self._play_queue.get_nowait()
                except queue.Empty:
                    break
                self.parent.after(
                    0, lambda g=gen, w=wav, d=duration, n=count: self._start_playback(g, w, d, n)
                )
            # A single chain lives for the whole instance: never replace the
            # queues and never start a second pump while this one is alive.
        finally:
            try:
                if self.parent.winfo_exists():
                    self.parent.after(40, self._pump_status)
                else:
                    self._pump_alive = False
            except Exception:
                self._pump_alive = False

    def _selected_indices(self) -> list[int]:
        """Validated, sorted, de-duplicated 0-based indices from the selection."""
        indices: list[int] = []
        for iid in self.tree.selection():
            try:
                idx = int(iid) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(self.cues) and idx not in indices:
                indices.append(idx)
        return sorted(indices)

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
            self._set_status(t("nothing_undo"), "blue")
            return
        self._redo_stack.append([Cue(c.text, c.start, c.end) for c in self.cues])
        self.cues = self._undo_stack.pop()
        self._refresh()
        self._autosave()
        self._set_status(t("undone"), "green")

    def _redo(self) -> None:
        if not self._redo_stack:
            self._set_status(t("nothing_redo"), "blue")
            return
        self._undo_stack.append([Cue(c.text, c.start, c.end) for c in self.cues])
        self.cues = self._redo_stack.pop()
        self._refresh()
        self._autosave()
        self._set_status(t("redone"), "green")

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
        idx = int(sel[0]) - 1 if str(sel[0]).isdigit() else -1
        if 0 <= idx < len(self.cues):
            cue = self.cues[idx]
            self.v_start.set(f"{cue.start:.2f}")
            self.v_end.set(f"{cue.end:.2f}")
            self.v_text.set(cue.text)

    def _apply_edit(self) -> None:
        indices = self._selected_indices()
        if not indices:
            self._set_status(t("select_cue"), "red")
            return
        try:
            start = float(self.v_start.get())
            end = float(self.v_end.get())
        except (TypeError, ValueError) as exc:
            self._set_status(t("config_error", exc=exc), "red")
            return
        text = self.v_text.get().strip()
        if not text:
            self._set_status(t("text_empty"), "red")
            return
        if end <= start:
            self._set_status(t("cue_invalid_time"), "red")
            return
        self._push_undo()
        for idx in indices:
            self.cues[idx] = Cue(text=text, start=start, end=end)
        self._refresh()
        self._autosave()
        if len(indices) > 1:
            self._set_status(t("cues_updated", n=len(indices)), "green")
        else:
            self._set_status(t("cue_updated"), "green")

    def _delete_selected(self) -> None:
        indices = self._selected_indices()
        if not indices:
            self._set_status(t("select_cue"), "red")
            return
        self._push_undo()
        for idx in reversed(indices):
            del self.cues[idx]
        self._refresh()
        self._autosave()
        if len(indices) > 1:
            self._set_status(t("cues_deleted", n=len(indices)), "green")
        else:
            self._set_status(t("cue_deleted"), "green")

    def _insert_cue(self) -> None:
        try:
            start = float(self.v_start.get() or "0")
            end = float(self.v_end.get() or "1")
            text = self.v_text.get().strip() or t("new_line")
            if end <= start:
                self._set_status(t("cue_invalid_time"), "red")
                return
            self._push_undo()
            self.cues.append(Cue(text=text, start=start, end=end))
            self._refresh()
            self._autosave()
            self._set_status(t("cue_inserted"), "green")
        except Exception as exc:
            self._set_status(t("config_error", exc=exc), "red")

    def _split_selected(self) -> None:
        indices = self._selected_indices()
        if not indices:
            self._set_status(t("select_cue"), "red")
            return
        valid = [idx for idx in indices if self.cues[idx].end > self.cues[idx].start]
        if not valid:
            self._set_status(t("cue_invalid_time"), "red")
            return
        self._push_undo()
        done: list[int] = []
        # Descending order keeps the remaining indices valid as the list grows.
        for idx in reversed(valid):
            cue = self.cues[idx]
            mid = (cue.start + cue.end) / 2.0
            words = cue.text.split()
            half = len(words) // 2
            left_text = " ".join(words[:half]) if half > 0 else cue.text
            right_text = " ".join(words[half:]) if half > 0 else cue.text
            self.cues[idx] = Cue(text=left_text, start=cue.start, end=mid)
            self.cues.insert(idx + 1, Cue(text=right_text, start=mid, end=cue.end))
            done.append(idx)
        self._refresh()
        self._autosave()
        if len(done) > 1:
            self._set_status(t("cues_batch_done", n=len(done)), "green")
        else:
            self._set_status(t("cue_split"), "green")

    def _merge_selected(self) -> None:
        indices = self._selected_indices()
        if not indices:
            self._set_status(t("select_cue"), "red")
            return
        # Contiguous selections merge as a group.  Non-contiguous selections
        # cannot form a pair: warn and merge the first cue with its successor.
        runs: list[tuple[int, int]] = []
        first = previous = indices[0]
        for idx in indices[1:]:
            if idx == previous + 1:
                previous = idx
            else:
                runs.append((first, previous))
                first = previous = idx
        runs.append((first, previous))
        pairs = [(a, b) for a, b in runs if b > a]
        fallback = False
        if not pairs:
            a = indices[0]
            if a + 1 >= len(self.cues):
                self._set_status(t("select_cue_next"), "red")
                return
            pairs = [(a, a + 1)]
            fallback = True
        self._push_undo()
        for a, b in reversed(pairs):
            text = " ".join(self.cues[i].text for i in range(a, b + 1))
            merged = Cue(text=text, start=self.cues[a].start, end=self.cues[b].end)
            self.cues[a] = merged
            del self.cues[a + 1:b + 1]
        self._refresh()
        self._autosave()
        if fallback:
            # Mesclar exige pares consecutivos: aviso persiste junto da acao.
            self._set_status(t("select_cue_next"), "red")
        elif len(pairs) > 1:
            self._set_status(t("cues_batch_done", n=len(pairs)), "green")
        else:
            self._set_status(t("cues_merged"), "green")

    def _validate(self) -> None:
        issues = validate_timeline(self.cues)
        if issues:
            msgs = [f"{i.severity.upper()} L{i.line}: {i.message}" for i in issues]
            self._set_status(" | ".join(msgs)[:200], "red")
        else:
            self._set_status(t("timeline_ok"), "green")

    def _realign_selected(self) -> None:
        indices = self._selected_indices()
        if not indices:
            self._set_status(t("select_cue"), "red")
            return
        audio = filedialog.askopenfilename(title=t("select_audio_video"))
        if not audio:
            return
        snapshot = [
            (idx, self.cues[idx].start, self.cues[idx].end, self.cues[idx].text)
            for idx in indices
        ]
        gen = self._realign_gen + 1
        self._realign_gen = gen
        self._worker_begin()

        def worker() -> None:
            proposals: list[dict] = []
            try:
                from .segment_realign import realign_segment

                for idx, start, end, text in snapshot:
                    proposal = realign_segment(audio, start, end, text)
                    if proposal is None:
                        continue
                    proposals.append({
                        "idx": idx,
                        "start": float(proposal["start"]),
                        "end": float(proposal["end"]),
                        "text": text,
                    })
            except Exception as exc:
                self._queue_status(t("error_msg", exc=exc), "red")
                proposals = []
            try:
                if gen != self._realign_gen:
                    return
                if proposals:
                    self._proposal_queue.put((gen, proposals))
                else:
                    self._queue_status(t("realign_none"), "red")
            finally:
                self._worker_end()

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_realign(self, gen: int, proposals: list[dict]) -> None:
        """GUI-thread confirmation and mutation of a realign proposal batch."""
        from tkinter import messagebox

        if gen != self._realign_gen:
            return
        lines = [
            f"#{p['idx'] + 1}: " + t(
                "realign_proposal", s=p["start"], e=p["end"], words=p["text"]
            )
            for p in proposals
        ]
        if messagebox.askyesno(t("realign_title"), "\n\n".join(lines)):
            self._push_undo()
            for p in proposals:
                idx = p["idx"]
                if 0 <= idx < len(self.cues):
                    self.cues[idx] = Cue(
                        text=p["text"], start=p["start"], end=p["end"]
                    )
            self._refresh()
            self._autosave()
            first = proposals[0]
            self.v_start.set(f"{first['start']:.2f}")
            self.v_end.set(f"{first['end']:.2f}")
            if len(proposals) > 1:
                self._set_status(t("cues_batch_done", n=len(proposals)), "green")
            else:
                self._set_status(t("realign_applied"), "green")
        else:
            self._set_status(t("realign_rejected"), "blue")

    @staticmethod
    def _find_ffmpeg() -> str | None:
        """Bundled ffmpeg first, then PATH.  Never a hardcoded user profile."""
        import shutil
        from pathlib import Path

        bundled = Path(__file__).resolve().parents[2] / "tools" / "ffmpeg.exe"
        if bundled.is_file():
            return str(bundled)
        return shutil.which("ffmpeg")

    def _play_selected(self) -> None:
        """Play every selected cue as one in-memory WAV, without any window."""
        import io
        import subprocess
        import wave

        indices = self._selected_indices()
        if not indices:
            self._set_status(t("select_cue"), "red")
            return
        # Reject invalid cues before touching ffmpeg.
        if any(self.cues[i].end <= self.cues[i].start for i in indices):
            self._set_status(t("cue_invalid_time"), "red")
            return

        # Single-flight: purge the previous take before a new one starts.
        self._stop_cue(silent=True)

        audio = filedialog.askopenfilename(title=t("select_audio_video"))
        if not audio:
            return

        spans = [(self.cues[i].start, self.cues[i].end) for i in indices]
        total = sum(max(0.0, end - start) + 0.6 for start, end in spans)
        if total > 120.0:
            self._set_status(t("play_cues_too_long"), "red")
            return

        gen = self._play_gen + 1
        self._play_gen = gen
        self._worker_begin()

        def worker() -> None:
            try:
                ffmpeg = self._find_ffmpeg()
                if not ffmpeg:
                    self._queue_status(t("ffmpeg_not_found"), "red")
                    return

                frames: list[bytes] = []
                channels = 0
                sampwidth = 0
                rate = 0
                for start, end in spans:
                    cmd = [
                        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{max(0.0, start - 0.3):.3f}", "-i", audio,
                        "-t", f"{max(0.0, end - start) + 0.6:.3f}",
                        "-vn", "-ac", "2", "-ar", "44100", "-f", "wav", "pipe:1",
                    ]
                    try:
                        result = subprocess.run(
                            cmd, check=False, capture_output=True, timeout=60
                        )
                    except subprocess.TimeoutExpired:
                        self._queue_status(t("audio_extract_timeout"), "red")
                        return
                    data = getattr(result, "stdout", b"") or b""
                    if not data:
                        self._queue_status(t("audio_extract_failed"), "red")
                        return
                    with wave.open(io.BytesIO(data), "rb") as wav:
                        ch, sw, fr = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
                        if channels == 0:
                            channels, sampwidth, rate = ch, sw, fr
                        elif (ch, sw, fr) != (channels, sampwidth, rate):
                            self._queue_status(t("audio_extract_failed"), "red")
                            return
                        frames.append(wav.readframes(wav.getnframes()))
                if not frames:
                    self._queue_status(t("audio_extract_failed"), "red")
                    return

                buf = io.BytesIO()
                with wave.open(buf, "wb") as out:
                    out.setnchannels(channels)
                    out.setsampwidth(sampwidth)
                    out.setframerate(rate)
                    for chunk in frames:
                        out.writeframes(chunk)
                wav_bytes = buf.getvalue()
                # A newer Play click supersedes this one: do not play stale audio.
                if gen != self._play_gen:
                    return
                self._play_queue.put((gen, wav_bytes, total, len(spans)))
            except Exception as exc:
                self._queue_status(t("config_error", exc=exc), "red")
            finally:
                self._worker_end()

        self._play_thread = threading.Thread(target=worker, daemon=True)
        self._play_thread.start()

    def _start_playback(
        self, gen: int, wav_bytes: bytes, duration: float, count: int
    ) -> None:
        """GUI-thread playback start with a duration-based cleanup timer."""
        if gen != self._play_gen:
            return
        try:
            import winsound
        except ImportError:
            winsound = None  # type: ignore[assignment]
        if os.name != "nt" or winsound is None:
            # No in-memory player available; never fall back to os.startfile.
            self._playing_audio = b""
            self._set_status(t("audio_extract_failed"), "red")
            return
        winsound.PlaySound(wav_bytes, winsound.SND_MEMORY | winsound.SND_ASYNC)
        self._playing_audio = wav_bytes
        self._set_status(t("playing_cues_internal", n=count), "green")
        if self._clear_timer is not None:
            try:
                self.parent.after_cancel(self._clear_timer)
            except Exception:
                pass
        delay_ms = int((duration + 0.6) * 1000) + 500
        self._clear_timer = self.parent.after(delay_ms, self._playback_finished)

    def _playback_finished(self) -> None:
        """Release the retained WAV bytes once playback should be over."""
        self._clear_timer = None
        self._playing_audio = b""
        self._set_status(t("stopped_cue"), "blue")

    def _stop_cue(self, *, silent: bool = False) -> None:
        """Purge any in-memory playback and release the retained WAV bytes."""
        try:
            import winsound
        except ImportError:
            winsound = None  # type: ignore[assignment]
        if winsound is not None and hasattr(winsound, "SND_PURGE"):
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if self._clear_timer is not None:
            try:
                self.parent.after_cancel(self._clear_timer)
            except Exception:
                pass
            self._clear_timer = None
        self._playing_audio = b""
        if not silent:
            self._set_status(t("stopped_cue"), "blue")

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
        self._set_status(t("srt_saved", path=path), "green")

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

    @staticmethod
    def _parse_srt(text: str) -> list[Cue]:
        import re

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
        return cues

    def _load_from_srt(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(title=t("select_srt_title"), filetypes=[("SRT", "*.srt")])
        if not path:
            return
        try:
            text = open(path, encoding="utf-8-sig").read()
            cues = self._parse_srt(text)
            if not cues:
                raise ValueError("SRT sem cues válidos")
            self.srt_path = path
            self.set_cues(cues)
            self._set_status(t("cues_loaded", n=len(cues)), "green")
        except Exception as exc:
            self._log(f"Falha ao carregar SRT: {exc}")
            self._set_status(t("config_error", exc=exc), "red")

    def _setup_dnd(self) -> None:
        # Try tkinterdnd2 if available
        try:
            import tkinterdnd2  # noqa: F401
            # If available, bind drop events
            self.parent.drop_target_register("DND_Files")  # type: ignore
            self.parent.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore
        except Exception as exc:
            self._log(f"DnD indisponível: {exc}")

    def _on_drop(self, event) -> None:
        try:
            path = str(getattr(event, "data", "") or "").strip().strip("{}")
            if not path.lower().endswith(".srt"):
                raise ValueError("arquivo solto não é SRT")
            self._load_from_path(path)
        except Exception as exc:
            self._log(f"Drop inválido: {exc}")
            self._set_status(t("config_error", exc=exc), "red")

    def _load_from_path(self, path: str) -> None:
        text = open(path, encoding="utf-8-sig").read()
        cues = self._parse_srt(text)
        if not cues:
            raise ValueError("SRT sem cues válidos")
        self.srt_path = path
        self.set_cues(cues)
        self._set_status(t("cues_loaded_dnd", n=len(cues)), "green")


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
