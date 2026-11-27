from __future__ import annotations

from .i18n import mark, t

import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from . import process_runner
from . import profiles_api
from . import settings
from .process_runner import console_python
from .services import align as alignment_service
from .tk_tooltip import add_tooltip

# Raiz de ``app``: tk_pipeline_tab.py fica em <install>/app/lyrics_aligner/.
APP_PATH = Path(__file__).resolve().parents[1]

# Alias publicos: os servicos sao a unica via de alinhamento e de subprocesso.
# Os aliases permitem isolar testes substituindo tanto o simbolo deste modulo
# quanto o simbolo no modulo de origem.
generate_alignment = alignment_service.generate_alignment
_ORIGINAL_GENERATE_ALIGNMENT = generate_alignment
run_job = process_runner.run_job
_ORIGINAL_RUN_JOB = run_job


def _call_generate_alignment(**kwargs):
    """Chama o servico unico de alinhamento (com QC embutido)."""
    if generate_alignment is not _ORIGINAL_GENERATE_ALIGNMENT:
        return generate_alignment(**kwargs)
    return alignment_service.generate_alignment(**kwargs)


def _call_run_job(command, **kwargs):
    """Chama o runner de processos; a UI nunca usa subprocess diretamente."""
    if run_job is not _ORIGINAL_RUN_JOB:
        return run_job(command, **kwargs)
    return process_runner.run_job(command, **kwargs)


