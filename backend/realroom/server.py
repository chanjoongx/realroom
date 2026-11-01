"""HTTP API and static site. The same app runs locally (uvicorn) and on Modal (asgi_app).

  GET  /api/config            live runs on or off, runs left today, catalog size
  GET  /api/demos             recorded demo runs for the home page
  GET  /api/runs/{id}         run.json (the interface replays or follows it)
  POST /api/runs              start a live run on an uploaded photo (capped, see Limits)
  GET  /runs/{id}/{file}      images a run wrote (jpg, png, webp only)
  GET  /catalog/{thumb|img}/  product photos
  GET  /*                     the built web app (single page, falls back to index.html)
"""
from __future__ import annotations

import datetime as dt
import io
import json
import pathlib
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
FILE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}\.(jpg|png|webp)$")
PID = re.compile(r"^[A-Z0-9]{10}$")
MEDIA = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

Launcher = Callable[[str, bytes, str, str], None]


@dataclass
class Limits:
    live: bool = False
    reason: str = ""
    per_day: int = 40
    per_client_per_day: int = 3
    max_upload_mb: int = 12
    state_file: pathlib.Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _load(self) -> dict:
        if self.state_file and self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except json.JSONDecodeError:
                pass
        return {}

    def _save(self, st: dict) -> None:
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(st))

    def left_today(self) -> int:
        st = self._load()
        today = dt.date.today().isoformat()
        return max(0, self.per_day - (st.get("total", 0) if st.get("day") == today else 0))

    def take(self, client: str) -> None:
        with self._lock:
            st = self._load()
            today = dt.date.today().isoformat()
            if st.get("day") != today:
                st = {"day": today, "total": 0, "clients": {}}
            if st["total"] >= self.per_day:
                raise HTTPException(429, "The live demo has used its runs for today. The demo rooms show complete recorded runs.")
            used = st["clients"].get(client, 0)
            if used >= self.per_client_per_day:
                raise HTTPException(429, f"You have used your {self.per_client_per_day} live runs for today.")
            st["total"] += 1
            st["clients"][client] = used + 1
            self._save(st)


