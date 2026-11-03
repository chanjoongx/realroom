"""Image helpers: decode, resize, encode, the placement guide, provenance metadata and blurring."""
from __future__ import annotations

import io
import os
import pathlib
import time
import zlib

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"

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
    """Encoded in memory and swapped in whole: the server may be sending this file while a run writes it
    again (a live run saves before.jpg on upload and again when the pipeline starts, and a page that
    fetched it in between got a cut-off response, 11-04)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        data = jpeg_bytes(arr, quality)
    else:
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format=Image.registered_extensions()[path.suffix.lower()])
        data = buf.getvalue()
    _replace_bytes(path, data)
    return path


def save_png(arr: np.ndarray, path: str | pathlib.Path, compress: int = 6) -> pathlib.Path:
    """Lossless PNG with no colour chunks, so a browser canvas reads back the exact pixel values. Swapped in
    whole, like save()."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", compress_level=compress)
    _replace_bytes(path, buf.getvalue())
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


def _replace_bytes(path: pathlib.Path, data: bytes, tries: int = 40) -> None:
    """Write next to the file, then swap it in, so a reader never sees half a file. Windows refuses the swap
    while another handle has the file open (the local server sending it), so it waits for that to end."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05)


# ---------------------------------------------------------------- blurring a photo that must not be kept

FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


def blur_file(path: str | pathlib.Path, max_side: int = 48) -> None:
    """Replace an image with a tiny, heavily blurred copy under the same name and format, so the photo
    can no longer be seen while every link to the file still works. A file that does not decode is
    replaced with plain grey."""
    path = pathlib.Path(path)
    try:
        small = resize_max(load_rgb(path), max_side)
        small = cv2.GaussianBlur(small, (0, 0), max(2.0, max(small.shape[:2]) / 12))
    except Exception:  # noqa: BLE001  (an unreadable file must still be overwritten)
        small = np.full((6, 8, 3), 128, dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(small).save(buf, format=FORMATS.get(path.suffix.lower(), "PNG"))
    _replace_bytes(path, buf.getvalue())


# ---------------------------------------------------------------- provenance (IPTC digital source type in XMP)

# IPTC's term for a picture that combines a real photo with output of a trained model, and the XMP
# names it needs. These are identifiers written into the file (never fetched), kept as bytes.
DIGITAL_SOURCE_TYPE = b"http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia"
_XMP_JPEG_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
_XMP_PNG_KEYWORD = b"XML:com.adobe.xmp"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def xmp_packet(source_type: bytes = DIGITAL_SOURCE_TYPE) -> bytes:
    """A minimal XMP packet (UTF-8) that records how the picture was made."""
    return (
        b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        b' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        b'  <rdf:Description rdf:about="" xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/">\n'
        b'   <Iptc4xmpExt:DigitalSourceType>' + source_type + b'</Iptc4xmpExt:DigitalSourceType>\n'
        b'  </rdf:Description>\n'
        b' </rdf:RDF>\n'
        b'</x:xmpmeta>\n'
        b'<?xpacket end="w"?>'
    )


def _jpeg_with_xmp(data: bytes, packet: bytes) -> bytes:
    body = _XMP_JPEG_HEADER + packet
    if len(body) + 2 > 0xFFFF:
        raise ValueError("XMP packet too large for one JPEG segment")
    app1 = b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body
    pos = 2
    if data[2:4] == b"\xff\xe0":  # JFIF stays the first segment, as readers expect
        pos = 4 + int.from_bytes(data[4:6], "big")
    return data[:pos] + app1 + data[pos:]


def _png_with_xmp(data: bytes, packet: bytes) -> bytes:
    if data[12:16] != b"IHDR":
        raise ValueError("PNG without IHDR first")
    end = 8 + 12 + int.from_bytes(data[8:12], "big")  # signature, then IHDR's length, type, data and CRC
    # iTXt: keyword, NUL, compression flag 0, method 0, empty language tag, NUL, empty translation, NUL, text
    payload = _XMP_PNG_KEYWORD + b"\x00" + b"\x00\x00" + b"\x00" + b"\x00" + packet
    chunk = len(payload).to_bytes(4, "big") + b"iTXt" + payload + \
        zlib.crc32(b"iTXt" + payload).to_bytes(4, "big")
    return data[:end] + chunk + data[end:]


def has_xmp(data: bytes) -> bool:
    if data[:3] == b"\xff\xd8\xff":
        return _XMP_JPEG_HEADER in data[:65536]
    if data[:8] == _PNG_SIGNATURE:
        return b"iTXt" + _XMP_PNG_KEYWORD in data
    return False


def _pixels(data: bytes) -> tuple:
    with Image.open(io.BytesIO(data)) as im:
        return im.mode, im.size, np.asarray(im).copy()


def add_xmp(path: str | pathlib.Path, packet: bytes | None = None) -> bool:
    """Write an XMP packet into a JPEG or PNG without re-encoding it: the bytes go into a metadata
    segment (JPEG APP1) or chunk (PNG iTXt) and the image data is not touched. The file is replaced
    only when the new bytes decode to exactly the same pixels. Returns whether the file now has XMP."""
    path = pathlib.Path(path)
    data = path.read_bytes()
    if has_xmp(data):
        return True
    packet = packet or xmp_packet()
    if data[:3] == b"\xff\xd8\xff":
        new = _jpeg_with_xmp(data, packet)
    elif data[:8] == _PNG_SIGNATURE:
        new = _png_with_xmp(data, packet)
    else:
        return False
    m0, s0, p0 = _pixels(data)
    m1, s1, p1 = _pixels(new)
    if m0 != m1 or s0 != s1 or not np.array_equal(p0, p1):
        return False
    _replace_bytes(path, new)
    return True
