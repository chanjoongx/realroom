"""Run the full Realroom agent on one room photo and write run.json plus images.

usage: python backend/scripts/run_demo.py --room <png> --id R2 --mode refresh --slots 3 \
           --brief "..." --catalog catalog/index --photos data/photos --out <dir>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from realroom import imgutil  # noqa: E402
from realroom.catalog import Catalog  # noqa: E402
from realroom.pipeline import run_pipeline  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--mode", default="refresh", choices=["refresh", "furnish"])
    ap.add_argument("--slots", type=int, default=3)
    ap.add_argument("--cands", type=int, default=3)
    ap.add_argument("--fixes", type=int, default=2)
    ap.add_argument("--rounds", type=int, default=2, help="redraw rounds per weak product")
    ap.add_argument("--tries", type=int, default=2, help="redraws per round, all judged")
    ap.add_argument("--catalog", required=True, help="catalog.jsonl and the embeddings (catalog/index)")
    ap.add_argument("--photos", default=None, help="img/ and thumb/ for every product (default: --catalog)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    t0 = time.time()
    cat = Catalog(args.catalog, args.photos)
    print(f"catalog {len(cat.products)} products, embeddings {'yes' if cat.emb is not None else 'no'} "
          f"({time.time() - t0:.1f} s)", flush=True)
    room = imgutil.load_rgb(args.room)

    def show(ev: dict) -> None:
        extra = " ".join(f"{k}={ev[k]}" for k in ("model", "sec", "usd") if ev.get(k) is not None)
        print(f"[{ev['t']:7.1f}s] {ev['stage']:<7} {ev['title']}  {extra}", flush=True)
        if ev.get("detail"):
            print(f"           {ev['detail'][:400]}", flush=True)

    data = run_pipeline(room, cat, args.out, args.id, args.brief, mode=args.mode, n_slots=args.slots,
                        n_candidates=args.cands, max_fixes=args.fixes, fix_rounds=args.rounds, fix_tries=args.tries,
                        room_meta={"title": args.title, "ai_generated": True,
                                   "made_with": "Muse Image 1.0 (AI-generated demo room)"},
                        on_event=show)
    print("status", data["status"], "| cost USD", data.get("cost_usd"), "| elapsed s", data.get("elapsed_s"))
    if data["status"] != "done":
        print(data.get("trace", ""))
    else:
        r = data["result"]
        print("proof", json.dumps(r["proof"]))
        print("scene", json.dumps({k: v for k, v in r["scene"].items() if k != "artifacts"}, ensure_ascii=False)[:600])


if __name__ == "__main__":
    main()