class PipelineTab:
    def __init__(self, parent: ttk.Frame, app, tools_only: bool = False) -> None:
        # Aceita as duas convencoes de chamada: (parent, app) historico e
        # (app, parent) usado na descricao das fachadas da Fase 3.
        if not hasattr(parent, "winfo_children") and hasattr(app, "winfo_children"):
            parent, app = app, parent
        self.parent = parent
        self.app = app
        # ``tools_only`` mounts only Bloco G (5 ferramentas) na aba Legendas:
        # sem midia, letra, alinhamento, burn, log ou progresso proprio.
        self.tools_only = bool(tools_only)
        self.cantado_path: Path | None = None
        self.playback_path: Path | None = None
        self._build()

    def _build(self) -> None:
        if self.tools_only:
            self._build_tools_only()
            return
        media_frame = ttk.LabelFrame(self.parent, text=t("media_section"))
        mark(media_frame, "media_section")
        media_frame.pack(fill="x", padx=8, pady=6)

        self.cantado_var = tk.StringVar(value=t("no_media"))
        # The API skips a StringVar whose value has been replaced by a media
        # path, so user selections survive a language change.
        mark(self.cantado_var, "no_media", kind="variable")
        lbl_cantado = ttk.Label(media_frame, text=t("cantado_label"))
        mark(lbl_cantado, "cantado_label")
        lbl_cantado.grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(media_frame, textvariable=self.cantado_var).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        b_selc = ttk.Button(media_frame, text=t("select_button"), command=self._select_cantado)
        mark(b_selc, "select_button")
        b_selc.grid(row=0, column=2, padx=4, pady=2)
        add_tooltip(b_selc, t("tip_select_cantado"))

        self.playback_var = tk.StringVar(value=t("no_media"))
        mark(self.playback_var, "no_media", kind="variable")
        lbl_playback = ttk.Label(media_frame, text=t("playback_label"))
        mark(lbl_playback, "playback_label")
        lbl_playback.grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Label(media_frame, textvariable=self.playback_var).grid(row=1, column=1, sticky="w", padx=4, pady=2)
        b_selp = ttk.Button(media_frame, text=t("select_button"), command=self._select_playback)
        mark(b_selp, "select_button")
        b_selp.grid(row=1, column=2, padx=4, pady=2)
        add_tooltip(b_selp, t("tip_select_playback"))

        lyrics_frame = ttk.LabelFrame(self.parent, text=t("lyrics_section"))
        mark(lyrics_frame, "lyrics_section")
        lyrics_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.lyrics_text = tk.Text(lyrics_frame, height=12, wrap=tk.WORD)
        self.lyrics_text.pack(fill="both", expand=True, padx=4, pady=4)

        ctrl = ttk.Frame(self.parent)
        ctrl.pack(fill="x", padx=8, pady=6)
        b_align = ttk.Button(ctrl, text=t("align_button"), command=self._run_align)
        mark(b_align, "align_button")
        b_align.pack(side="left", padx=4)
        add_tooltip(b_align, t("tip_align"))
        b_comb = ttk.Button(ctrl, text=t("preview_combine_button"), command=self._run_combine)
        mark(b_comb, "preview_combine_button")
        b_comb.pack(side="left", padx=4)
        add_tooltip(b_comb, t("tip_combine"))
        b_prev = ttk.Button(ctrl, text=t("preview_native_button"), command=self._run_native_preview)
        mark(b_prev, "preview_native_button")
        b_prev.pack(side="left", padx=4)
        add_tooltip(b_prev, t("tip_native_preview"))
        b_burn = ttk.Button(ctrl, text=t("burn_button"), command=self._run_burn)
        mark(b_burn, "burn_button")
        b_burn.pack(side="left", padx=4)
        add_tooltip(b_burn, t("tip_burn"))
        b_stage = ttk.Button(ctrl, text=t("stage_button"), command=self._run_staging)
        mark(b_stage, "stage_button")
        b_stage.pack(side="left", padx=4)
        add_tooltip(b_stage, t("tip_stage"))
        self.progress = ttk.Progressbar(ctrl, mode="indeterminate", length=200)
        self.progress.pack(side="left", padx=8)

        log_frame = ttk.LabelFrame(self.parent, text=t("log_section"))
        mark(log_frame, "log_section")
        log_frame.pack(fill="both", expand=True, padx=8, pady=6)
        self.log_text = tk.Text(log_frame, height=10, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

        tools_frame = ttk.LabelFrame(self.parent, text=t("tools_title"))
        mark(tools_frame, "tools_title")
        tools_frame.pack(fill="x", padx=8, pady=6)
        for key, command in (
            ("find_replace", self._find_replace),
            ("export_formats", self._export_formats),
            ("export_project", self._export_project),
            ("import_project", self._import_project),
        ):
            tool_button = ttk.Button(tools_frame, text=t(key), command=command)
            mark(tool_button, key)
            tool_button.pack(side="left", padx=4)

    def _build_tools_only(self) -> None:
        """Apenas o bloco de ferramentas (Bloco G), com os 5 botoes existentes."""
        tools_frame = ttk.LabelFrame(self.parent, text=t("tools_title"))
        mark(tools_frame, "tools_title")
        tools_frame.pack(fill="x", padx=8, pady=6)
        for key, command in (
            ("find_replace", self._find_replace),
            ("export_formats", self._export_formats),
            ("export_project", self._export_project),
            ("import_project", self._import_project),
            ("stage_button", self._run_staging),
        ):
            tool_button = ttk.Button(tools_frame, text=t(key), command=command)
            mark(tool_button, key)
            tool_button.pack(side="left", padx=4)

    # ---- resolucao de projeto / perfil / caminhos ----
    def _preferences(self) -> dict:
        """Preferencias unificadas, sem gravar nada."""
        try:
            return settings.load_settings(getattr(self.app, "settings_path", None)) or {}
        except Exception:
            return {}

    def _settings_snapshot(self) -> dict:
        """Ajustes da UI pela fachada da casca, com fallback defensivo."""
        for name in ("settings", "settings_tab"):
            source = getattr(self.app, name, None)
            if source is None:
                continue
            try:
                if callable(source) and not hasattr(source, "collect"):
                    value = source()
                elif hasattr(source, "collect"):
                    value = source.collect()
                else:
                    continue
            except Exception:
                continue
            if isinstance(value, dict):
                return value
        return {}

    def _active_profile(self) -> str:
        """Perfil ativo: ajustes da UI > settings > profiles.json."""
        ui = self._settings_snapshot()
        for candidate in (ui.get("profile"), self._preferences().get("profile")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        try:
            profiles_path = getattr(self.app, "profiles_path", None)
            return str(profiles_api.get_active_profile(profiles_path) or "").strip()
        except Exception:
            return ""

    def _edited_cues(self) -> list:
        """Cues editados via fachada, sem exigir aba ja construida."""
        getter = getattr(self.app, "cues", None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = None
            if value:
                return list(value)
        tab = getattr(self.app, "cue_editor_tab", None)
        if tab is not None:
            cues = getattr(tab, "cues", None)
            if cues:
                return list(cues)
        return []

    def _set_edited_cues(self, cues: list) -> bool:
        """Devolve os cues ao editor pela fachada, quando disponivel."""
        setter = getattr(self.app, "set_cues", None)
        if callable(setter):
            try:
                setter(cues)
                return True
            except Exception:
                pass
        tab = getattr(self.app, "cue_editor_tab", None)
        if tab is not None and hasattr(tab, "set_cues"):
            try:
                tab.set_cues(cues)
                return True
            except Exception:
                pass
        return False

    def _profile_overrides(self, profile_name: str) -> dict:
        """Ajustes explicitos do perfil ativo (o CLI tambem aplica o perfil)."""
        align = {}
        if profile_name:
            try:
                align = profiles_api.profile_settings(profile_name).get("alignment") or {}
            except Exception:
                align = {}
        if not align:
            align = self._settings_snapshot().get("alignment") or {}
        return {
            "max_chars": int(align.get("max_chars", 30)),
            "auto_segment": str(align.get("mode", "auto")) != "manual",
            "language": str(align.get("language", "auto")),
            "model_name": str(align.get("model", "small")),
            "separate": bool(align.get("separate", True)),
            "lead_ms": int(align.get("lead_ms", 600)),
            "offset_seconds": float(align.get("offset_seconds", 0.0) or 0.0),
            "range_start_seconds": float(align.get("range_start", 0.0) or 0.0),
            "range_end_seconds": align.get("range_end"),
            "end_pad_ms": int(align.get("end_pad_ms", 500)),
            "min_duration_ms": int(align.get("min_duration_ms", 500)),
            "max_duration_ms": int(align.get("max_duration_ms", 10000)),
            "min_gap_ms": int(align.get("min_gap_ms", 80)),
        }

    def _project_dir(self) -> Path | None:
        """Diretorio do projeto, quando houver; None no modo legado."""
        for attr in ("project_dir", "project_path", "current_project_dir"):
            value = getattr(self.app, attr, None)
            if isinstance(value, (str, Path)) and str(value).strip():
                candidate = Path(value)
                if candidate.is_dir():
                    return candidate
        if self.cantado_path is None:
            return None
        parent = self.cantado_path.parent
        for candidate in (parent, *list(parent.parents)[:3]):
            try:
                if (candidate / "projeto.json").is_file():
                    return candidate
                if (candidate / "letra.txt").is_file() or (
                    (candidate / "saida").is_dir() and (candidate / "trabalho").is_dir()
                ):
                    return candidate
            except OSError:
                continue
        root = self._preferences().get("projects_root")
        if isinstance(root, str) and root.strip():
            try:
                base = Path(root).resolve()
                relative = self.cantado_path.resolve().relative_to(base)
                top = base / relative.parts[0]
                if top.is_dir():
                    return top
            except (ValueError, OSError):
                pass
        return None

    def _paths(self) -> tuple[Path | None, Path, Path, Path]:
        """(projeto, saida, trabalho, legado) para a midia selecionada."""
        media = self.cantado_path
        legacy = media.parent / f"{media.stem}_lyrics"
        project = self._project_dir()
        if project is not None:
            return project, project / "saida", project / "trabalho" / "cli", legacy
        return None, media.parent / "saida", legacy / "trabalho", legacy

    def _select_cantado(self) -> None:
        path = filedialog.askopenfilename(title=t("select_cantado_title"))
        if path:
            self.cantado_path = Path(path)
            self.cantado_var.set(self.cantado_path.name)

    def _select_playback(self) -> None:
        path = filedialog.askopenfilename(title=t("select_playback_title"))
        if path:
            self.playback_path = Path(path)
            self.playback_var.set(self.playback_path.name)

    def _log(self, msg: str) -> None:
        if self.tools_only or not hasattr(self, "log_text"):
            # No modo ferramentas o log pertence a casca (log_text eager).
            logger = getattr(self.app, "log", None)
            if callable(logger):
                self.app.root.after(0, lambda: logger(msg))
            return
        self.app.root.after(0, lambda: self._append_log(msg))

    def _append_log(self, msg: str) -> None:
        text_widget = getattr(self, "log_text", None)
        if text_widget is None:
            return
        text_widget.config(state=tk.NORMAL)
        text_widget.insert(tk.END, msg + "\n")
        text_widget.see(tk.END)
        text_widget.config(state=tk.DISABLED)

    def _progress_start(self) -> None:
        progress = getattr(self, "progress", None)
        if progress is not None:
            self.app.root.after(0, lambda: progress.start(10))

    def _progress_stop(self) -> None:
        progress = getattr(self, "progress", None)
        if progress is not None:
            self.app.root.after(0, lambda: progress.stop())

    def _lyrics_widget(self):
        """Fonte de letra: widget proprio (modo completo) ou fachada da casca."""
        widget = getattr(self, "lyrics_text", None)
        if widget is not None:
            return widget
        getter = getattr(self.app, "lyrics_text", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
        return widget

    def _run_align(self) -> None:
        if self.cantado_path is None:
            self._log(t("select_cantado_first"))
            return
        lyrics = self.lyrics_text.get("1.0", tk.END).strip()
        if not lyrics:
            self._log(t("paste_lyrics"))
            return
        media = self.cantado_path
        profile_name = self._active_profile()
        overrides = self._profile_overrides(profile_name)

        def worker() -> None:
            self._progress_start()
            try:
                project, saida, work, legacy = self._paths()
                srt_path = (saida if project is not None else legacy) / "lyrics.srt"
                srt_path.parent.mkdir(parents=True, exist_ok=True)
                work.mkdir(parents=True, exist_ok=True)
                result = _call_generate_alignment(
                    project_dir=project or media.parent,
                    media=media,
                    lyrics_text=lyrics,
                    profile_name=profile_name,
                    output=srt_path,
                    work=work,
                    overrides=overrides,
                    progress=self._log,
                )
                for warning in (result or {}).get("warnings") or []:
                    self._log(f"{t('qc_preflight')}: {warning}")
                cues = (result or {}).get("cues")
                if cues is None and srt_path.is_file():
                    try:
                        from .srt import parse_srt

                        cues = parse_srt(srt_path.read_text(encoding="utf-8-sig"))
                    except Exception:
                        cues = []
                self._log(t("align_done", n=len(cues or [])))
                self._log(t("align_srt", path=srt_path))
            except Exception as exc:
                self._log(t("error_msg", exc=exc))
            finally:
                self._progress_stop()

        threading.Thread(target=worker, daemon=True).start()

    def _run_burn(self) -> None:
        if self.cantado_path is None:
            self._log(t("select_cantado_first"))
            return
        lyrics = self.lyrics_text.get("1.0", tk.END).strip()
        if not lyrics:
            self._log(t("paste_lyrics"))
            return
        media = self.cantado_path
        playback = self.playback_path
        profile_name = self._active_profile()
        if not profile_name:
            self._log(t("profile_none"))
            return

        edited_cues = self._edited_cues() or None
        if edited_cues:
            self._log(t("using_editor_cues", n=len(edited_cues)))

        def worker() -> None:
            self._progress_start()
            burn_lock = None
            try:
                from lyrics_aligner.burn_lock import BurnLock

                burn_lock = BurnLock(timeout=300.0)
                if not burn_lock.acquire():
                    self._log(t("burn_lock_error"))
                    return

                _project, saida, work, _legacy = self._paths()
                saida.mkdir(parents=True, exist_ok=True)
                work.mkdir(parents=True, exist_ok=True)
                self._log(t("step_prepare"))

                lyrics_path = work / "lyrics-input.txt"
                lyrics_path.write_text(lyrics, encoding="utf-8-sig")
                if edited_cues is not None:
                    try:
                        from .srt import render_srt

                        (work / "lyrics.srt").write_text(
                            render_srt(edited_cues), encoding="utf-8-sig"
                        )
                    except Exception as exc:
                        self._log(t("error_msg", exc=exc))

                video = saida / "video_cantado.mp4"
                command = [
                    str(console_python(sys.executable)), "-m", "lyrics_aligner",
                    "make", str(media), str(lyrics_path),
                    "-o", str(video), "--profile", profile_name,
                    "--work", str(work),
                ]
                if playback is not None:
                    command.extend(("--playback", str(playback)))
                env = os.environ.copy()
                env["PYTHONPATH"] = str(APP_PATH)
                env["PYTHONIOENCODING"] = "utf-8"

                self._log(t("step_burn"))
                result = _call_run_job(command, env=env, on_output=self._log)
                if getattr(result, "returncode", 0) != 0:
                    detail = str(getattr(result, "output", "") or "make falhou").strip()
                    self._log(t("error_msg", exc=detail))
                    return
                self._log(t("completed"))
            except Exception as exc:
                self._log(t("error_msg", exc=exc))
            finally:
                try:
                    if burn_lock is not None:
                        burn_lock.release()
                except Exception:
                    pass
                self._progress_stop()

        threading.Thread(target=worker, daemon=True).start()

    def _run_combine(self) -> None:
        from .publish import combine_side_by_side
        import os

        a = filedialog.askopenfilename(title=t("select_sung_video"), filetypes=[("MP4", "*.mp4")])
        if not a:
            return
        b = filedialog.askopenfilename(title=t("select_playback_video"), filetypes=[("MP4", "*.mp4")])
        if not b:
            return
        out = filedialog.asksaveasfilename(title=t("save_combined_preview"), defaultextension=".mp4", filetypes=[("MP4", "*.mp4")])
        if not out:
            return

        def worker() -> None:
            self._progress_start()
            try:
                result = combine_side_by_side(a, b, out)
                self._log(f"Preview combinado: {result}")
                try:
                    os.startfile(str(result))
                except Exception as exc:
                    self._log(t("open_auto_error", exc=exc))
            except Exception as exc:
                self._log(t("error_msg", exc=exc))
            finally:
                self._progress_stop()

        threading.Thread(target=worker, daemon=True).start()

    def _run_native_preview(self) -> None:
        from tkinter import filedialog
        from .tk_video_preview import VideoPreviewWindow

        path = filedialog.askopenfilename(title=t("select_preview_video"), filetypes=[("MP4", "*.mp4")])
        if path:
            VideoPreviewWindow(self.app.root, path)

    def _run_staging(self) -> None:
        import threading
        from tkinter import filedialog
        from pathlib import Path

        project_dir = filedialog.askdirectory(title=t("select_project_folder"))
        if not project_dir:
            return
        finals = filedialog.askopenfilenames(title=t("select_final_videos"), filetypes=[("MP4", "*.mp4")])
        if not finals:
            return

        def worker() -> None:
            try:
                from .staging import prepare_staging

                staging = prepare_staging(project_dir, [Path(f) for f in finals],
                                         change_log=[t("change_burn_done"), t("change_validation_ok")])
                self._log(t("stage_ready", path=staging))
                import os
                try:
                    os.startfile(str(staging))
                except Exception:
                    pass
            except Exception as exc:
                self._log(t("staging_error", exc=exc))

        threading.Thread(target=worker, daemon=True).start()
    def _find_replace(self) -> None:
        from tkinter import simpledialog
        find = simpledialog.askstring(t("find_title"), t("find_prompt"))
        if not find:
            return
        replace = simpledialog.askstring(t("replace_title"), t("replace_prompt"))
        if replace is None:
            return
        widget = self._lyrics_widget()
        if widget is None or not all(
            hasattr(widget, name) for name in ("get", "delete", "insert")
        ):
            self._log(t("paste_lyrics"))
            return
        text = widget.get("1.0", tk.END)
        count = text.count(find)
        if count == 0:
            self._log(t("text_not_found"))
            return
        new_text = text.replace(find, replace)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", new_text)
        self._log(t("replaced_count", n=count))

    def _export_formats(self) -> None:
        import threading
        from tkinter import filedialog
        from pathlib import Path

        out_dir = filedialog.askdirectory(title=t("export_dir_title"))
        if not out_dir:
            return
        # Use cues from editor if available, else from last align (none) -> require align first
        cues = self._edited_cues()
        if not cues:
            self._log(t("no_cues"))
            return

        def worker():
            try:
                from .export_formats import export_all
                created = export_all(cues, Path(out_dir), title=self.cantado_path.stem if self.cantado_path else "Vilm")
                self._log(t("exported_count", n=len(created), path=out_dir))
                for f in created:
                    self._log(f"  {f.name}")
            except Exception as exc:
                self._log(t("export_error", exc=exc))

        threading.Thread(target=worker, daemon=True).start()

    def _export_project(self) -> None:
        from tkinter import filedialog
        from pathlib import Path
        cues = self._edited_cues()
        if not cues:
            self._log(t("no_cues_export"))
            return
        path = filedialog.asksaveasfilename(title=t("save_project_title"), defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            from .project import HymnProject
            proj = HymnProject.from_cues(cues, title=self.cantado_path.stem if self.cantado_path else "projeto")
            proj.save(path)
            self._log(t("project_saved", path=path))
        except Exception as exc:
            self._log(t("error_msg", exc=exc))

    def _import_project(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(title=t("import_project_title"), filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            from .project import HymnProject
            proj = HymnProject.load(path)
            self._set_edited_cues(proj.to_cues())
            self._log(t("project_imported", n=len(proj.cues)))
        except Exception as exc:
            self._log(t("error_msg", exc=exc))
