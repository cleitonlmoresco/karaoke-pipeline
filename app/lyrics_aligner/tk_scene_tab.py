"""Editor de cena extraido de ``desktop.py`` (F3-I4).

O modulo e dono dos widgets de cena (canvas, lista de camadas, timeline,
propriedades, formatos e dialogo de FX) e dos 28 metodos que os operam.
Todas as dependencias de outros blocos passam pela fachada ``app``:
``project_root_for``, ``background_image``, ``first_batch_item``, ``append_qc``,
``set_status`` e ``scene_autosave_path``. Nenhum widget alheio e acessado
diretamente.

A traducao e aplicada na criacao com ``t()`` + ``mark()`` e reaplicada por
``refresh_language()``, registrada como hook global via WeakSet (mesmo padrao
dos demais modulos de aba). O modulo nunca altera o idioma global, nunca grava
preferencias e nunca escreve autosave durante a construcao: o guarda
``_scene_ready`` so e levantado no fim de ``build()``.
"""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from tkinter import (BOTH, END, LEFT, RIGHT, X, Y, BooleanVar, Canvas,
                     Checkbutton, DoubleVar, IntVar, Listbox, Scale, StringVar,
                     Toplevel, filedialog, messagebox)
from tkinter import ttk
from weakref import WeakSet

from PIL import Image, ImageTk

from .i18n import mark, on_language_changed, t
from .platform_paths import application_data_root

APP_NAME = "Vilm Lyrics Aligner"
# Mesmo calculo de desktop.AUTOSAVE_PATH (inclusive o desvio por variavel de
# ambiente LYRICS_ALIGNER_APP_ROOT usado nos testes headless).
AUTOSAVE_PATH = application_data_root() / "scene-autosave.json"


