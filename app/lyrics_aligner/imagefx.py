"""Efeitos de imagem para cenas: remocao de fundo, transparencia e ajustes.

Usa apenas PIL/numpy/scipy, ja incluidos no Vilm (sem download de modelos).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from scipy import ndimage


def _hex(color: str) -> tuple[int, int, int]:
    raw = str(color or "#FFFFFF").strip().removeprefix("#")
    if len(raw) == 8:
        raw = raw[:6]
    if len(raw) != 6:
        return (255, 255, 255)
    try:
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (255, 255, 255)


def _soft_alpha(distance: np.ndarray, tolerance: float, feather: float) -> np.ndarray:
    """Alpha suave: 0 dentro da tolerancia, 255 fora, com penumbra."""
    tolerance = max(0.0, min(0.95, float(tolerance)))
    low = max(0.02, tolerance * 0.55)
    high = max(low + 0.02, tolerance)
    alpha = np.clip((distance - low) / (high - low), 0.0, 1.0) * 255.0
    if feather > 0:
        alpha = ndimage.gaussian_filter(alpha, sigma=max(0.5, float(feather)))
    return np.clip(alpha, 0, 255).astype(np.uint8)


def remove_background(image: Image.Image, *, method: str = "none",
                       color: str = "#FFFFFF", tolerance: float = 0.12,
                       feather: float = 1.5) -> Image.Image:
    """Remove o fundo da imagem e devolve RGBA.

   _method_:
      - none: mantem o alpha existente
      - color: remove pixels proximos da cor informada
      - luma: remove pixels claros (fundo branco)
      - flood: remove apenas a regiao de fundo conectada as bordas
    """
    image = image.convert("RGBA")
    if method == "none":
        return image
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    target = np.asarray(_hex(color), dtype=np.float32) / 255.0
    if method == "luma":
        luma = rgb.mean(axis=2)
        distance = np.clip((luma - (1.0 - tolerance)) / max(0.05, tolerance), 0, 1)
    elif method == "saturation":
        saturation = rgb.max(axis=2) - rgb.min(axis=2)
        distance = np.clip(saturation / max(0.02, tolerance), 0, 1)
    else:
        distance = np.linalg.norm(rgb - target, axis=2)
        distance = np.clip(distance / max(0.02, tolerance * 1.8), 0, 1)
    alpha = _soft_alpha(distance, tolerance, feather)
    if method == "flood":
        seed = distance < tolerance
        labels, count = ndimage.label(seed)
        if count:
            border = set(np.unique(np.concatenate([
                labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]
            ])).tolist())
            border.discard(0)
            background = np.isin(labels, list(border))
            alpha = np.where(background, 0, 255).astype(np.uint8)
            alpha = ndimage.gaussian_filter(alpha.astype(np.float32), max(0.5, feather))
            alpha = np.clip(alpha, 0, 255).astype(np.uint8)
    result = image.copy()
    if method == "none":
        return result
    if hasattr(image, "getchannel"):
        old = np.asarray(image.getchannel("A"), dtype=np.float32) / 255.0
        alpha = (alpha.astype(np.float32) / 255.0 * old * 255.0).astype(np.uint8)
    result.putalpha(Image.fromarray(alpha, mode="L"))
    return result


def apply_adjustments(image: Image.Image, fx: dict) -> Image.Image:
    """Ajustes basicos: brilho, contraste, saturacao, nitidez, blur, cor, etc."""
    image = image.convert("RGBA")
    if fx.get("rotate"):
        image = image.rotate(float(fx["rotate"]), expand=True, resample=Image.Resampling.BICUBIC)
    if fx.get("flip_h"):
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if fx.get("flip_v"):
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    for key, enhancer, default in (
        ("brightness", ImageEnhance.Brightness, 1.0),
        ("contrast", ImageEnhance.Contrast, 1.0),
        ("saturation", ImageEnhance.Color, 1.0),
        ("sharpness", ImageEnhance.Sharpness, 1.0),
    ):
        value = float(fx.get(key, default) or default)
        if abs(value - 1.0) > 1e-6:
            image = enhancer(image).enhance(max(0.0, value))
    if fx.get("grayscale"):
        gray = ImageOps.grayscale(image.convert("RGB"))
        image = ImageOps.colorize(gray, black="#000000", white="#FFFFFF").convert("RGBA")
        image.putalpha(image.getchannel("A"))
    if fx.get("sepia"):
        sepia = ImageOps.colorize(ImageOps.grayscale(image.convert("RGB")),
                                  black="#2b1d0e", white="#f4e3c1").convert("RGBA")
        sepia.putalpha(image.getchannel("A"))
        image = sepia
    if fx.get("tint"):
        color = Image.new("RGBA", image.size, _hex(str(fx["tint"])) + (255,))
        image = Image.blend(image, color, max(0.0, min(0.9, float(fx.get("tint_strength", 0.2)))))
    if float(fx.get("blur", 0)) > 0:
        image = image.filter(ImageFilter.GaussianBlur(float(fx["blur"])))
    alpha = image.getchannel("A")
    opacity = float(fx.get("opacity", 1.0))
    if opacity < 1.0:
        alpha = alpha.point(lambda p: int(p * max(0.0, opacity)))
        image.putalpha(alpha)
    if fx.get("shadow"):
        offset = int(fx.get("shadow_offset", 8))
        blur = float(fx.get("shadow_blur", 6))
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        mask = alpha.filter(ImageFilter.GaussianBlur(blur))
        shadow.paste(Image.new("RGBA", image.size, (0, 0, 0, 150)), (0, 0), mask)
        canvas = Image.new("RGBA", image.size, (0, 0, 0, 0))
        canvas.alpha_composite(shadow, (offset, offset))
        canvas.alpha_composite(image)
        image = canvas
    return image


def process_image(source: str | Path, output: str | Path, fx: dict) -> Path:
    """Aplica remocao de fundo + ajustes e salva PNG RGBA."""
    source = Path(source); output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(source)
    image = remove_background(
        image,
        method=str(fx.get("remove_background", "none")),
        color=str(fx.get("key_color", "#FFFFFF")),
        tolerance=float(fx.get("tolerance", 0.12)),
        feather=float(fx.get("feather", 1.5)),
    )
    image = apply_adjustments(image, fx)
    image.save(output)
    return output
