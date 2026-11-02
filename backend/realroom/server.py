"""HTTP API and static site. The same app runs locally (uvicorn) and on Modal (asgi_app).

  GET  /api/config            live runs on or off, runs left today, catalog size
  GET  /api/demos             recorded demo runs for the home page
  GET  /api/runs/{id}         run.json (the interface replays or follows it)
  POST /api/runs              start a live run on an uploaded photo (capped, see Limits)
  GET  /runs/{id}/{file}      images a run wrote (jpg, png, webp only)
  GET  /catalog/{thumb|img}/  product photos (from the catalog folder, then any extra photo folders)
  GET  /*                     the built web app (single page, falls back to index.html)
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import io
import ipaddress
import json
import os
import pathlib
import re
import secrets
import shutil
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.datastructures import UploadFile

from .atomic import write_atomic

# Path parts are checked with fullmatch, so a trailing newline never passes.
RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
FILE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,80}\.(jpg|png|webp)")
PID = re.compile(r"[A-Z0-9]{10}")
MEDIA = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

# Uploads. Only these decoders ever see a visitor's bytes, and the pixel count is read from the file
# header before anything is decoded (Pillow itself only warns from 89 MP and refuses from 179 MP).
# The web page sends JPEGs of at most 1792 px, so these limits only stop crafted files.
PHOTO_FORMATS = ("JPEG", "PNG", "WEBP")
MAX_PIXELS = 40_000_000
MULTIPART_SLACK = 64 * 1024   # boundaries, part headers, brief and mode on top of the photo itself
MAX_FIELD_BYTES = 16 * 1024   # brief and mode (the brief is cut to 280 characters anyway)
DECODE_SLOTS = 2              # uploads decoded at the same time, each in a worker thread

# Security headers on every response. CSP is one switch: set it to "" to send no Content-Security-Policy.
CSP = ("default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Strict-Transport-Security": "max-age=31536000",
}

STALE_RUN_S = 300   # a "running" run.json untouched this long, with no worker here, has stopped
DEMOS_TTL_S = 60

INTERRUPTED = "The run stopped before it finished because the server was interrupted. Please start it again."
PAUSED = "Live runs are paused while the usage counter is checked. The demo rooms show complete recorded runs."

Launcher = Callable[[str, bytes, str, str], None]


def log(msg: str) -> None:
    """One line on stdout, which Modal keeps as the app log. Never pass an address, a photo or a brief."""
    print(f"realroom: {msg}", flush=True)


# ---------------------------------------------------------------- visitors

def _global_ip(text: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    s = (text or "").strip()
    if s.startswith("[") and "]" in s:   # [v6] or [v6]:port
        s = s[1:s.index("]")]
    elif s.count(":") == 1:             # v4:port
        s = s.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip if ip.is_global else None


def client_identity(request: Request) -> tuple[str, str]:
    """(bucket, source) for the per-visitor cap. The source is "peer" when the connection's own address
    is a public one (what Modal's proxy reports), else "forwarded" for the right-most public entry of
    X-Forwarded-For (the one the nearest proxy added), else "unknown", which is one shared bucket.
    An IPv6 visitor counts per /64, because one home or phone connection usually holds a whole /64."""
    source = "peer"
    ip = _global_ip(request.client.host if request.client else None)
    if ip is None:
        source = "forwarded"
        for part in reversed((request.headers.get("x-forwarded-for") or "").split(",")):
            ip = _global_ip(part)
            if ip is not None:
                break
    if ip is None:
        return "unknown", "unknown"
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False)), source
    return str(ip), source


# ---------------------------------------------------------------- limits

class QuotaStateError(RuntimeError):
    """The usage counter exists but neither the file nor its backup can be read."""


def _utc_today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


@dataclass
class Limits:
    live: bool = False
    reason: str = ""
    per_day: int = 40
    per_client_per_day: int = 3
    max_upload_mb: int = 12
    # 0 means no cap. Otherwise live runs stop once the model calls of all live runs reach this many
    # dollars: each run reserves est_run_usd when it starts and settles to its own ledger total.
    budget_usd: float = 0.0
    est_run_usd: float = 0.4
    # The counter (UTC day, a salt for that day, salted visitor hashes, runs, dollars) is written
    # atomically together with a backup copy; without a file it lives in memory. When copies exist
    # but none parses, live runs pause instead of counting from zero. on_change runs after each save.
    state_file: pathlib.Path | None = None
    on_change: Callable[[], None] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _mem: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.state_file is None:
            return
        self.state_file = pathlib.Path(self.state_file)
        try:
            st = self._load()
        except QuotaStateError:
            return
        if st and "salt" not in st:  # written before visitors were hashed: drop the raw addresses now
            with self._lock:
                self._save(self._today_state(st))

    # ---- storage

    def _backup(self) -> pathlib.Path:
        return self.state_file.with_name(self.state_file.name + ".bak")

    def _load(self) -> dict:
        if self.state_file is None:
            return json.loads(json.dumps(self._mem))
        copies = [p for p in (self.state_file, self._backup()) if p.exists()]
        if not copies:
            return {}
        for p in copies:
            try:
                st = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(st, dict):
                return st
        raise QuotaStateError(f"{self.state_file.name} and its backup cannot be read")

    def _save(self, st: dict) -> None:
        if self.state_file is None:
            self._mem = json.loads(json.dumps(st))
        else:
            text = json.dumps(st)
            write_atomic(self.state_file, text)
            write_atomic(self._backup(), text)
        if self.on_change:
            try:
                self.on_change()
            except Exception as exc:  # noqa: BLE001
                log(f"quota on_change failed: {type(exc).__name__}")

    @staticmethod
    def _today_state(st: dict) -> dict:
        """The counter for the current UTC day. A new day starts a new salt and an empty visitor table,
        so yesterday's hashes cannot be linked to today's; the dollars carry over."""
        today = _utc_today()
        try:
            spent = float(st.get("spent_usd", 0.0))
            total = int(st.get("total", 0))
        except (TypeError, ValueError):
            raise QuotaStateError("the usage counter holds a value that is not a number") from None
        if st.get("day") == today and isinstance(st.get("salt"), str) and isinstance(st.get("clients"), dict):
            return {**st, "total": total, "spent_usd": spent}
        return {"day": today, "salt": secrets.token_hex(16), "total": total if st.get("day") == today else 0,
                "clients": {}, "spent_usd": spent}

    def _current(self) -> dict:
        try:
            return self._today_state(self._load())
        except QuotaStateError as exc:
            log(f"live runs paused: {exc}")
            raise HTTPException(503, PAUSED) from None

    @staticmethod
    def _visitor(st: dict, client: str) -> str:
        return hmac.new(st["salt"].encode(), client.encode(), hashlib.sha256).hexdigest()[:24]

    # ---- checks

    def _refuse_if_over(self, st: dict, client: str) -> None:
        if self.budget_usd and st["spent_usd"] + self.est_run_usd > self.budget_usd:
            raise HTTPException(429, "The live demo has used its model budget. The demo rooms show complete recorded runs.")
        if st["total"] >= self.per_day:
            raise HTTPException(429, "The live demo has used its runs for today. The demo rooms show complete recorded runs.")
        # A visitor with no usable address ("unknown") gets only the caps above: one shared bucket would let
        # a single person use up everyone's runs, and the daily and dollar caps still bound the spend.
        if client != "unknown" and st["clients"].get(self._visitor(st, client), 0) >= self.per_client_per_day:
            raise HTTPException(429, f"You have used your {self.per_client_per_day} live runs for today.")

    def problem(self) -> str:
        """A plain reason when live runs must pause because the counter cannot be read, else ""."""
        try:
            self._today_state(self._load())
        except QuotaStateError:
            return PAUSED
        return ""

    def left_today(self) -> int:
        try:
            st = self._today_state(self._load())
        except QuotaStateError:
            return 0
        n = max(0, self.per_day - st["total"])
        if self.budget_usd:
            n = min(n, max(0, int((self.budget_usd - st["spent_usd"]) // self.est_run_usd)))
        return n

    def check(self, client: str) -> None:
        """What take() would refuse, without writing anything: runs before the upload is read or decoded."""
        self._refuse_if_over(self._current(), client)

    def take(self, client: str) -> None:
        with self._lock:
            st = self._current()
            self._refuse_if_over(st, client)
            key = self._visitor(st, client)
            st["total"] += 1
            st["clients"][key] = st["clients"].get(key, 0) + 1
            st["spent_usd"] = round(st["spent_usd"] + self.est_run_usd, 4)
            self._save(st)

    def refund(self, client: str) -> None:
        """Undo a take() whose run never started (the server was busy or the photo would not load)."""
        with self._lock:
            try:
                st = self._load()
            except QuotaStateError as exc:
                log(f"refund skipped: {exc}")
                return
            if st.get("day") == _utc_today() and isinstance(st.get("salt"), str):
                st["total"] = max(0, int(st.get("total", 0)) - 1)
                clients = st.setdefault("clients", {})
                key = self._visitor(st, client)
                if clients.get(key):
                    clients[key] -= 1
            st["spent_usd"] = round(max(0.0, float(st.get("spent_usd", 0.0)) - self.est_run_usd), 4)
            self._save(st)

    def settle(self, usd: float | None) -> None:
        """Swap a finished run's reservation for what its ledger says it cost (kept if unknown)."""
        with self._lock:
            try:
                st = self._load()
            except QuotaStateError as exc:
                log(f"settle skipped: {exc}")
                return
            spent = float(st.get("spent_usd", 0.0)) - self.est_run_usd + (self.est_run_usd if usd is None else usd)
            st["spent_usd"] = round(max(0.0, spent), 4)
            self._save(st)


# ---------------------------------------------------------------- runs that stopped

def stop_run(path: pathlib.Path, data: dict, sentence: str, stopped_at: str) -> dict:
    """Mark a run that will never finish as stopped, with a plain sentence for the page, and save it."""
    events = [e for e in (data.get("events") or []) if isinstance(e, dict)]
    t = max((e["t"] for e in events if isinstance(e.get("t"), (int, float))), default=0)
    events.append({"t": t, "stage": "error", "title": "The run stopped", "detail": sentence})
    data = {**data, "status": "error", "error": sentence, "stopped_at": stopped_at, "events": events}
    write_atomic(pathlib.Path(path), json.dumps(data, ensure_ascii=False, indent=1))
    return data


def sweep_interrupted(runs_dir: pathlib.Path, older_than_s: float,
                      is_active: Callable[[str], bool] | None = None) -> list[str]:
    """Stop every run still "running" whose run.json has not changed for older_than_s seconds and that no
    worker here owns. Modal runs this when its container starts, when no worker can own one yet."""
    runs_dir = pathlib.Path(runs_dir)
    stopped: list[str] = []
    if not runs_dir.exists():
        return stopped
    now = time.time()
    for d in sorted(runs_dir.iterdir()):
        p = d / "run.json"
        if not d.is_dir() or d.name.startswith("_") or (is_active is not None and is_active(d.name)):
            continue
        try:
            if now - p.stat().st_mtime <= older_than_s:
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("status") == "running":
            stop_run(p, data, INTERRUPTED, "interrupted")
            stopped.append(d.name)
    return stopped


# ---------------------------------------------------------------- app

def create_app(runs_dir: pathlib.Path, catalog_dir: pathlib.Path, web_dir: pathlib.Path | None, limits: Limits,
               launcher: Launcher | None = None, demos_file: pathlib.Path | None = None,
               refresh: Callable[[], None] | None = None,
               photo_dirs: list[pathlib.Path] | None = None,
               is_active: Callable[[str], bool] | None = None) -> FastAPI:
    """is_active(run_id) says whether a worker in this process is still running that run. With it, a
    run.json left "running" for STALE_RUN_S seconds by a worker that is gone is marked as stopped."""
    app = FastAPI(title="Realroom", docs_url=None, redoc_url=None, openapi_url=None)
    runs_dir = pathlib.Path(runs_dir)
    catalog_dir = pathlib.Path(catalog_dir)
    photo_roots = [catalog_dir, *(pathlib.Path(d) for d in photo_dirs or [])]
    n_catalog = sum(1 for line in (catalog_dir / "catalog.jsonl").open(encoding="utf-8") if line.strip()) \
        if (catalog_dir / "catalog.jsonl").exists() else 0
    decode_slots = asyncio.Semaphore(DECODE_SLOTS)
    demos_cache: dict = {}

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            resp.headers.setdefault(name, value)
        if CSP:
            resp.headers.setdefault("Content-Security-Policy", CSP)
        p = request.url.path
        if resp.status_code == 200:
            if p.startswith("/runs/u"):  # a live run: someone's own room photo and what was made from it
                resp.headers["Cache-Control"] = "private, no-store"
                resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
            elif p.startswith("/catalog/") or (p.startswith("/runs/") and not p.endswith(".json")):
                resp.headers.setdefault("Cache-Control", "public, max-age=86400")
            elif p.startswith("/assets/"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @app.get("/api/config")
    def config():
        problem = limits.problem() if limits.live else ""
        live = bool(limits.live and launcher) and not problem
        return {"live": live, "reason": problem or limits.reason or None,
                "runs_left_today": limits.left_today() if limits.live and not problem else None,
                "catalog_size": n_catalog, "max_upload_mb": limits.max_upload_mb}

    def list_demos() -> list[dict]:
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

    @app.get("/api/demos")
    def demos():
        if refresh:
            refresh()
            return list_demos()
        try:
            stamp = demos_file.stat().st_mtime_ns if demos_file else None
        except OSError:
            stamp = None
        now = time.monotonic()
        if "out" in demos_cache and demos_cache["stamp"] == stamp and now - demos_cache["at"] < DEMOS_TTL_S:
            return demos_cache["out"]
        out = list_demos()
        demos_cache.update(stamp=stamp, at=now, out=out)
        return out

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        if not RUN_ID.fullmatch(run_id):
            raise HTTPException(404, "No such run.")
        if refresh:
            refresh()
        p = runs_dir / run_id / "run.json"
        if not p.exists():
            raise HTTPException(404, "No such run.")
        data = json.loads(p.read_text(encoding="utf-8"))
        if (is_active is not None and data.get("status") == "running" and not is_active(run_id)
                and time.time() - p.stat().st_mtime > STALE_RUN_S):
            data = stop_run(p, data, INTERRUPTED, "interrupted")
            log("a live run with no worker was marked as interrupted")
        data.pop("trace", None)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})

    @app.post("/api/runs")
    async def start_run(request: Request):
        client, source = client_identity(request)
        log(f"POST /api/runs client source {source}")
        if not (limits.live and launcher):
            raise HTTPException(503, limits.reason or "Live runs are off on this server.")
        site = request.headers.get("sec-fetch-site")
        if site is not None and site not in ("same-origin", "none"):
            raise HTTPException(403, "Live runs can only be started from the Realroom page itself.")
        # Size first, from the header, before a byte of the body is read or parsed.
        cap = limits.max_upload_mb * 1024 * 1024
        length = request.headers.get("content-length")
        if length is None:
            raise HTTPException(411, "The upload needs a Content-Length header.")
        if not length.isdigit():
            raise HTTPException(400, "The Content-Length header is not a number.")
        if int(length) > cap + MULTIPART_SLACK:
            raise HTTPException(413, f"The photo is larger than {limits.max_upload_mb} MB.")
        limits.check(client)  # a visitor or a day that is used up is refused before the upload is read
        async with request.form(max_files=1, max_fields=8, max_part_size=MAX_FIELD_BYTES) as form:
            photo, brief, mode = form.get("photo"), form.get("brief", ""), form.get("mode", "refresh")
            if not isinstance(photo, UploadFile):
                raise HTTPException(400, "Attach a photo of a room.")
            if not isinstance(brief, str):
                raise HTTPException(400, "The brief must be plain text.")
            if mode not in ("refresh", "furnish"):
                raise HTTPException(400, "Unknown mode.")
            raw = await photo.read(cap + 1)
        if len(raw) > cap:
            raise HTTPException(413, f"The photo is larger than {limits.max_upload_mb} MB.")
        async with decode_slots:
            jpeg = await run_in_threadpool(_normalize_photo, raw)
        # Nothing is awaited from here to the launcher: take() and launcher() run back to back on the
        # event loop, so the launcher's busy check sees every run started before this one.
        limits.take(client)
        run_id = dt.datetime.now(dt.timezone.utc).strftime("u%m%d%H%M") + secrets.token_hex(12)
        brief = re.sub(r"\s+", " ", brief).strip()[:280] or "Make it feel finished and comfortable."
        try:
            launcher(run_id, jpeg, brief, mode)
        except Exception:
            limits.refund(client)
            raise
        return {"id": run_id}

    @app.get("/runs/{run_id}/{name}")
    def run_file(run_id: str, name: str):
        if not RUN_ID.fullmatch(run_id) or not FILE_NAME.fullmatch(name):
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
        if which not in ("thumb", "img") or not PID.fullmatch(stem) or ext != ("webp" if which == "thumb" else "jpg"):
            raise HTTPException(404)
        p = next((r / which / name for r in photo_roots if (r / which / name).exists()), None)
        if p is None:
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


_UNREADABLE = "This file is not a photo Realroom can read (use JPEG, PNG or WebP)."
_DECODE_ERRORS = (OSError, ValueError, SyntaxError, EOFError, IndexError, struct.error)


def _normalize_photo(raw: bytes) -> bytes:
    """Decode, apply EXIF rotation, drop metadata, cap the size at 1792 px, re-encode as JPEG. Only JPEG,
    PNG and WebP are opened, and the pixel count is checked from the header before anything is decoded."""
    from PIL import Image, ImageOps

    too_many = f"The photo has too many pixels. Use one under {MAX_PIXELS // 1_000_000} megapixels."
    try:
        im = Image.open(io.BytesIO(raw), formats=PHOTO_FORMATS)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, too_many) from None
    except _DECODE_ERRORS:
        raise HTTPException(400, _UNREADABLE) from None
    w, h = im.size
    if w * h > MAX_PIXELS:  # anything Pillow would warn about is far past this, so the warning never decides
        raise HTTPException(400, too_many)
    try:
        im.load()
        im = ImageOps.exif_transpose(im).convert("RGB")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, too_many) from None
    except _DECODE_ERRORS:
        raise HTTPException(400, _UNREADABLE) from None
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
