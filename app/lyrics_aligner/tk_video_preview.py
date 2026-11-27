"""Native video preview with scrubbing using PyAV (av) + tkinter."""

from __future__ import annotations

import base64
import threading
import time
import tkinter as tk
from tkinter import ttk
from weakref import WeakSet

import av
import numpy as np

from .i18n import mark, on_language_changed, t


class VideoPreviewWindow:
    def __init__(self, parent: tk.Tk, path: str) -> None:
        self.parent = parent
        self.path = path
        self.window = tk.Toplevel(parent)
        self.window.title(f"Preview — {path}")
        self.canvas = tk.Canvas(self.window, width=640, height=360, bg="black")
        self.canvas.pack(fill="both", expand=True)
        ctrl = ttk.Frame(self.window)
        ctrl.pack(fill="x", padx=8, pady=4)
        self.play_var = tk.BooleanVar(value=True)
        self.play_button = ttk.Checkbutton(
            ctrl, text=t("video_preview_play"), variable=self.play_var
        )
        mark(self.play_button, "video_preview_play")
        self.play_button.pack(side="left")
        self.slider = ttk.Scale(ctrl, from_=0, to=100, orient="horizontal", command=self._on_seek)
        self.slider.pack(side="left", fill="x", expand=True, padx=8)
        self._status_key: str | None = "video_preview_loading"
        self._status_text = t("video_preview_loading")
        self.status = ttk.Label(self.window, text=self._status_text)
        mark(self.status, "video_preview_loading")
        self.status.pack(pady=4)
        _PREVIEWS.add(self)
        self._running = True
        self._photo = None
        self._seek_to: float | None = None
        self._duration = 0.0
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        threading.Thread(target=self._decode_loop, daemon=True).start()

    def _close(self) -> None:
        self._running = False
        self.window.destroy()

    def _set_status(self, key: str) -> None:
        """Set a translated status and register its key for future switches."""
        if not self.status.winfo_exists():
            return
        text = t(key)
        self._status_key = key
        self._status_text = text
        self.status.config(text=text)
        if key == "video_preview_ended":
            mark(self.status, "video_preview_ended")
        elif key == "video_preview_loading":
            mark(self.status, "video_preview_loading")
        else:
            mark(self.status, key)

    def _refresh_status(self) -> None:
        """Hook: refresh loading/ended text without overwriting playback time."""
        if not self._status_key or not self.status.winfo_exists():
            return
        current = str(self.status.cget("text"))
        if current not in (self._status_text, t(self._status_key)):
            return
        text = t(self._status_key)
        self._status_text = text
        self.status.config(text=text)

    def _on_seek(self, value) -> None:
        # Mark a seek request; handled in decode loop
        try:
            self._seek_to = float(value) / 100.0 * self._duration
        except ValueError:
            pass

    def _decode_loop(self) -> None:
        try:
            container = av.open(self.path)
            stream = container.streams.video[0]
            self._duration = float(container.duration / av.time_base) if container.duration else 0.0
            self.parent.after(0, lambda: self.slider.config(to=max(1, self._duration)))
            frame_interval = 1.0 / 24.0
            for frame in container.decode(video=0):
                if not self._running:
                    break
                if self._seek_to is not None:
                    try:
                        container.seek(int(self._seek_to * av.time_base), stream=stream)
                    except Exception:
                        pass
                    self._seek_to = None
                    # re-decode one frame
                    try:
                        frame = next(container.decode(video=0))
                    except StopIteration:
                        break
                if not self.play_var.get():
                    time.sleep(0.1)
                    continue
                t0 = time.time()
                img = frame.to_ndarray(format="rgb24")
                h, w = img.shape[:2]
                scale = min(640 / w, 360 / h)
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))
                ys = (np.arange(new_h) * h // new_h).astype(int)
                xs = (np.arange(new_w) * w // new_w).astype(int)
                small = img[ys][:, xs]
                raw = small.tobytes()
                header = f"P6\n{new_w} {new_h}\n255\n".encode("ascii")
                data = base64.b64encode(header + raw)
                photo = tk.PhotoImage(data=data, format="PPM")
                pts = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
                self.parent.after(0, self._update_canvas, photo, new_w, new_h, pts)
                elapsed = time.time() - t0
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
            container.close()
        except Exception as exc:
            def _error(exc=exc) -> None:
                if self.status.winfo_exists():
                    self._status_key = None
                    self.status.config(text=f"Erro: {exc}")
            self.parent.after(0, _error)
        else:
            self.parent.after(0, lambda: self._set_status("video_preview_ended"))

    def _update_canvas(self, photo, w, h, pts) -> None:
        self._photo = photo
        self.canvas.config(width=w, height=h)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=photo)
        # Playback time is transient data: the language hook must not replace it.
        self._status_key = None
        self.status.config(text=f"{pts:.2f}s / {self._duration:.2f}s")
        try:
            self.slider.set(pts)
        except Exception:
            pass


# Module level hook: one registration per import; weak references keep closed
# preview windows from being retained.
_PREVIEWS: WeakSet["VideoPreviewWindow"] = WeakSet()


def _on_language_changed(_lang: str) -> None:
    for preview in list(_PREVIEWS):
        try:
            preview._refresh_status()
        except Exception:
            pass


on_language_changed(_on_language_changed)
