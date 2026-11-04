"""Realroom on Modal.

One web container serves the site, the API and the catalog photos, and runs live jobs in
background threads (so a run's files are visible to the page the moment they are written).
Two volumes: `realroom-catalog` (product photos, catalog.jsonl, Perception Encoder embeddings), mounted
read-only, and `realroom-runs` (recorded demo runs, live runs). The web container gets one secret,
`realroom-model`, which holds MODEL_API_KEY and nothing else; the catalog-tagging keys live in
`realroom-keys`, which only catalog/modal_catalog.py uses.

Live runs are capped at 40 a UTC day, 8 a UTC hour, three per visitor (and six per IPv6 /48) and 60
dollars of model calls in all (each run reserves 0.40 when it starts and settles to its ledger
total). They are deleted 18 hours after
the upload: the web container checks every hour, and a small scheduled function wakes it every
three hours, so an uploaded photo is gone within about 21 hours even when nobody visits.

deploy:  modal deploy backend/modal_app.py          (from the repo root, after `npm run build` in web/)
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import threading
import time

import modal

ROOT = pathlib.Path(__file__).resolve().parents[1]
PE_CORE = "PE-Core-B-16"  # Meta Perception Encoder (open_clip name); the look search runs its text tower


def _version() -> str:
    """The commit being deployed (git rev-parse --short HEAD), or "dev". Inside the container this
    module is imported again, and the value then comes from the image env set at deploy time."""
    if not modal.is_local():
        return os.environ.get("REALROOM_VERSION", "dev")
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "dev"
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "dev"


VERSION = _version()
# Never ship a key file that happens to sit in a mounted folder.
NO_SECRETS = ["**/.env", "**/.env.*", "**/*.env", "**/__pycache__"]

app = modal.App("realroom")
cat_vol = modal.Volume.from_name("realroom-catalog")
runs_vol = modal.Volume.from_name("realroom-runs", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("fonts-dejavu-core")  # numbers on the pick sheets and labels on the guide image
    .pip_install("torch==2.6.0", "torchvision==0.21.0", index_url="https://download.pytorch.org/whl/cpu")
    .pip_install(  # the versions in backend/requirements.txt
        "numpy==2.5.4", "opencv-python-headless==5.0.0.93", "pillow==13.0.0", "scikit-image==0.26.0",
        "scipy==1.18.1", "requests==2.34.2", "fastapi[standard]==0.162.0", "python-multipart==0.0.33",
        "uvicorn==0.54.0", "openai==3.42.0", "meta-sam-parser==0.1.0", "open_clip_torch==3.3.0", "timm==1.0.30",
    )
    .run_commands(  # bake the weights into the image, so a live run never downloads a model
        f"python -c \"import open_clip; open_clip.create_model_and_transforms('{PE_CORE}', pretrained='meta')\""
    )
    .env({"HF_HUB_OFFLINE": "1", "REALROOM_VERSION": VERSION})
    .add_local_dir(ROOT / "backend" / "realroom", "/root/realroom", ignore=NO_SECRETS)
    .add_local_dir(ROOT / "web" / "dist", "/web", ignore=NO_SECRETS)
)

MAX_PARALLEL_RUNS = 3
RETAIN_HOURS = 18
STALE_AT_START_S = 15 * 60  # at startup no worker here owns a run, so "running" this long means stopped
# The web container decodes visitors' uploads, so it holds the one key a live run needs and no other.
WEB_SECRET = "realroom-model"


@app.function(
    image=image,
    volumes={"/cat": cat_vol.read_only(), "/runs": runs_vol},
    secrets=[modal.Secret.from_name(WEB_SECRET, required_keys=["MODEL_API_KEY"])],
    cpu=2.0,
    memory=4096,
    timeout=900,
    # Keep this at 1. The usage counter's lock, the set of active runs, the decode slots and the
    # worker threads all live in this one container; a second container would count on its own.
    max_containers=1,
    scaledown_window=600,
)
@modal.concurrent(max_inputs=60)
@modal.asgi_app(label="realroom")
def web():
    import json

    from fastapi import HTTPException

    from realroom import imgutil
    from realroom.catalog import Catalog
    from realroom.pipeline import run_pipeline
    from realroom.server import Limits, create_app, log, purge_expired, stop_run, sweep_interrupted

    runs = pathlib.Path("/runs")
    cat_box: dict = {}
    cat_lock = threading.Lock()
    commit_lock = threading.Lock()
    active: set[str] = set()

    def is_active(run_id: str) -> bool:
        return run_id in active

    def commit(why: str) -> None:
        """Commit the runs volume in a background thread, so no request waits for it."""
        def go() -> None:
            with commit_lock:
                try:
                    runs_vol.commit()
                except Exception as exc:  # noqa: BLE001
                    log(f"runs volume commit after {why} failed: {type(exc).__name__}")

        threading.Thread(target=go, daemon=True).start()

    def catalog() -> Catalog:
        with cat_lock:
            if "cat" not in cat_box:
                cat_box["cat"] = Catalog("/cat")
            return cat_box["cat"]

    def launch(run_id: str, jpeg: bytes, brief: str, mode: str) -> None:
        if len(active) >= MAX_PARALLEL_RUNS:
            raise HTTPException(503, "Realroom is busy with other live runs. Try again in a few minutes.")
        room = imgutil.load_rgb(jpeg)
        d = runs / run_id
        d.mkdir(parents=True, exist_ok=True)
        imgutil.save(room, d / "before.jpg", 94)
        h, w = room.shape[:2]
        (d / "run.json").write_text(json.dumps({
            "id": run_id, "version": 1, "status": "running", "events": [],
            "room": {"w": w, "h": h, "before": "before.jpg", "title": "Your room", "ai_generated": False},
            "brief": brief, "mode": mode}), encoding="utf-8")
        active.add(run_id)

        def stop(sentence: str, stopped_at: str) -> None:
            try:
                p = d / "run.json"
                stop_run(p, json.loads(p.read_text(encoding="utf-8")), sentence, stopped_at)
            except Exception as exc:  # noqa: BLE001
                log(f"could not mark a failed live run as stopped: {type(exc).__name__}")

        def work():
            try:
                try:
                    cat = catalog()
                except Exception as exc:  # noqa: BLE001
                    log(f"catalog failed to load: {type(exc).__name__}")
                    stop("The product catalog could not be loaded. Please try again later.", "catalog")
                    return
                run_pipeline(room, cat, d, run_id, brief, mode=mode,
                             n_slots=3 if mode == "refresh" else 4, n_candidates=2, max_fixes=1,
                             room_meta={"title": "Your room", "ai_generated": False})
            except Exception as exc:  # noqa: BLE001  (run_pipeline records its own errors; this is a backstop)
                log(f"live run worker failed: {type(exc).__name__}")
                stop("The run stopped because of a server error. Please try again.", "server")
            finally:
                active.discard(run_id)
                try:
                    limits.settle(json.loads((d / "run.json").read_text(encoding="utf-8")).get("cost_usd"))
                except Exception as exc:  # noqa: BLE001
                    log(f"settling a live run's cost failed: {type(exc).__name__}")
                commit("a live run")

        threading.Thread(target=work, daemon=True).start()

    def tidy() -> None:
        while True:
            try:
                listed = json.loads((runs / "demos.json").read_text(encoding="utf-8")) \
                    if (runs / "demos.json").exists() else []
                gone = purge_expired(runs, RETAIN_HOURS, {d["id"] for d in listed}, set(active))
                if gone:
                    log(f"deleted {len(gone)} expired live runs")
                    commit("the hourly purge")
            except Exception as exc:  # noqa: BLE001
                log(f"hourly purge failed: {type(exc).__name__}")
            time.sleep(3600)

    try:
        stopped = sweep_interrupted(runs, STALE_AT_START_S, is_active)
        if stopped:
            log(f"marked {len(stopped)} interrupted live runs as stopped")
            commit("the startup sweep")
    except Exception as exc:  # noqa: BLE001
        log(f"startup sweep failed: {type(exc).__name__}")

    threading.Thread(target=tidy, daemon=True).start()

    live = os.environ.get("REALROOM_LIVE", "1") == "1"
    limits = Limits(live=live, reason="" if live else "Live runs are paused right now.",
                    per_day=int(os.environ.get("REALROOM_RUNS_PER_DAY", "40")),
                    per_client_per_day=int(os.environ.get("REALROOM_RUNS_PER_CLIENT", "3")),
                    per_hour=int(os.environ.get("REALROOM_RUNS_PER_HOUR", "8")),
                    budget_usd=float(os.environ.get("REALROOM_BUDGET_USD", "60")),
                    state_file=runs / "_quota.json", on_change=lambda: commit("a usage counter change"))
    log(f"web container up, version {VERSION}")
    return create_app(runs, pathlib.Path("/cat"), pathlib.Path("/web"), limits, launch,
                      demos_file=runs / "demos.json", is_active=is_active, warm_variants=True)


@app.function(image=modal.Image.debian_slim(python_version="3.12").pip_install("requests==2.34.2"),
              schedule=modal.Period(hours=3), timeout=180)
def wake() -> None:
    """Start the web container every three hours so expired live runs are deleted even with no visitors."""
    import os

    import requests

    url = os.environ.get("REALROOM_URL") or web.get_web_url()
    requests.get(url.rstrip("/") + "/api/config", timeout=150)
