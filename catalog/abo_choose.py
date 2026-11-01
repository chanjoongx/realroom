"""Pick one reference image per catalog product from the small-image scores (step 2).

A clean cutout (whole product alone on white) is what Muse Image copies best, so the main image
wins when it is one, otherwise the cleanest alternative, otherwise the main image as a lifestyle
shot. Dimension diagrams (white, but only thin lines) fail the solidity test.

usage: python catalog/abo_choose.py   (reads small_scores.json pulled from the Modal volume)
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import sys

DATA = pathlib.Path(os.environ.get("REALROOM_DATA", pathlib.Path(__file__).resolve().parents[1] / "data"))
ABO = DATA / "abo"


def is_cutout(s: dict | None) -> bool:
    return bool(s) and "error" not in s and s["border"] >= 0.85 and s["cov"] >= 0.10 and s["solid"] >= 0.30 \
        and s["fill"] >= 0.15


def quality(s: dict) -> float:
    return s["fill"] * s["solid"] * (0.5 + 0.5 * s["border"])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    scores = json.loads((ABO / "small_scores.json").read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in (ABO / "catalog_candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    chosen, why = {}, collections.Counter()
    per_kind = collections.defaultdict(collections.Counter)
    for r in rows:
        ims = [(im, scores.get(im["path"])) for im in r["images"]]
        main_im, main_s = ims[0]
        if main_s is None and all(s is None for _, s in ims):
            # added after the small-image pass: the fetch step scores the original instead
            why["unscored"] += 1
            per_kind[r["kind"]]["unscored"] += 1
            chosen[r["id"]] = {"image_id": main_im["id"], "path": main_im["path"], "cutout": None,
                               "reason": "unscored", "score": None, "orig_wh": [main_im["w"], main_im["h"]]}
            continue
        if is_cutout(main_s):
            pick, cut, reason = main_im, True, "main_cutout"
        else:
            alts = [(im, s) for im, s in ims[1:] if is_cutout(s)]
            if alts:
                pick, s = max(alts, key=lambda t: quality(t[1]))
                cut, reason = True, "alt_cutout"
            else:
                pick, cut, reason = main_im, False, "lifestyle"
        why[reason] += 1
        per_kind[r["kind"]][reason] += 1
        chosen[r["id"]] = {"image_id": pick["id"], "path": pick["path"], "cutout": cut, "reason": reason,
                           "score": scores.get(pick["path"]), "orig_wh": [pick["w"], pick["h"]]}
    (ABO / "chosen_images.json").write_text(json.dumps(chosen, indent=0), encoding="utf-8")
    print("chosen", len(chosen), dict(why))
    for k, c in sorted(per_kind.items(), key=lambda t: -sum(t[1].values())):
        print(f"  {k:14s} {dict(c)}")


if __name__ == "__main__":
    main()
