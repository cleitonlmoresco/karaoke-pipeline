"""Escala visual proporcional da casca tkinter pelo tamanho da janela.

Sem controle manual de zoom, sem chave nova em settings.json e sem tocar em
i18n: o unico insumo e a geometry real do ``root``.  Todos os metodos publicos
devem ser chamados na thread principal (a mesma que criou o Tk); nunca ha
chamadas Tcl vindas de workers.

Contrato de idempotencia: os tamanhos BASE das named fonts sao capturados uma
unica vez em ``__init__``.  Cada ``apply_now`` reconfigura ``base * fator`` --
nunca ``atual * fator`` -- de modo que reaplicar o mesmo tamanho nao multiplica
a fonte outra vez.
"""

from __future__ import annotations

import threading
import tkinter as tk
import weakref
from tkinter import font as tkfont
from typing import Any, Callable

BASE_W = 1100
BASE_H = 720
MIN_FACTOR = 0.75
MAX_FACTOR = 2.0
DEBOUNCE_MS = 150
DELTA = 0.02

# Ordem canonica das named fonts do Tk.  Algumas podem nao existir em certas
# plataformas; nesse caso criamos a fonte com o mesmo nome (nome seguro, ja
# reservado pelo Tk) a partir de TkDefaultFont.
_NAMED_FONT_NAMES = (
    "TkDefaultFont",
    "TkTextFont",
    "TkHeadingFont",
    "TkFixedFont",
    "TkMenuFont",
    "TkTooltipFont",
    "TkSmallCaptionFont",
    "TkCaptionFont",
    "TkIconFont",
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _scale_int(value: Any, factor: float, *, keep_zero: bool = True) -> int:
    """Arredonda um valor BASE em px para o fator, preservando zero."""
    try:
        base = float(value)
    except (TypeError, ValueError):
        return 0 if keep_zero else 1
    scaled = int(round(base * factor))
    if base > 0 and scaled < 1:
        scaled = 1
    if base <= 0 and keep_zero:
        return 0
    return scaled


def _scale_pad(value: Any, factor: float) -> Any:
    """Escala ``padx``/``pady`` (int ou par de ints) sem perder a tupla."""
    if isinstance(value, (tuple, list)):
        items = list(value)[:2]
        while len(items) < 2:
            items.append(0)
        return (_scale_int(items[0], factor), _scale_int(items[1], factor))
    return _scale_int(value, factor)


class UiScale:
    """Escala fonts e paddings registrados com a geometry da janela.

    Uso tipico::

        scale = UiScale(root)
        scale.on_apply(app.reapply_theme)
        ...  # widgets construidos
        scale.bind_root()
    """

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._applied_factor = 1.0
        self._applying = False
        self._after_id: str | None = None
        self._callbacks: list[Callable[[], None]] = []
        self._packs: list[tuple[weakref.ReferenceType, Any, Any]] = []
        self._px: list[tuple[weakref.ReferenceType, str, Any]] = []
        self._fonts: dict[str, tkfont.Font] = {}
        self._font_bases: dict[str, int] = {}

        # Captura UNICA dos tamanhos base -- antes de qualquer escala.
        for name in _NAMED_FONT_NAMES:
            font = self._get_or_create_font(name)
            if font is None:
                continue
            try:
                self._font_bases[name] = int(font.cget("size"))
                self._fonts[name] = font
            except (tk.TclError, TypeError, ValueError):
                continue

    # ---- descoberta de fonts --------------------------------------------
    def _get_or_create_font(self, name: str) -> tkfont.Font | None:
        try:
            return tkfont.nametofont(name)
        except tk.TclError:
            pass
        # Fonte ausente: cria com o nome canonico, copiando os atributos de
        # TkDefaultFont.  Nome seguro = o proprio nome reservado pelo Tk.
        try:
            default = tkfont.nametofont("TkDefaultFont")
            kwargs = {
                key: default.cget(key)
                for key in ("family", "size", "weight", "slant", "underline", "overstrike")
            }
            return tkfont.Font(self.root, name=name, **kwargs)
        except (tk.TclError, TypeError, ValueError):
            return None

    # ---- fator ----------------------------------------------------------
    def factor(self) -> float:
        """``clamp(min(largura/BASE_W, altura/BASE_H), MIN, MAX)``."""
        try:
            width = self.root.winfo_width()
            height = self.root.winfo_height()
        except tk.TclError:
            return 1.0
        # Geometria ainda desconhecida (1x1) -- tipico antes do mapping:
        # mantem escala neutra ate o primeiro <Configure> real.
        if width <= 1 or height <= 1:
            return 1.0
        return _clamp(min(width / BASE_W, height / BASE_H), MIN_FACTOR, MAX_FACTOR)

    # ---- fonte escalada -------------------------------------------------
    def font(self, family: str, size: Any, *styles: Any) -> tuple:
        """``(familia, max(1, round(size * fator())), *estilos)``."""
        try:
            base = float(size)
        except (TypeError, ValueError):
            base = 10.0
        scaled = max(1, int(round(base * self.factor())))
        return (family, scaled, *styles)

    # ---- registracoes ---------------------------------------------------
    def register_pack(self, widget: tk.Misc, padx: Any, pady: Any) -> None:
        """Registra paddings BASE de ``pack`` para acompanhar a escala."""
        try:
            self._packs.append((weakref.ref(widget), padx, pady))
        except TypeError:
            # Widget sem suporte a weakref: ignorado (nao segurar referencia).
            pass

    def register_px(self, widget: tk.Misc, attr: str, base_value: Any) -> None:
        """Registra uma opcao BASE em px (ex.: ``width``) de um widget."""
        try:
            self._px.append((weakref.ref(widget), str(attr), base_value))
        except TypeError:
            pass

    def scale_pad(self, value: Any) -> Any:
        """Converte um padding BASE para o fator atual (util em repacks manuais)."""
        return _scale_pad(value, self.factor())

    def on_apply(self, callback: Callable[[], None]) -> None:
        """Registra callback chamado apos cada aplicacao da escala."""
        if callable(callback) and callback not in self._callbacks:
            self._callbacks.append(callback)

    # ---- ciclo de aplicacao ---------------------------------------------
    def bind_root(self) -> None:
        """Inscreve o debounce no `<Configure>` do proprio root."""
        self.root.bind("<Configure>", self._on_root_configure, add="+")

    def _on_root_configure(self, event: tk.Event) -> None:
        # Eventos de filhos nao interessam e nao podem rearmar o debounce.
        if event.widget is not self.root or self._applying:
            return
        if threading.current_thread() is not threading.main_thread():
            return
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except (tk.TclError, ValueError):
                pass
        self._after_id = self.root.after(DEBOUNCE_MS, self._apply_debounced)

    def _apply_debounced(self) -> None:
        self._after_id = None
        # O delta e verificado no aguardo: mudancas menores que DELTA nao
        # justificam reconfigurar fonts/paddings (evita tremor e loops).
        if abs(self.factor() - self._applied_factor) < DELTA:
            return
        self.apply_now()

    def apply_now(self) -> None:
        """Recomputa o fator, reaplica fonts/paddings e dispara callbacks.

        Main-thread-only: chamar de uma worker levantaria TclError, porque
        ``after`` e qualquer ``configure`` pertencem a quem criou o Tk.
        Chamadores assíncronos devem entregar o pedido à main thread pelos
        seus próprios canais (fila + ``after`` já agendado na main thread).
        """
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("UiScale.apply_now deve ser chamado na thread principal.")
        if self._applying:
            return
        self._applying = True
        try:
            factor = self.factor()

            # Named fonts: sempre a partir do tamanho BASE capturado.
            for name, base in self._font_bases.items():
                font = self._fonts.get(name)
                if font is None:
                    continue
                scaled = max(1, int(round(base * factor)))
                try:
                    font.config(size=scaled)
                except tk.TclError:
                    continue

            # Paddings registrados (referencias fracas; widgets mortos saem).
            live_packs = []
            for ref, padx, pady in self._packs:
                widget = ref()
                if widget is None:
                    continue
                try:
                    if not widget.winfo_exists():
                        continue
                    widget.pack_configure(
                        padx=_scale_pad(padx, factor), pady=_scale_pad(pady, factor)
                    )
                except tk.TclError:
                    continue
                live_packs.append((ref, padx, pady))
            self._packs = live_packs

            # Demais valores px registrados.
            live_px = []
            for ref, attr, base_value in self._px:
                widget = ref()
                if widget is None:
                    continue
                try:
                    if not widget.winfo_exists():
                        continue
                    widget.configure(**{attr: _scale_int(base_value, factor)})
                except (tk.TclError, TypeError, ValueError):
                    continue
                live_px.append((ref, attr, base_value))
            self._px = live_px

            self._applied_factor = factor

            # Callbacks (ex.: reaplicar o tema escalado) apos o estado consistente.
            for callback in list(self._callbacks):
                try:
                    callback()
                except Exception:
                    # Um callback quebrado nunca impede os demais.
                    continue
        finally:
            self._applying = False
