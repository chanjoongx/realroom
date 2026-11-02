"""Serve the API and the built web app locally.

usage: python backend/scripts/serve_local.py [--runs demo-runs] [--catalog catalog/index]
           [--photos data/photos] [--live] [--per-day 10] [--port 8787]

Without --live it replays the recorded runs in --runs and needs no key: the product photos those runs
show are in demo-runs/_photos. With --live, uploaded photos run through the full pipeline in a
background thread. That calls the Meta Model API (MODEL_API_KEY, costs money, at most --per-day runs a
day) and needs every product photo in --photos (python catalog/get_photos.py).
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

from realroom import imgutil  # noqa: E402
from realroom.server import Limits, create_app  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]


def write_placeholder(d: pathlib.Path, run_id: str, jpeg: bytes, brief: str, mode: str) -> None:
    """run.json and the photo exist before the worker starts, so the page can open the run at once."""
    import json

    room = imgutil.load_rgb(jpeg)
    d.mkdir(parents=True, exist_ok=True)
    imgutil.save(room, d / "before.jpg", 94)
    h, w = room.shape[:2]
    (d / "run.json").write_text(json.dumps({
        "id": run_id, "version": 1, "status": "running", "events": [],
        "room": {"w": w, "h": h, "before": "before.jpg", "title": "Your room", "ai_generated": False},
        "brief": brief, "mode": mode}), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "demo-runs"), help="run folders and demos.json")
    ap.add_argument("--catalog", default=str(ROOT / "catalog" / "index"),
                    help="catalog.jsonl and the Perception Encoder embeddings")
    ap.add_argument("--photos", default=str(ROOT / "data" / "photos"),
                    help="img/ and thumb/ for every product; live runs need them")
    ap.add_argument("--web", default=str(ROOT / "web" / "dist"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--per-day", type=int, default=10, help="live runs this server starts per day")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    runs = pathlib.Path(args.runs)
    cat_dir = pathlib.Path(args.catalog)
    photos = pathlib.Path(args.photos)
    if not (photos / "img").is_dir() and (cat_dir / "img").is_dir():
        photos = cat_dir  # a catalog folder that holds its own photos

    launcher, reason = None, "Live runs are off on this local server (start it with --live)."
    if args.live and not (photos / "img").is_dir():
        reason = f"Live runs need the product photos in {photos} (python catalog/get_photos.py)."
        print(reason, flush=True)
    elif args.live:
        from realroom.catalog import Catalog
        from realroom.pipeline import run_pipeline

        cat_box: dict = {}
        lock = threading.Lock()

        def launch(run_id: str, jpeg: bytes, brief: str, mode: str) -> None:
            write_placeholder(runs / run_id, run_id, jpeg, brief, mode)

            def work():
                with lock:
                    if "cat" not in cat_box:
                        cat_box["cat"] = Catalog(cat_dir, photos)
                room = imgutil.load_rgb(jpeg)
                run_pipeline(room, cat_box["cat"], runs / run_id, run_id, brief, mode=mode,
                             n_slots=3 if mode == "refresh" else 4, n_candidates=2, max_fixes=1,
                             room_meta={"title": "Your room", "ai_generated": False})

            threading.Thread(target=work, daemon=True).start()

        launcher = launch

    limits = Limits(live=launcher is not None, reason="" if launcher else reason, per_day=args.per_day,
                    per_client_per_day=args.per_day, state_file=runs / "_quota.json")
    app = create_app(runs, cat_dir, pathlib.Path(args.web), limits, launcher, demos_file=runs / "demos.json",
                     photo_dirs=[photos, runs / "_photos"])
    print(f"Realroom on http://127.0.0.1:{args.port}  (runs {runs}, live {'on' if launcher else 'off'})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
