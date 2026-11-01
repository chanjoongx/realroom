"""Image helpers shared by the probe: decode, resize, encode, guides and overlays."""
from __future__ import annotations

import io
import pathlib

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
FONT_KO = "C:/Windows/Fonts/malgun.ttf"

GUIDE_COLORS = [(255, 0, 170), (0, 200, 255), (255, 210, 0), (120, 255, 0), (255, 120, 0), (170, 90, 255)]


def load_rgb(src: str | pathlib.Path | bytes) -> np.ndarray:
    if isinstance(src, (bytes, bytearray)):
        img = Image.open(io.BytesIO(src))
    else:
        img = Image.open(src)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return np.asarray(img.convert("RGB")).copy()


def png_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def jpeg_bytes(arr: np.ndarray, quality: int = 92, max_side: int | None = None) -> bytes:
    if max_side:
        arr = resize_max(arr, max_side)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


def save(arr: np.ndarray, path: str | pathlib.Path, quality: int = 92) -> pathlib.Path:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        Image.fromarray(arr).save(path, format="JPEG", quality=quality, subsampling=0)
    else:
        Image.fromarray(arr).save(path)
    return path


def save_png(arr: np.ndarray, path: str | pathlib.Path, compress: int = 6) -> pathlib.Path:
    """Lossless PNG with no colour chunks, so a browser canvas reads back the exact pixel values."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path, format="PNG", compress_level=compress)
    return path


def resize_max(arr: np.ndarray, max_side: int) -> np.ndarray:
    h, w = arr.shape[:2]
    scale = max_side / max(h, w)
    if scale >= 1:
        return arr
    return cv2.resize(arr, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)


def resize_to(arr: np.ndarray, width: int, height: int) -> np.ndarray:
    h, w = arr.shape[:2]
    if (w, h) == (width, height):
        return arr
    interp = cv2.INTER_AREA if width * height < w * h else cv2.INTER_LANCZOS4
    return cv2.resize(arr, (width, height), interpolation=interp)


def norm_to_px(box: list[float] | tuple, width: int, height: int) -> tuple[int, int, int, int]:
    """0-1000 grid box [x_min, y_min, x_max, y_max] to clamped pixel box (x2, y2 inclusive)."""
    x1, y1, x2, y2 = [float(v) for v in box]
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    px = (round(x1 * width / 1000), round(y1 * height / 1000),
          round(x2 * width / 1000), round(y2 * height / 1000))
    return (max(0, min(width - 1, px[0])), max(0, min(height - 1, px[1])),
            max(0, min(width - 1, px[2])), max(0, min(height - 1, px[3])))


FALLBACK_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def _font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in [path] + FALLBACK_FONTS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def draw_guide(arr: np.ndarray, items: list[dict]) -> np.ndarray:
    """Placement guide: translucent numbered boxes (items: label, box_px, color)."""
    base = Image.fromarray(arr).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    stroke = max(4, base.width // 220)
    font = _font(FONT_BOLD, max(28, base.width // 22))
    for item in items:
        color = tuple(item["color"])
        x1, y1, x2, y2 = item["box_px"]
        draw.rectangle([x1, y1, x2, y2], fill=color + (70,), outline=color + (255,), width=stroke)
        label = str(item["label"])
        tw, th = draw.textbbox((0, 0), label, font=font)[2:]
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        pad = stroke * 2
        draw.rounded_rectangle([cx - tw // 2 - pad, cy - th // 2 - pad, cx + tw // 2 + pad, cy + th // 2 + pad],
                               radius=pad, fill=color + (235,))
        draw.text((cx - tw // 2, cy - th // 2 - stroke), label, fill=(0, 0, 0, 255), font=font)
    return np.asarray(Image.alpha_composite(base, layer).convert("RGB")).copy()


def draw_overlay(arr: np.ndarray, items: list[dict], font_px: int | None = None) -> np.ndarray:
    """Analysis overlay: outlined boxes with short Korean or English labels."""
    base = Image.fromarray(arr).convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    stroke = max(2, base.width // 500)
    font = _font(FONT_KO, font_px or max(14, base.width // 70))
    for item in items:
        color = tuple(item.get("color", (255, 255, 255)))
        x1, y1, x2, y2 = item["box_px"]
        dash = item.get("dashed", False)
        if dash:
            for x in range(x1, x2, 18):
                draw.line([x, y1, min(x + 9, x2), y1], fill=color + (255,), width=stroke)
                draw.line([x, y2, min(x + 9, x2), y2], fill=color + (255,), width=stroke)
            for y in range(y1, y2, 18):
                draw.line([x1, y, x1, min(y + 9, y2)], fill=color + (255,), width=stroke)
                draw.line([x2, y, x2, min(y + 9, y2)], fill=color + (255,), width=stroke)
        else:
            draw.rectangle([x1, y1, x2, y2], outline=color + (255,), width=stroke)
        label = item.get("label")
        if label:
            tb = draw.textbbox((x1 + 4, y1 + 2), label, font=font)
            draw.rectangle([tb[0] - 3, tb[1] - 2, tb[2] + 3, tb[3] + 2], fill=(0, 0, 0, 170))
            draw.text((x1 + 4, y1 + 2), label, fill=color + (255,), font=font)
    return np.asarray(Image.alpha_composite(base, layer).convert("RGB")).copy()


def product_card(arr: np.ndarray, side: int = 1024) -> np.ndarray:
    """Center a product photo on a white square so every reference has the same framing."""
    h, w = arr.shape[:2]
    scale = (side * 0.92) / max(h, w)
    small = cv2.resize(arr, (max(1, round(w * scale)), max(1, round(h * scale))),
                       interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LANCZOS4)
    canvas = np.full((side, side, 3), 255, dtype=np.uint8)
    y0, x0 = (side - small.shape[0]) // 2, (side - small.shape[1]) // 2
    canvas[y0:y0 + small.shape[0], x0:x0 + small.shape[1]] = small
    return canvas


def crop_box(arr: np.ndarray, box: tuple[int, int, int, int], margin: float = 0.08) -> np.ndarray:
    h, w = arr.shape[:2]
    x1, y1, x2, y2 = box
    mx, my = int((x2 - x1) * margin), int((y2 - y1) * margin)
    return arr[max(0, y1 - my):min(h, y2 + my), max(0, x1 - mx):min(w, x2 + mx)].copy()