class SceneTab:
    """Secao de cena da aba de producao (Karaoke)."""

    def __init__(self, app, parent) -> None:
        self.app = app
        self.parent = parent
        # Projeto ao qual a cena pertence e guarda que impede a cena vazia da
        # construcao de sobrescrever um autosave ja existente.
        self.scene_project_dir: Path | None = None
        self._scene_ready = False
        self._language_widgets: list[tuple[object, str]] = []
        self.build(parent)
        _SCENE_TABS.add(self)

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

    def _check(self, parent, key: str, variable, **kwargs):
        return self._register_text(
            Checkbutton(parent, text=t(key), variable=variable, **kwargs), key)

    def build(self, parent) -> None:
        """Monta o LabelFrame de cena dentro do parent fornecido por DesktopApp."""
        scene_frame = ttk.LabelFrame(parent, text=t("scene_title"), padding=8)
        self.scene_frame = scene_frame
        self._register_text(scene_frame, "scene_title")
        scene_frame.pack(fill=X, pady=(10, 0))

        tools = ttk.Frame(scene_frame); tools.pack(fill=X)
        self._button(tools, "new_scene", self._scene_new).pack(side=LEFT)
        self._button(tools, "open_json", self._scene_open).pack(side=LEFT, padx=4)
        self._button(tools, "save_json", self._scene_save).pack(side=LEFT)
        self._button(tools, "add_image", self._scene_add_image).pack(side=LEFT, padx=(14, 0))
        self._button(tools, "add_text", self._scene_add_text).pack(side=LEFT, padx=4)
        self._button(tools, "remove_button", self._scene_remove).pack(side=LEFT)
        self._button(tools, "undo_button", self._scene_undo).pack(side=LEFT, padx=(14, 0))
        self._button(tools, "redo_button", self._scene_redo).pack(side=LEFT, padx=4)
        self._button(tools, "effects_button", self._scene_fx_dialog).pack(side=RIGHT, padx=(0, 6))
        self._button(tools, "scene_preview_button", self._scene_preview).pack(side=RIGHT)
        self.scene_autosave_var = BooleanVar(value=True)
        self.scene_autosave_check = self._check(
            tools, "scene_project_autosave", self.scene_autosave_var)
        self.scene_autosave_check.pack(side=RIGHT, padx=(0, 6))
        self.snap_var = BooleanVar(value=True)
        self._check(tools, "snap_label", self.snap_var).pack(side=RIGHT, padx=(0, 6))
        self.grid_var = IntVar(value=20)
        ttk.Spinbox(tools, from_=5, to=200, width=5,
                    textvariable=self.grid_var).pack(side=RIGHT, padx=(0, 4))
        self._label(tools, "format_label").pack(side=RIGHT, padx=(8, 2))
        self.format_var = StringVar(value="1920x1080")
        self.format_box = ttk.Combobox(tools, state="readonly", width=12,
                                       textvariable=self.format_var,
                                       values=list({"1920x1080", "1280x720", "3840x2160",
                                                    "1080x1920", "1080x1080"}))
        self.format_box.pack(side=RIGHT)
        self.format_box.bind("<<ComboboxSelected>>", self._scene_format_changed)

        stage = ttk.Frame(scene_frame); stage.pack(fill=X, pady=(6, 0))
        self.scene_canvas = Canvas(stage, width=480, height=270,
                                   background="#1a1d24", highlightthickness=1,
                                   highlightbackground="#555b66")
        self.scene_canvas.pack(side=LEFT)
        self.scene_canvas.bind("<Button-1>", self._scene_press)
        self.scene_canvas.bind("<B1-Motion>", self._scene_drag)
        self.scene_canvas.bind("<ButtonRelease-1>", self._scene_release)
        self.preview_label = ttk.Label(stage, relief="sunken",
                                       background="#20242b", width=300)
        self.preview_label.pack(side=LEFT, fill=BOTH, expand=True, padx=(10, 0))

        self.scene_list = Listbox(stage, height=6, width=24)
        self.scene_list.pack(side=LEFT, fill=Y, padx=(10, 0))
        self.scene_list.bind("<<ListboxSelect>>", self._scene_select)

        props = ttk.Frame(scene_frame); props.pack(fill=X, pady=(6, 0))
        self.props = props
        self.pvars = {key: StringVar() for key in
                      ("x", "y", "w", "h", "start", "end", "fade_in", "fade_out",
                       "text", "font_size", "color", "file")}
        fields = [("x", "X"), ("y", "Y"), ("w", "field_width"), ("h", "field_height"),
                  ("start", "field_start"), ("end", "field_end"),
                  ("fade_in", "field_fade_in"), ("fade_out", "field_fade_out"),
                  ("text", "field_text"), ("font_size", "field_font"),
                  ("color", "field_color")]
        for index, (key, label) in enumerate(fields):
            row = index // 6; col = (index % 6) * 2
            caption = (ttk.Label(props, text=label) if label in {"X", "Y"}
                       else self._label(props, label))
            caption.grid(row=row, column=col, sticky="w", padx=(0, 4), pady=2)
            width = 30 if key == "text" else 10
            ttk.Entry(props, textvariable=self.pvars[key], width=width).grid(
                row=row, column=col + 1, sticky="ew", pady=2)
        self._button(props, "apply", self._scene_apply_props).grid(
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
        # A cena vazia construida acima nao deve apagar o autosave do projeto.
        self._scene_ready = True

    def refresh_language(self, _lang: str | None = None) -> None:
        """Reaplica os rotulos de cena sem tocar na cena nem na selecao."""
        for widget, key in list(self._language_widgets):
            try:
                if widget is None or not widget.winfo_exists():
                    continue
                widget.configure(text=t(key))
            except Exception:
                pass

    # ---- 28 operacoes de cena ------------------------------------------
    def _scene_push(self) -> None:
        self.scene_history.append(copy.deepcopy(self.scene))
        self.scene_future.clear()

    def _snap(self, value: float) -> float:
        if not self.snap_var.get():
            return round(value, 1)
        grid = max(1, int(self.grid_var.get()))
        return float(round(value / grid) * grid)

    def _scene_autosave_path(self) -> Path:
        """Autosave do projeto atual ou, sem projeto, o global legado."""
        project = getattr(self, "scene_project_dir", None)
        var = getattr(self, "scene_autosave_var", None)
        enabled = var is None or bool(var.get())
        if project is not None and enabled and (Path(project) / "projeto.json").is_file():
            return Path(project) / "trabalho" / "scene-autosave.json"
        # Permite desviar o global tanto por esta constante quanto pela
        # fachada (testes headless); nunca apagamos o global ao trocar de projeto.
        if AUTOSAVE_PATH != application_data_root() / "scene-autosave.json":
            return AUTOSAVE_PATH
        facade_path = getattr(self.app, "scene_autosave_path", None)
        if callable(facade_path):
            return Path(facade_path())
        return AUTOSAVE_PATH

    def _scene_bind_project(self, path: str | Path | None = None) -> bool:
        """Troca o projeto da cena e recarrega o autosave correspondente."""
        root = (self.app.project_root_for(path)
                if hasattr(self.app, "project_root_for") else None)
        if root == self.scene_project_dir:
            return False
        self._scene_ready = False
        self.scene_project_dir = root
        self._scene_load_autosave()
        self._scene_redraw()
        self._scene_ready = True
        return True

    @property
    def bind_project(self):
        """API publica: usada por set_media na casca de 7 abas (F3-I2)."""
        return self._scene_bind_project

    def _scene_view(self) -> tuple[int, int, float, float]:
        """Canvas 480x270 preservando a proporcao do formato da cena.

        Retorna ``(cw, ch, sx, sy)``: ``sx``/``sy`` sao usados por desenho,
        grade, area segura, camadas e arraste para nao distorcer coordenadas.
        """
        sw = max(1, int(self.scene.get("width", 1920) or 1920))
        sh = max(1, int(self.scene.get("height", 1080) or 1080))
        scale = min(480.0 / sw, 270.0 / sh)
        cw = max(1, int(round(sw * scale)))
        ch = max(1, int(round(sh * scale)))
        return cw, ch, cw / sw, ch / sh

    def _scene_autosave(self) -> None:
        if not getattr(self, "_scene_ready", False):
            return
        path = self._scene_autosave_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.scene, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        except OSError:
            pass

    def _scene_load_autosave(self) -> None:
        path = self._scene_autosave_path()
        if not path.is_file():
            return
        try:
            scene = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(scene, dict) or not isinstance(scene.get("layers"), list):
                return
            self.scene = scene
            if hasattr(self.app, "append_qc"):
                self.app.append_qc(t("scene_restored"))
            from .scene import preset_size
            for name in ("1920x1080", "1280x720", "3840x2160", "1080x1920", "1080x1080"):
                width, height, _margin = preset_size(name)
                if (width == int(scene.get("width", 0))
                        and height == int(scene.get("height", 0))):
                    self.format_var.set(name)
                    break
        except (OSError, ValueError):
            pass

    def _scene_redraw(self) -> None:
        c = self.scene_canvas
        cw, ch, sx, sy = self._scene_view()
        c.configure(width=cw, height=ch)
        c.delete("all")
        c.create_rectangle(0, 0, cw, ch, fill="#2b303b", outline="")
        if self.snap_var.get():
            grid = max(1, int(self.grid_var.get()))
            gx = grid * sx
            while gx < cw:
                c.create_line(gx, 0, gx, ch, fill="#333944")
                gx += grid * sx
            gy = grid * sy
            while gy < ch:
                c.create_line(0, gy, cw, gy, fill="#333944")
                gy += grid * sy
        from .scene import safe_bounds
        sw = int(self.scene.get("width", 1920)); sh = int(self.scene.get("height", 1080))
        x1, y1, x2, y2 = safe_bounds(self.scene)
        c.create_rectangle(x1 * sx, y1 * sy, x2 * sx, y2 * sy,
                           outline="#d95f5f", dash=(4, 3))
        for index, layer in enumerate(self.scene.get("layers", [])):
            x = float(layer.get("x", 0)) * sx
            y = float(layer.get("y", 0)) * sy
            w = max(6, float(layer.get("w", sw)) * sx)
            h = max(6, float(layer.get("h", sh)) * sy)
            color = "#4a90d9" if layer.get("type") == "image" else "#d9a441"
            c.create_rectangle(x, y, x + w, y + h, outline=color, width=2,
                               tags=f"layer{index}")
            c.create_rectangle(x + w - 8, y + h - 8, x + w, y + h,
                               fill=color, outline="", tags=f"handle{index}")
            if layer.get("type") == "text":
                c.create_text(x + w / 2, y + h / 2, text=str(layer.get("text", ""))[:22],
                              fill="white", tags=f"layer{index}")
        self.scene_list.delete(0, END)
        for index, layer in enumerate(self.scene.get("layers", [])):
            label = (f"{index + 1}. {layer.get('type')}  "
                     f"{layer.get('text') or Path(str(layer.get('file') or '')).name}")
            self.scene_list.insert(END, label[:34])
        self._draw_timeline()
        self._scene_autosave()

    def _draw_timeline(self) -> None:
        c = self.timeline
        c.delete("all")
        width = max(400, c.winfo_width())
        total = max(30.0, max((float(l.get("end") or 0)
                               for l in self.scene.get("layers", [])), default=0) + 5)
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
        cw, ch, _sx, _sy = self._scene_view()
        self.scene_canvas.configure(width=cw, height=ch)
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
        # Mesmo sx/sy do desenho: clique e arraste nao distorcem coordenadas.
        self.drag_scale = self._scene_view()[2:]
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
        scale_x, scale_y = getattr(self, "drag_scale", None) or self._scene_view()[2:]
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
        if hasattr(self, "drag_scale"):
            del self.drag_scale

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
        cw, ch, _sx, _sy = self._scene_view()
        self.scene_canvas.configure(width=cw, height=ch)
        self._scene_redraw()

    def _scene_open(self) -> None:
        path = filedialog.askopenfilename(filetypes=[(t("scene_json_filetype"), "*.json")])
        if not path:
            return
        try:
            from .scene import load_scene, validate_scene
            self._scene_push()
            self.scene = load_scene(path)
            for warning in validate_scene(self.scene):
                self.app.append_qc(t("scene_qc_prefix", warning=warning))
            cw, ch, _sx, _sy = self._scene_view()
            self.scene_canvas.configure(width=cw, height=ch)
            self._scene_redraw()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def _scene_save(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[(t("scene_json_filetype"), "*.json")],
                                            initialfile="cena.json")
        if path:
            from .scene import save_scene
            save_scene(path, self.scene)

    def _scene_add_image(self) -> None:
        path = filedialog.askopenfilename(filetypes=[(t("image_filetype"),
                                                      "*.png *.jpg *.jpeg *.webp")])
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
        self.scene["layers"].append({"type": "text", "text": t("scene_new_text"),
                                     "x": 960, "y": 800, "font_size": 64,
                                     "color": "#000000", "start": 0, "end": 5,
                                     "fade": 0.3})
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
            messagebox.showinfo(APP_NAME, t("select_layer"))
            return
        layer = self.scene["layers"][self.selected_layer]
        if layer.get("type") != "image":
            messagebox.showinfo(APP_NAME, t("fx_image_only"))
            return
        fx = dict(layer.get("fx") or {})
        top = Toplevel(self.app.root)
        top.title(t("fx_title"))
        top.geometry("460x820")
        row = 0
        ttk.Label(top, text=t("fx_remove_bg")).grid(row=row, column=0, sticky="w", padx=8, pady=6)
        method = StringVar(value=str(fx.get("remove_background", "none")))
        ttk.Combobox(top, state="readonly", textvariable=method,
                     values=["none", "color", "luma", "saturation", "flood"]).grid(row=row, column=1, sticky="w")
        row += 1
        ttk.Label(top, text=t("fx_bg_color")).grid(row=row, column=0, sticky="w", padx=8)
        color = StringVar(value=str(fx.get("key_color", "#FFFFFF")))
        ttk.Entry(top, textvariable=color, width=14).grid(row=row, column=1, sticky="w")
        row += 1
        scales: dict[str, DoubleVar] = {}
        for key, label, low, high, default in (
            ("tolerance", t("fx_tolerance"), 0.02, 0.9, 0.12),
            ("feather", t("fx_feather"), 0, 8, 1.5),
            ("brightness", t("fx_brightness"), 0, 3, 1),
            ("contrast", t("fx_contrast"), 0, 3, 1),
            ("saturation", t("fx_saturation"), 0, 3, 1),
            ("sharpness", t("fx_sharpness"), 0, 3, 1),
            ("blur", t("fx_blur"), 0, 10, 0),
            ("opacity", t("fx_opacity"), 0, 1, 1),
            ("rotate", t("fx_rotate"), -180, 180, 0),
        ):
            ttk.Label(top, text=label).grid(row=row, column=0, sticky="w", padx=8)
            var = DoubleVar(value=float(fx.get(key, default)))
            scales[key] = var
            Scale(top, from_=low, to=high, resolution=(high - low) / 100,
                  orient="horizontal", variable=var, length=230).grid(row=row, column=1)
            row += 1
        checks: dict[str, BooleanVar] = {}
        for key, label in (("grayscale", t("fx_grayscale")), ("sepia", t("fx_sepia")),
                           ("flip_h", t("fx_flip_h")), ("flip_v", t("fx_flip_v")),
                           ("shadow", t("fx_shadow"))):
            var = BooleanVar(value=bool(fx.get(key, False)))
            checks[key] = var
            Checkbutton(top, text=label, variable=var).grid(row=row, column=1, sticky="w")
            row += 1
        # F2-I3a: tintura e sombra (fx) + Ken Burns (propriedade da camada).
        ttk.Label(top, text=t("fx_tint")).grid(row=row, column=0, sticky="w", padx=8)
        # Vazio = sem tintura (imagefx so aplica quando ha cor).
        tint = StringVar(value=str(fx.get("tint", "")))
        ttk.Entry(top, textvariable=tint, width=14).grid(row=row, column=1, sticky="w")
        row += 1
        extra_scales: dict[str, DoubleVar] = {}
        for key, label, low, high, default in (
            ("tint_strength", t("fx_tint_strength"), 0, 0.9, 0.2),
            ("shadow_offset", t("fx_shadow_offset"), 0, 40, 8),
            ("shadow_blur", t("fx_shadow_blur"), 0, 30, 6),
        ):
            ttk.Label(top, text=label).grid(row=row, column=0, sticky="w", padx=8)
            var = DoubleVar(value=float(fx.get(key, default)))
            extra_scales[key] = var
            Scale(top, from_=low, to=high, resolution=(high - low) / 100,
                  orient="horizontal", variable=var, length=230).grid(row=row, column=1)
            row += 1
        ken_burns = BooleanVar(value=bool(layer.get("ken_burns", False)))
        Checkbutton(top, text=t("fx_ken_burns"), variable=ken_burns).grid(
            row=row, column=1, sticky="w")
        row += 1

        def apply() -> None:
            self._scene_push()
            new_fx = {"remove_background": method.get(),
                      "key_color": color.get(), "tint": tint.get()}
            for key, var in {**scales, **extra_scales}.items():
                new_fx[key] = round(float(var.get()), 3)
            for key, var in checks.items():
                new_fx[key] = bool(var.get())
            layer["fx"] = new_fx
            layer["ken_burns"] = bool(ken_burns.get())
            self._scene_redraw()
            self._scene_preview()
            top.destroy()

        ttk.Button(top, text=t("apply"), style="Primary.TButton", command=apply).grid(
            row=row, column=0, columnspan=2, pady=12)

    def _scene_preview(self) -> None:
        if not self.scene.get("layers"):
            return
        try:
            from .scene import render_preview
            background = (self.app.background_image()
                          if hasattr(self.app, "background_image") else None)
            if background is None or not background.is_file():
                first = (self.app.first_batch_item()
                         if hasattr(self.app, "first_batch_item") else None)
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
            if hasattr(self.app, "append_qc"):
                self.app.append_qc(t("scene_preview_error", exc=exc))


# Mesmo padrao dos demais tab modules: instancias fracas e hook unico, com
# try/except para nunca quebrar a retraducao por causa de uma aba fechada.
_SCENE_TABS: WeakSet["SceneTab"] = WeakSet()


def _on_language_changed(lang: str) -> None:
    for tab in list(_SCENE_TABS):
        try:
            tab.refresh_language(lang)
        except Exception:
            pass


on_language_changed(_on_language_changed)
