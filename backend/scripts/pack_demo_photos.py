"""Copy the product photos that the recorded demo runs show into demo-runs/_photos.

A fresh clone then replays the demo runs with every thumbnail and catalog photo in place, without the
full photo set. The photos are from Amazon Berkeley Objects (Amazon.com, CC BY 4.0), trimmed and resized.

usage: python backend/scripts/pack_demo_photos.py --photos data/photos [--runs demo-runs]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOTICE = """Product photos from Amazon Berkeley Objects (ABO) by Amazon.com, licensed CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/). Changes: one photo per product was chosen, its white
margins were trimmed, and it was resized (img/ at most 1024 px, thumb/ 480 px square). These are the
products the recorded demo runs show. Realroom is not affiliated with or endorsed by Amazon.
"""


def wanted(run: dict) -> tuple[set[str], set[str]]:
    """(thumbnails, full photos) one recorded run asks the web app for."""
    thumbs, imgs = set(), set()
    for s in (run.get("plan") or {}).get("slots", []):
        thumbs.update(s.get("shortlist") or [])
    for pid in run.get("products") or {}:  # each pick and its alternates
        thumbs.add(pid)
        imgs.add(pid)
    return thumbs, imgs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", required=True, help="img/ and thumb/ for every product")
    ap.add_argument("--runs", default=str(ROOT / "demo-runs"))
    args = ap.parse_args()
    src, runs = pathlib.Path(args.photos), pathlib.Path(args.runs)
    out = runs / "_photos"
    demos = json.loads((runs / "demos.json").read_text(encoding="utf-8"))
    thumbs, imgs = set(), set()
    for d in demos:
        t, i = wanted(json.loads((runs / d["id"] / "run.json").read_text(encoding="utf-8")))
        thumbs |= t
        imgs |= i
    if out.exists():
        shutil.rmtree(out)
    missing, size = [], 0
    for which, ids, ext in (("thumb", thumbs, "webp"), ("img", imgs, "jpg")):
        (out / which).mkdir(parents=True, exist_ok=True)
        for pid in sorted(ids):
            f = src / which / f"{pid}.{ext}"
            if not f.exists():
                missing.append(f"{which}/{pid}")
                continue
            shutil.copy2(f, out / which / f.name)
            size += f.stat().st_size
    (out / "NOTICE.txt").write_text(NOTICE, encoding="utf-8")
    print(f"{len(thumbs)} thumbnails, {len(imgs)} photos, {size / 1e6:.1f} MB in {out}")
    if missing:
        raise SystemExit(f"missing in {src}: {missing[:8]}")


if __name__ == "__main__":
    main()
