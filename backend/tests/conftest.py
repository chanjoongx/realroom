"""Shared fixtures. The tests make no network calls and need no API key."""
from __future__ import annotations

import io
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

DEMO_RUNS = REPO / "demo-runs"
CATALOG_INDEX = REPO / "catalog" / "index"
DEMO_IDS = ("living", "moving-day", "studio")


@pytest.fixture(autouse=True)
def _no_key(monkeypatch):
    """Keep a developer's real key out of the tests, so nothing can reach the paid API by accident."""
    monkeypatch.setenv("MODEL_API_KEY", "")


def jpeg_bytes(w: int = 1024, h: int = 768, exif: bytes | None = None, fmt: str = "JPEG") -> bytes:
    from PIL import Image

    im = Image.new("RGB", (w, h), (180, 170, 160))
    buf = io.BytesIO()
    if exif is not None:
        im.save(buf, format=fmt, exif=exif)
    else:
        im.save(buf, format=fmt)
    return buf.getvalue()
