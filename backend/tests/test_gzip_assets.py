"""The built page's script and stylesheet go out gzipped to a browser that accepts it; nothing else changes."""
from __future__ import annotations

import gzip
import os

import pytest
from fastapi.testclient import TestClient

from conftest import CATALOG_INDEX, DEMO_RUNS
from realroom import server
from realroom.server import Limits, accepts_gzip, create_app

IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def site(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Realroom</title>", encoding="utf-8")
    files = {
        "assets/index-abc123.js": b"export const rooms = ['living', 'moving-day', 'studio'];\n" * 400,
        "assets/index-abc123.css": b".compare { position: absolute; inset: 0; }\n" * 300,
        # stand-ins for a photo and a font: sent as they are
        "assets/room-abc123.jpg": bytes(range(256)) * 40,
        "assets/serif-abc123.woff2": bytes(reversed(range(256))) * 20,
        "og.jpg": bytes(range(256)) * 30,
    }
    for name, body in files.items():
        (dist / name).write_bytes(body)
    app = create_app(DEMO_RUNS, CATALOG_INDEX, dist, Limits(live=False), demos_file=DEMO_RUNS / "demos.json")
    return TestClient(app), dist, files


def raw_get(client, path, encoding):
    """The response with its body exactly as sent (the test client would otherwise unzip it)."""
    with client.stream("GET", path, headers={"Accept-Encoding": encoding}) as r:
        return r, b"".join(r.iter_raw())


@pytest.mark.parametrize("name,ctype", [("assets/index-abc123.js", "text/javascript"),
                                        ("assets/index-abc123.css", "text/css")])
def test_script_and_style_are_gzipped_when_accepted(site, name, ctype):
    client, _, files = site
    r, raw = raw_get(client, f"/{name}", "gzip, deflate, br, zstd")
    assert r.status_code == 200
    assert r.headers["content-encoding"] == "gzip"
    assert r.headers["vary"] == "Accept-Encoding"
    assert r.headers["content-type"].startswith(ctype)
    assert r.headers["cache-control"] == IMMUTABLE
    assert int(r.headers["content-length"]) == len(raw) < len(files[name]) / 3
    assert gzip.decompress(raw) == files[name]


@pytest.mark.parametrize("encoding", ["identity", "", "gzip;q=0, deflate", "br"])
def test_script_and_style_are_plain_otherwise(site, encoding):
    client, _, files = site
    for name in ("assets/index-abc123.js", "assets/index-abc123.css"):
        r, raw = raw_get(client, f"/{name}", encoding)
        assert r.status_code == 200
        assert "content-encoding" not in r.headers
        assert r.headers["vary"] == "Accept-Encoding"
        assert r.headers["cache-control"] == IMMUTABLE
        assert raw == files[name]


@pytest.mark.parametrize("name", ["assets/room-abc123.jpg", "assets/serif-abc123.woff2", "og.jpg"])
def test_images_and_everything_else_are_left_alone(site, name):
    client, _, files = site
    r, raw = raw_get(client, f"/{name}", "gzip, deflate, br")
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert "vary" not in r.headers
    assert raw == files[name]
    if name.startswith("assets/"):
        assert r.headers["cache-control"] == IMMUTABLE


def test_the_page_itself_is_not_gzipped(site):
    client, _, _ = site
    r, raw = raw_get(client, "/r/living", "gzip")
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert raw.startswith(b"<!doctype html>")


def test_each_file_is_compressed_once_until_it_changes(site, monkeypatch):
    client, dist, files = site
    calls = []
    real = server.gzip.compress

    def counting(data, *a, **kw):
        calls.append(len(data))
        return real(data, *a, **kw)

    monkeypatch.setattr(server.gzip, "compress", counting)
    path = "/assets/index-abc123.js"
    first = raw_get(client, path, "gzip")[1]
    second = raw_get(client, path, "gzip")[1]
    assert first == second and len(calls) == 1

    f = dist / "assets" / "index-abc123.js"
    newer = b"export const rooms = [];\n" * 500
    f.write_bytes(newer)
    st = f.stat()
    os.utime(f, (st.st_atime, st.st_mtime + 5))
    assert gzip.decompress(raw_get(client, path, "gzip")[1]) == newer
    assert len(calls) == 2


@pytest.mark.parametrize("header,ok", [
    ("gzip, deflate, br", True), ("GZIP", True), ("deflate, gzip;q=0.5", True), ("x-gzip", True), ("*", True),
    ("", False), ("identity", False), ("br, deflate", False), ("gzip;q=0", False), ("gzip;q=0.0, *", False),
    ("*;q=0", False), ("br, *;q=0.1", True),
])
def test_accept_encoding_is_read_with_its_q_values(header, ok):
    assert accepts_gzip(header) is ok
