"""Delayed tooltip helper for the tkinter frontend.

A tooltip is shown after the mouse rests on a widget for 2 seconds.  It is
placed in the bottom-right corner of the main window so it never covers the
widget being inspected.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

from .i18n import find_key, mark_tooltip


def _ui_scale(widget):
    """Provider opcional subindo a cadeia de pais; ``None`` = escala neutra.

    Um tooltip pode pertencer a uma Toplevel filha (ex.: Configuracoes
    avancadas), que nao carrega o ``ui_scale`` exposto no root.  A busca
    percorre ``winfo_parent``/``nametowidget`` e, quando esse elo termina,
    o ``master`` Python da Toplevel, ate encontrar um toplevel com provider.
    """
    node = widget
    seen: set[object] = set()
    for _ in range(64):  # teto contra ciclos de parentesco
        if node is None:
            break
        key = str(getattr(node, "_w", "")) or id(node)
        if key in seen:
            break
        seen.add(key)
        provider = getattr(node, "ui_scale", None)
        if provider is not None:
            return provider
        parent = None
        try:
            name = node.winfo_parent()
        except Exception:
            name = ""
        if name and name != str(node):
            try:
                parent = node.nametowidget(name)
            except Exception:
                parent = None
        if parent is None:
            master = getattr(node, "master", None)
            if master is not None and master is not node:
                parent = master
        node = parent
    return None


def _scaled_font(widget, family: str, size: Any, *styles):
    """Fonte escalada pelo provider, ou a fonte original se ele nao existir."""
    provider = _ui_scale(widget)
    font_fn = getattr(provider, "font", None)
    if callable(font_fn):
        try:
            return tuple(font_fn(family, size, *styles))
        except Exception:
            pass
    return (family, size, *styles)


def _scale_factor(widget) -> float:
    """Fator BASE do root, com fallback neutro (1.0)."""
    provider = _ui_scale(widget)
    factor_fn = getattr(provider, "factor", None)
    if not callable(factor_fn):
        return 1.0
    try:
        value = float(factor_fn())
    except Exception:
        return 1.0
    return value if value > 0 else 1.0


class DelayedTooltip:
    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 2000) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        # Make the tooltip reachable by i18n.retranslate() and remember the
        # current text so an already visible tip can be updated in place.
        widget._vlm_tooltip = self
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")

    def _on_enter(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _on_leave(self, _event=None) -> None:
        self._cancel()
        self._hide()

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None:
            return
        root = self.widget.winfo_toplevel()
        self._tip = tk.Toplevel(root)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_attributes("-topmost", True)
        # O provider pode estar no root e nao na Toplevel filha que hospeda o
        # widget dono: a resolucao parte do widget e sobe a cadeia de pais.
        owner = self.widget
        factor = _scale_factor(owner)
        label = tk.Label(
            self._tip,
            text=self.text,
            justify="left",
            background="#FFFFE0",
            foreground="#000000",
            relief="solid",
            borderwidth=1,
            font=_scaled_font(owner, "Segoe UI", 9),
            wraplength=max(1, round(320 * factor)),
            padx=8,
            pady=6,
        )
        label.pack()
        # Position in bottom-right corner of the main window
        root.update_idletasks()
        x = root.winfo_x() + root.winfo_width() - self._tip.winfo_reqwidth() - 16
        y = root.winfo_y() + root.winfo_height() - self._tip.winfo_reqheight() - 40
        self._tip.wm_geometry(f"+{x}+{y}")

    def set_text(self, text: str) -> None:
        """Update the tooltip text, including a tip that is already visible."""
        self.text = text
        if self._tip is not None:
            try:
                for child in self._tip.winfo_children():
                    child.configure(text=text)
            except Exception:
                pass

    def _hide(self) -> None:
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def add_tooltip(widget: tk.Widget, text: str, delay_ms: int = 2000) -> DelayedTooltip:
    tooltip = DelayedTooltip(widget, text, delay_ms)
    # Callers pass an already translated string; resolve it back to its key so
    # retranslate() can refresh the tip without recreating any widget.
    mark_tooltip(widget, find_key(text) or text)
    return tooltip
