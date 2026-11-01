"""Serve the API and the built web app locally.

usage: python backend/scripts/serve_local.py --runs <dir with run folders and demos.json> \
           --catalog <dir with catalog.jsonl, thumb/, img/> [--live] [--port 8787]

With --live, uploaded photos run through the full pipeline in a background thread (this calls the
Meta APIs and costs money; the same daily caps as the hosted app apply).
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
    ap.add_argument("--runs", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--web", default=str(ROOT / "web" / "dist"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    runs = pathlib.Path(args.runs)
    cat_dir = pathlib.Path(args.catalog)

    launcher = None
    if args.live:
        from realroom.catalog import Catalog
        from realroom.pipeline import run_pipeline

        cat_box: dict = {}
        lock = threading.Lock()

        def launch(run_id: str, jpeg: bytes, brief: str, mode: str) -> None:
            write_placeholder(runs / run_id, run_id, jpeg, brief, mode)

            def work():
                with lock:
                    if "cat" not in cat_box:
                        cat_box["cat"] = Catalog(cat_dir)
                room = imgutil.load_rgb(jpeg)
                run_pipeline(room, cat_box["cat"], runs / run_id, run_id, brief, mode=mode,
                             n_slots=3 if mode == "refresh" else 4, n_candidates=2, max_fixes=1,
                             room_meta={"title": "Your room", "ai_generated": False})

            threading.Thread(target=work, daemon=True).start()

        launcher = launch

    limits = Limits(live=args.live, reason="" if args.live else "Live runs are off on this local server.",
                    state_file=runs / "_quota.json")
    app = create_app(runs, cat_dir, pathlib.Path(args.web), limits, launcher, demos_file=runs / "demos.json")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