def create_app(runs_dir: pathlib.Path, catalog_dir: pathlib.Path, web_dir: pathlib.Path | None, limits: Limits,
               launcher: Launcher | None = None, demos_file: pathlib.Path | None = None,
               refresh: Callable[[], None] | None = None) -> FastAPI:
    app = FastAPI(title="Realroom", docs_url=None, redoc_url=None, openapi_url=None)
    runs_dir = pathlib.Path(runs_dir)
    catalog_dir = pathlib.Path(catalog_dir)
    n_catalog = sum(1 for line in (catalog_dir / "catalog.jsonl").open(encoding="utf-8") if line.strip()) \
        if (catalog_dir / "catalog.jsonl").exists() else 0

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        p = request.url.path
        if resp.status_code == 200 and (p.startswith("/catalog/") or (p.startswith("/runs/") and not p.endswith(".json"))):
            resp.headers.setdefault("Cache-Control", "public, max-age=86400")
        if p.startswith("/assets/") and resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @app.get("/api/config")
    def config():
        return {"live": bool(limits.live and launcher), "reason": limits.reason or None,
                "runs_left_today": limits.left_today() if limits.live else None,
                "catalog_size": n_catalog, "max_upload_mb": limits.max_upload_mb}

    @app.get("/api/demos")
    def demos():
        if refresh:
            refresh()
        listed = json.loads(demos_file.read_text(encoding="utf-8")) if demos_file and demos_file.exists() else []
        out = []
        for d in listed:
            rp = runs_dir / d["id"] / "run.json"
            if not rp.exists():
                continue
            run = json.loads(rp.read_text(encoding="utf-8"))
            if run.get("status") != "done":
                continue
            res = run["result"]
            out.append({"id": d["id"], "title": d.get("title") or run["room"].get("title", ""),
                        "kicker": d.get("kicker", ""), "brief": run["brief"], "mode": run["mode"],
                        "before": run["room"]["before"], "after": res["after"], "products": len(res["products"]),
                        "untouched_pct": res["proof"]["untouched_pct"], "cost_usd": run.get("cost_usd", 0),
                        "elapsed_s": run.get("elapsed_s", 0)})
        return out

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        if not RUN_ID.match(run_id):
            raise HTTPException(404, "No such run.")
        if refresh:
            refresh()
        p = runs_dir / run_id / "run.json"
        if not p.exists():
            raise HTTPException(404, "No such run.")
        data = json.loads(p.read_text(encoding="utf-8"))
        data.pop("trace", None)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.post("/api/runs")
    async def start_run(request: Request, photo: UploadFile = File(...), brief: str = Form(""),
                        mode: str = Form("refresh")):
        if not (limits.live and launcher):
            raise HTTPException(503, limits.reason or "Live runs are off on this server.")
        if mode not in ("refresh", "furnish"):
            raise HTTPException(400, "Unknown mode.")
        raw = await photo.read(limits.max_upload_mb * 1024 * 1024 + 1)
        if len(raw) > limits.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"The photo is larger than {limits.max_upload_mb} MB.")
        jpeg = _normalize_photo(raw)
        client = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
        limits.take(client)
        run_id = dt.datetime.now().strftime("u%m%d%H%M") + secrets.token_hex(3)
        brief = re.sub(r"\s+", " ", brief).strip()[:280] or "Make it feel finished and comfortable."
        launcher(run_id, jpeg, brief, mode)
        return {"id": run_id}

    @app.get("/runs/{run_id}/{name}")
    def run_file(run_id: str, name: str):
        if not RUN_ID.match(run_id) or not FILE_NAME.match(name):
            raise HTTPException(404)
        p = runs_dir / run_id / name
        if not p.exists():
            if refresh:
                refresh()
            if not p.exists():
                raise HTTPException(404)
        return FileResponse(p, media_type=MEDIA[p.suffix])

    @app.get("/catalog/{which}/{name}")
    def catalog_file(which: str, name: str):
        stem, _, ext = name.partition(".")
        if which not in ("thumb", "img") or not PID.match(stem) or ext != ("webp" if which == "thumb" else "jpg"):
            raise HTTPException(404)
        p = catalog_dir / which / name
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type=MEDIA["." + ext])

    if web_dir and (web_dir / "index.html").exists():
        index_cache: dict = {}

        def index_html() -> bytes:
            # re-read after a rebuild so the page never points at hashed assets that are gone
            f = web_dir / "index.html"
            mt = f.stat().st_mtime
            if index_cache.get("mtime") != mt:
                index_cache.update(mtime=mt, body=f.read_bytes())
            return index_cache["body"]

        @app.get("/{path:path}")
        def spa(path: str):
            if path.startswith(("api/", "runs/", "catalog/")):
                raise HTTPException(404)
            f = (web_dir / path).resolve()
            if path and f.is_file() and web_dir.resolve() in f.parents:
                return FileResponse(f)
            if path.startswith("assets/"):
                raise HTTPException(404)
            return Response(index_html(), media_type="text/html")

    return app


def purge_expired(runs_dir: pathlib.Path, max_age_h: float, keep: set[str] | frozenset = frozenset(),
                  busy: set[str] | frozenset = frozenset()) -> list[str]:
    """Delete live runs (the uploaded photo and every image made from it) once they are max_age_h
    hours old. Recorded demo runs, which use AI-generated rooms, are never deleted."""
    now = time.time()
    gone = []
    if not runs_dir.exists():
        return gone
    for d in runs_dir.iterdir():
        if not d.is_dir() or d.name.startswith("_") or d.name in keep or d.name in busy:
            continue
        try:
            run = json.loads((d / "run.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            run = {}
        if (run.get("room") or {}).get("ai_generated") is True:
            continue
        born = min((f.stat().st_mtime for f in d.iterdir()), default=now)
        if now - born >= max_age_h * 3600:
            shutil.rmtree(d, ignore_errors=True)
            gone.append(d.name)
    return gone


def _normalize_photo(raw: bytes) -> bytes:
    """Decode, apply EXIF rotation, drop metadata, cap the size at 1792 px, re-encode as JPEG."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(400, "This file is not a photo Realroom can read (use JPEG, PNG or WebP).")
    im = ImageOps.exif_transpose(im).convert("RGB")
    w, h = im.size
    if max(w, h) < 640:
        raise HTTPException(400, "The photo is too small. Use one at least 640 pixels wide.")
    if not (0.45 <= w / h <= 2.3):
        raise HTTPException(400, "The photo is too narrow or too wide. A normal landscape or portrait photo works best.")
    s = min(1.0, 1792 / max(w, h))
    if s < 1:
        im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=93)
    return buf.getvalue()
