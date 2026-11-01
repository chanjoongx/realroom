"""Realroom on Modal.

One web container serves the site, the API and the catalog photos, and runs live jobs in
background threads (so a run's files are visible to the page the moment they are written).
Two volumes: `realroom-catalog` (product photos, catalog.jsonl, SigLIP 2 embeddings) and
`realroom-runs` (recorded demo runs, live runs). API keys come from the `realroom-keys` secret.

Live runs are capped at 40 a day, three per visitor and 60 dollars of model calls in all (each run
reserves 0.40 when it starts and settles to its ledger total). They are deleted 18 hours after the
upload: the web container checks every hour, and a small scheduled function wakes it every six
hours, so an uploaded photo is gone within 24 hours even when nobody visits.

deploy:  modal deploy backend/modal_app.py          (from the repo root, after `npm run build` in web/)
"""
from __future__ import annotations

import pathlib
import threading
import time

import modal

ROOT = pathlib.Path(__file__).resolve().parents[1]
SIGLIP = "google/siglip2-base-patch16-256"

app = modal.App("realroom")
cat_vol = modal.Volume.from_name("realroom-catalog")
runs_vol = modal.Volume.from_name("realroom-runs", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("fonts-dejavu-core")  # numbers on the pick sheets and labels on the guide image
    .pip_install("torch==2.6.0", index_url="https://download.pytorch.org/whl/cpu")
    .pip_install(  # the versions in backend/requirements.txt
        "numpy==2.5.4", "opencv-python-headless==5.0.0.93", "pillow==13.0.0", "scikit-image==0.26.0",
        "scipy==1.18.1", "requests==2.34.2", "fastapi[standard]==0.162.0", "python-multipart==0.0.33",
        "uvicorn==0.54.0", "openai==3.42.0", "meta-sam-parser==0.1.0", "transformers==4.51.3",
        "sentencepiece==0.2.2", "protobuf==7.36.2",
    )
    .run_commands(
        f"python -c \"from transformers import AutoModel, AutoProcessor; "
        f"AutoModel.from_pretrained('{SIGLIP}'); AutoProcessor.from_pretrained('{SIGLIP}')\""
    )
    .env({"HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
    .add_local_dir(ROOT / "backend" / "realroom", "/root/realroom")
    .add_local_dir(ROOT / "web" / "dist", "/web")
)

MAX_PARALLEL_RUNS = 3
RETAIN_HOURS = 18


@app.function(
    image=image,
    volumes={"/cat": cat_vol, "/runs": runs_vol},
    secrets=[modal.Secret.from_name("realroom-keys")],
    cpu=2.0,
    memory=4096,
    timeout=900,
    max_containers=1,
    scaledown_window=600,
)
@modal.concurrent(max_inputs=60)
@modal.asgi_app()
def web():
    import json
    import os

    from fastapi import HTTPException

    from realroom import imgutil
    from realroom.catalog import Catalog
    from realroom.pipeline import run_pipeline
    from realroom.server import Limits, create_app, purge_expired

    runs = pathlib.Path("/runs")
    cat_box: dict = {}
    cat_lock = threading.Lock()
    active: set[str] = set()

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

        def work():
            try:
                run_pipeline(room, catalog(), d, run_id, brief, mode=mode,
                             n_slots=3 if mode == "refresh" else 4, n_candidates=2, max_fixes=1,
                             room_meta={"title": "Your room", "ai_generated": False})
            finally:
                active.discard(run_id)
                try:
                    limits.settle(json.loads((d / "run.json").read_text(encoding="utf-8")).get("cost_usd"))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    runs_vol.commit()
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=work, daemon=True).start()

    def tidy() -> None:
        while True:
            try:
                listed = json.loads((runs / "demos.json").read_text(encoding="utf-8")) \
                    if (runs / "demos.json").exists() else []
                if purge_expired(runs, RETAIN_HOURS, {d["id"] for d in listed}, set(active)):
                    runs_vol.commit()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3600)

    threading.Thread(target=tidy, daemon=True).start()

    live = os.environ.get("REALROOM_LIVE", "1") == "1"
    limits = Limits(live=live, reason="" if live else "Live runs are paused right now.",
                    per_day=int(os.environ.get("REALROOM_RUNS_PER_DAY", "40")),
                    per_client_per_day=int(os.environ.get("REALROOM_RUNS_PER_CLIENT", "3")),
                    budget_usd=float(os.environ.get("REALROOM_BUDGET_USD", "60")),
                    state_file=runs / "_quota.json")
    return create_app(runs, pathlib.Path("/cat"), pathlib.Path("/web"), limits, launch,
                      demos_file=runs / "demos.json")


@app.function(image=modal.Image.debian_slim(python_version="3.12").pip_install("requests==2.34.2"),
              schedule=modal.Period(hours=6), timeout=180)
def wake() -> None:
    """Start the web container every six hours so expired live runs are deleted even with no visitors."""
    import os

    import requests

    url = os.environ.get("REALROOM_URL") or web.get_web_url()
    requests.get(url.rstrip("/") + "/api/config", timeout=150)
