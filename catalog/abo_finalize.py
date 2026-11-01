"""Merge the catalog pieces into the file the app reads: data/catalog/catalog.jsonl.

candidates (listing metadata, real sizes) + chosen reference image + fetch results (cutout, trim)
+ Muse Spark Open tags. Products whose photo does not show the listed product are dropped.

usage: python catalog/abo_finalize.py
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import re
import sys

DATA = pathlib.Path(os.environ.get("REALROOM_DATA", pathlib.Path(__file__).resolve().parents[1] / "data"))
ABO = DATA / "abo"
OUT = DATA / "catalog"
DASH = re.compile("[" + "".join(map(chr, (0x2012, 0x2013, 0x2014, 0x2015))) + "]")  # dash-like characters


def clean(v):
    if isinstance(v, str):
        return DASH.sub(", ", v).replace(" ,", ",")
    if isinstance(v, list):
        return [clean(x) for x in v]
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    return v


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    rows = [json.loads(l) for l in (ABO / "catalog_candidates.jsonl").read_text(encoding="utf-8").splitlines() if l]
    chosen = json.loads((ABO / "chosen_images.json").read_text(encoding="utf-8"))
    fetched = {r["id"]: r for r in json.loads((ABO / "fetch_results.json").read_text(encoding="utf-8"))}
    tags = {}
    for line in (ABO / "tags.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                t = json.loads(line)
            except json.JSONDecodeError:  # the tagger may still be appending the last line
                continue
            if "tag" in t:
                tags[t["id"]] = t
    OUT.mkdir(parents=True, exist_ok=True)
    kept, why = [], collections.Counter()
    for r in rows:
        f = fetched.get(r["id"])
        if not f or f.get("error"):
            why["no_image"] += 1
            continue
        t = tags.get(r["id"])
        if not t:
            why["no_tag"] += 1
            continue
        tag = clean(t["tag"])
        if not tag.get("matches_listing", True):
            why["photo_not_listing"] += 1
            continue
        r = {k: v for k, v in r.items() if k not in ("images", "bullets")}
        r.update({"image_id": chosen[r["id"]]["image_id"], "cutout": bool(f.get("cutout")), "img_wh": f.get("img_wh"),
                  "tag": tag, "tag_by": t.get("provider")})
        kept.append(r)
    with (OUT / "catalog.jsonl").open("w", encoding="utf-8") as fh:
        for r in kept:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    by = collections.Counter(r["kind"] for r in kept)
    risk = collections.Counter(r["tag"]["render_risk"] for r in kept)
    prov = collections.Counter(r["tag_by"] for r in kept)
    print(f"kept {len(kept)} of {len(rows)}; dropped {dict(why)}")
    print("by kind:", ", ".join(f"{k} {v}" for k, v in by.most_common()))
    print("render risk:", dict(risk), "| tagged by:", dict(prov))
    bad = sum(1 for r in kept if DASH.search(json.dumps(r, ensure_ascii=False)))
    print("rows with an en/em dash:", bad)


if __name__ == "__main__":
    main()
