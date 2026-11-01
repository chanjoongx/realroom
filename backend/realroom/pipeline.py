"""Realroom agent: one room photo in, one verified makeover out.

  scan     Muse Spark 1.3 reads the room and plans; SAM 3.1 outlines every object
  search   catalog funnel per slot: kind -> fits the room -> looks right (open-weight index)
  pick     Muse Spark 1.3 chooses a set from numbered contact sheets
  render   Muse Image 1.0 draws n candidates with the real product photos
  verify   align + mask-guarded composite + Muse Spark 1.3 inspection of every product
  fix      a product that is only partly right is cut out, redrawn bigger, checked again
  result   original pixels everywhere except the new products, with the proof

Everything the interface shows is written to <out>/run.json plus images next to it. Every model
call is logged with its latency and an estimated cost.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import pathlib
import re
import time
import traceback
from typing import Callable

import numpy as np

from . import composite as C
from . import imgutil, meta, pick, render, scan, verify
from .catalog import Catalog

Event = Callable[[dict], None]


class UserFacingError(RuntimeError):
    """A run that stops for a reason the person can fix (wrong kind of photo)."""


class Run:
    def __init__(self, out_dir: pathlib.Path, run_id: str, on_event: Event | None = None):
        self.dir = pathlib.Path(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.id = run_id
        self.t0 = time.time()
        self.events: list[dict] = []
        self.on_event = on_event
        self.ledger = meta.Ledger(self.dir / "ledger.jsonl")
        self.data: dict = {"id": run_id, "version": 1, "status": "running", "events": self.events}

    def event(self, stage: str, title: str, detail: str = "", **extra) -> None:
        ev = {"t": round(time.time() - self.t0, 2), "stage": stage, "title": title, "detail": detail, **extra}
        self.events.append(ev)
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:  # noqa: BLE001
                pass
        self.save()

    def save_img(self, arr: np.ndarray, name: str, quality: int = 90) -> str:
        imgutil.save(arr, self.dir / name, quality=quality)
        return name

    def save(self) -> None:
        self.data["elapsed_s"] = round(time.time() - self.t0, 1)
        self.data["cost_usd"] = self.ledger.total_usd()
        tmp = self.dir / "run.json.tmp"
        tmp.write_text(json.dumps(tidy(self.data), ensure_ascii=False, indent=1, default=_json_default),
                       encoding="utf-8")
        tmp.replace(self.dir / "run.json")


_DASHES = "".join(chr(c) for c in (0x2012, 0x2013, 0x2014, 0x2015))
_RANGE = re.compile(r"(\d)(?:\s*[" + _DASHES + r"]\s*| - )(\d)")
_SPACED = re.compile(r"(?<=\S) [-" + _DASHES + chr(0x2212) + r"]{1,2} (?=\S)")
_DASH = re.compile(r"\s*[" + _DASHES + r"]\s*")


def tidy(o):
    """Model text uses dashes as punctuation and the interface does not: a spaced hyphen or a long
    dash between words becomes a comma, a dash between numbers becomes "to". Applied to every
    string in run.json."""
    if isinstance(o, str):
        if "-" not in o and not _DASH.search(o):
            return o
        o = _RANGE.sub(r"\1 to \2", o)
        o = _SPACED.sub(", ", o)
        return _DASH.sub(", ", o)
    if isinstance(o, dict):
        return {k: tidy(v) for k, v in o.items()}
    if isinstance(o, list):
        return [tidy(v) for v in o]
    return o


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(type(o))


def _cost_since(ledger: meta.Ledger, n0: int) -> float:
    return round(sum(r.get("usd", 0) for r in ledger.rows()[n0:]), 4)


def run_pipeline(room: np.ndarray, cat: Catalog, out_dir: str | pathlib.Path, run_id: str, brief: str,
                 mode: str = "refresh", n_slots: int = 3, n_candidates: int = 3, room_meta: dict | None = None,
                 on_event: Event | None = None, max_fixes: int = 2, fix_rounds: int = 2, fix_tries: int = 2) -> dict:
    R = Run(pathlib.Path(out_dir), run_id, on_event)
    h, w = room.shape[:2]
    R.data.update({"room": {"w": w, "h": h, "before": R.save_img(room, "before.jpg", 94), **(room_meta or {})},
                   "brief": brief, "mode": mode})
    try:
        _run(R, room, cat, brief, mode, n_slots, n_candidates, max_fixes, fix_rounds, fix_tries)
        R.data["status"] = "done"
    except UserFacingError as exc:
        R.data["status"] = "error"
        R.data["error"] = str(exc)
        R.event("error", "The run stopped", str(exc))
    except Exception as exc:  # noqa: BLE001
        R.data["status"] = "error"
        R.data["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        R.data["trace"] = traceback.format_exc()[-2000:]
        R.event("error", "The run stopped", R.data["error"])
    R.save()
    return R.data


def _run(R: Run, room: np.ndarray, cat: Catalog, brief: str, mode: str, n_slots: int, n_candidates: int,
         max_fixes: int, fix_rounds: int = 2, fix_tries: int = 2) -> None:
    h, w = room.shape[:2]
    L = R.ledger

    # ------------------------------------------------------------------ scan
    R.event("scan", "Reading the room", "Muse Spark 1.3 lists every object, the fixed structure, size and light",
            model="muse-spark-1.3", status="start")
    n0 = len(L.rows())
    sc = scan.scan(L, room, brief, mode, n_slots, tag="scan")
    pc = sc.get("photo_check") or {}
    if pc.get("people_visible"):
        raise UserFacingError("The photo shows a person. Realroom only edits photos of rooms, so please use one "
                              "without people in it.")
    if pc.get("is_room_interior") is False:
        raise UserFacingError("This does not look like a photo of a room. " + (pc.get("problem") or ""))
    st = sc["stats"]
    R.event("scan", f"Found {st['objects']} objects and {len(sc['fixed'])} fixed elements",
            f"{sc['room_type']}, about {sc['dimensions_cm']['width']:g} x {sc['dimensions_cm']['depth']:g} cm "
            f"({sc['dimensions_cm']['confidence']} confidence). {sc['summary']}",
            model="muse-spark-1.3", sec=st["spark_sec"], status="done")
    R.event("scan", f"Outlined {st['outlined']} objects with SAM 3.1",
            f"{st['sam_concepts']} concept prompts found {st['by_concept']} objects, "
            f"{st['sam_box_requests']} box-prompt request(s) for the rest; Spark box vs SAM box IoU {st['mean_iou']}",
            model="sam-3.1", sec=st["sam_sec"], usd=_cost_since(L, n0), status="done")
    objects_public = [{k: v for k, v in o.items() if k != "_mask"} for o in sc["objects"]]
    R.data["scan"] = {k: v for k, v in sc.items() if k != "objects"} | {"objects": objects_public}
    slots = sc["plan"]["slots"]
    R.event("plan", sc["plan"]["title"], sc["plan"]["idea"], model="muse-spark-1.3", status="done",
            slots=[{"id": s["id"], "kind": s["kind"], "action": s["action"]} for s in slots])

    # ------------------------------------------------------------------ search
    t = time.time()
    shortlists, funnels = {}, {}
    used: set[str] = set()
    for s in slots:
        res = cat.search(s["kind"], s["look"], s["size_cm"], k=24, exclude=used, color_family=s.get("color_family"))
        shortlists[s["id"]] = [x["id"] for x in res["items"]]
        funnels[s["id"]] = res["funnel"]
    R.event("search", f"Searched {len(cat.products):,} real products",
            "; ".join(f"{s['kind'].replace('_', ' ')}: {funnels[s['id']]['kind']} > {funnels[s['id']]['fits']} fit > "
                      f"{funnels[s['id']].get('shortlist', 0)}" for s in slots),
            model="catalog index + siglip-2", sec=round(time.time() - t, 2), status="done")

    # ------------------------------------------------------------------ pick
    n0 = len(L.rows())
    t = time.time()
    pk = pick.pick_products(L, cat, room, sc["plan"], slots, shortlists, tag="pick")
    for sid, sheet in pk["sheets"].items():
        R.save_img(sheet, f"sheet_{sid}.jpg", 86)
    picks = pk["picks"]
    slots = [s for s in slots if s["id"] in picks]
    products = [cat.products[picks[s["id"]]["pick"]] for s in slots]
    R.data["plan"] = {
        "title": sc["plan"]["title"], "idea": sc["plan"]["idea"], "style": sc["plan"]["style"],
        "palette": sc["plan"]["palette"], "set_comment": pk["set_comment"],
        "slots": [{**{k: v for k, v in s.items()}, "funnel": funnels[s["id"]], "shortlist": shortlists[s["id"]],
                   "sheet": f"sheet_{s['id']}.jpg", **picks[s["id"]]} for s in slots],
    }
    R.data["products"] = {pid: cat.products[pid].public() for s in slots
                          for pid in [picks[s["id"]]["pick"], *picks[s["id"]]["alternates"]]}
    R.event("pick", "Chose " + ", ".join(p.title for p in products), pk["set_comment"], model="muse-spark-1.3",
            sec=round(time.time() - t, 1), usd=_cost_since(L, n0), status="done")

    # ------------------------------------------------------------------ render
    n0 = len(L.rows())
    cards = [imgutil.product_card(imgutil.load_rgb(cat.image_path(p.id))) for p in products]
    guide = render.guide_image(room, slots)
    R.save_img(guide, "guide.jpg", 86)
    prompt = render.edit_prompt(sc, slots, products, 2)
    R.event("render", f"Drawing {n_candidates} candidates", "Muse Image 1.0 gets the room, a placement guide and "
            f"{len(products)} real product photos", model="muse-image-1.0", status="start")
    outs, gen_sec = render.generate(L, room, guide, cards, prompt, n=n_candidates, tag="render")
    R.event("render", f"{len(outs)} candidates in {gen_sec:.0f} s", "", model="muse-image-1.0", sec=gen_sec,
            usd=_cost_since(L, n0), status="done")
    if not outs:
        raise RuntimeError("the image model returned no candidates")

    # ------------------------------------------------------------------ verify candidates
    n0 = len(L.rows())
    t = time.time()
    removals = [o["name"] for o in sc["objects"] if o["class"] == "remove"]

    def check(i_b):
        i, b = i_b
        cc = render.composite_candidate(L, room, b, sc, slots, products, tag=f"verify:c{i + 1}")
        crops = [imgutil.crop_box(cc["comp"], box, margin=0.12) for box in cc["boxes"]]
        j = verify.judge_scene(L, room, cc["comp"], products, cards, crops, removals, tag=f"verify:c{i + 1}")
        return i, cc, crops, j

    with cf.ThreadPoolExecutor(max_workers=len(outs)) as pool:
        checked = list(pool.map(check, enumerate(outs)))
    cands = []
    for i, cc, crops, j in checked:
        score = verify.candidate_score(j, len(products))
        R.save_img(cc["raw"], f"cand_{i + 1}_raw.jpg", 88)
        R.save_img(cc["comp"], f"cand_{i + 1}.jpg", 90)
        cands.append({"index": i + 1, "score": score, "judge": j, "raw": f"cand_{i + 1}_raw.jpg",
                      "comp": f"cand_{i + 1}.jpg", "drift": cc["preservation"], "align": cc["align"],
                      "seg_how": cc["seg_how"]})
    best_i = max(range(len(checked)), key=lambda k: cands[k]["score"])
    _, best, best_crops, best_j = checked[best_i]
    R.data["render"] = {"candidates": cands, "chosen": best_i + 1, "prompt": prompt}
    verdicts = {p["index"]: p for p in best_j.get("products", [])}
    R.event("verify", f"Inspected {len(products) * len(outs)} product renders, kept candidate {best_i + 1}",
            ", ".join(f"{p.title}: {verdicts.get(k + 1, {}).get('verdict', '?')} "
                      f"{verdicts.get(k + 1, {}).get('score', '?')}" for k, p in enumerate(products)),
            model="muse-spark-1.3 + sam-3.1", sec=round(time.time() - t, 1), usd=_cost_since(L, n0), status="done",
            scores=[c["score"] for c in cands])

    # ------------------------------------------------------------------ fix
    comp = best["comp"]
    boxes = list(best["boxes"])
    masks = [f[0]["mask"] if f[0] is not None else None for f in best["found"]]
    fixes = []
    weak = sorted([k for k in range(len(products)) if verdicts.get(k + 1, {}).get("verdict") in ("partial", "mismatch")],
                  key=lambda k: verdicts[k + 1]["score"])[:max_fixes]
    keep_union = np.zeros((h, w), dtype=bool)  # objects that must not change: a redraw never grows into them
    for o in sc["objects"]:
        if o.get("_mask") is not None:
            keep_union |= o["_mask"]
    for k in weak:
        n0 = len(L.rows())
        t = time.time()
        v0 = v = verdicts[k + 1]
        others = keep_union.copy()
        for j, mj in enumerate(masks):
            if j != k and mj is not None:
                others |= mj
        # each round: fix_tries redraws from one call, all judged by Spark; the best one is kept only if it
        # beats the current version, and its issues steer the next round. A match ends the loop.
        region, crop_first, best_try, tries, accepted, rounds = None, None, None, [], False, 0
        for rnd in range(max(1, fix_rounds)):
            rounds = rnd + 1
            R.event("fix", f"Redrawing the {products[k].title}" + (f", round {rounds}" if rnd else ""),
                    "; ".join(v.get("issues", [])[:2]), model="muse-image-1.0", status="start")
            try:
                rds = render.redraw_product(L, comp, room, products[k], cards[k], boxes[k], masks[k],
                                            v.get("issues", []), v.get("fix", ""), tag=f"fix:p{k + 1}r{rounds}",
                                            forbid=others, n=fix_tries)
            except meta.ApiError as exc:
                rds = []
                R.event("fix", "Redraw failed", str(exc)[:160], status="done")
            if not rds:
                break
            if region is None:
                region, crop_first = rds[0]["region"], rds[0]["crop_before"]

            def judge(rd: dict, k: int = k, rounds: int = rounds) -> dict:
                crop_new = imgutil.crop_box(rd["comp"], rd["box"], margin=0.12)
                return verify.judge_one(L, products[k], cards[k], crop_new, tag=f"fix:p{k + 1}r{rounds}")

            with cf.ThreadPoolExecutor(max_workers=len(rds)) as pool:
                js = list(pool.map(judge, rds))
            tries += [{"round": rounds, "verdict": j1.get("verdict"), "score": j1.get("score")} for j1 in js]
            scored = [(float(j1["score"]), i) for i, j1 in enumerate(js) if "score" in j1]
            if not scored:
                continue
            s_best, i_best = max(scored)
            if best_try is None or s_best > best_try[0]:
                best_try = (s_best, rds[i_best], js[i_best])
            if s_best > float(v["score"]):
                rd = rds[i_best]
                comp, boxes[k], masks[k] = rd["comp"], rd["box"], rd["mask"]
                v = {**js[i_best], "index": k + 1, "fixed": True}
                accepted = True
            if v.get("verdict") == "match":
                break
        if best_try is None:
            continue
        if accepted:
            verdicts[k + 1] = v
        after = v if accepted else best_try[2]
        x1, y1, x2, y2 = region
        shown = comp if accepted else best_try[1]["comp"]
        rec = {"slot": slots[k]["id"], "product": products[k].id, "before": v0, "after": after, "accepted": accepted,
               "region": region, "rounds": rounds, "tries": tries}
        R.save_img(crop_first, f"fix_{slots[k]['id']}_before.jpg", 90)
        R.save_img(shown[y1:y2, x1:x2], f"fix_{slots[k]['id']}_after.jpg", 90)
        rec["images"] = [f"fix_{slots[k]['id']}_before.jpg", f"fix_{slots[k]['id']}_after.jpg"]
        fixes.append(rec)
        R.event("fix", f"{products[k].title}: {v0['verdict']} {v0['score']:g} -> {after.get('verdict', '?')} "
                f"{after.get('score', '?')}" + ("" if accepted else " (kept the first version)"),
                f"{len(tries)} redraws in {rounds} round{'s' if rounds > 1 else ''}, each judged by Spark",
                model="muse-image-1.0 + sam-3.1 + muse-spark-1.3", sec=round(time.time() - t, 1),
                usd=_cost_since(L, n0), status="done")
    # clutter the plan removes but the render kept (moving boxes under a clothes rack): SAM checks the
    # composite, Muse Image clears what is left, SAM checks again
    clutter = [o for o in sc["objects"] if o["class"] == "remove" and o.get("_mask") is not None]
    cleared_mask = np.zeros((h, w), dtype=bool)
    if clutter and max_fixes > 0:
        n0 = len(L.rows())
        t = time.time()
        zones = {o["id"]: render.clutter_zone(o, h, w) for o in clutter}
        left, hits = render.leftover(L, comp, clutter, zones, tag="clear:check")
        still = [o for o in clutter if left.get(o["id"], 0) >= 0.2]
        if still:
            names = ", ".join(o["name"] for o in still)
            R.event("fix", f"Clearing the {names}", "The render left clutter the plan removes. SAM 3.1 still finds "
                    + ", ".join(f"{100 * left[o['id']]:.0f}% of the {o['name']}" for o in still) + ".",
                    model="muse-image-1.0", status="start")
            protect = keep_union.copy()
            for o in still:
                protect &= ~o["_mask"]
            for mk in masks:
                if mk is not None:
                    protect |= mk
            try:
                cl = render.clear_objects(L, comp, still, hits, protect, tag="fix:clear")
            except meta.ApiError as exc:
                cl = None
                R.event("fix", "Clearing failed", str(exc)[:160], status="done")
            if cl is not None:
                before_score = max(left[o["id"]] for o in still)
                ok = cl["score"] < before_score
                R.save_img(cl["crop_before"], "fix_clear_before.jpg", 90)
                R.save_img(cl["crop_after"], "fix_clear_after.jpg", 90)
                fixes.append({"kind": "clear", "slot": "clear", "objects": [o["id"] for o in still],
                              "names": [o["name"] for o in still],
                              "before": {"left": {o["id"]: left[o["id"]] for o in still}},
                              "after": {"left": cl["left"]}, "accepted": ok, "region": cl["region"],
                              "images": ["fix_clear_before.jpg", "fix_clear_after.jpg"]})
                if ok:
                    comp = cl["comp"]
                    cleared_mask = cl["mask"]
                title = (f"Cleared the {names}" if cl["score"] < 0.2 else f"Partly cleared the {names}") if ok \
                    else f"Kept the first version: the {names} did not clear"
                R.event("fix", title, f"SAM 3.1 finds {100 * before_score:.0f}% of it before and "
                        f"{100 * cl['score']:.0f}% after.", model="muse-image-1.0 + sam-3.1",
                        sec=round(time.time() - t, 1), usd=_cost_since(L, n0), status="done")
    R.data["fixes"] = fixes
    # the scene verdict so far judged the first render: when a redraw or a clearing pass was kept, Spark
    # judges the finished room once more so the verdict describes the image people see
    scene, scene_first, final_products = best_j.get("scene", {}), None, None
    if any(f.get("accepted") for f in fixes):
        n0 = len(L.rows())
        t = time.time()
        crops = [imgutil.crop_box(comp, b, margin=0.12) for b in boxes]
        jf = verify.judge_scene(L, room, comp, products, cards, crops, removals, tag="final")
        if jf.get("scene"):
            scene_first, scene, final_products = scene, jf["scene"], jf.get("products")
            R.event("fix", "Judged the finished room again",
                    f"Overall {scene.get('overall', '?')}, photoreal {scene.get('photoreal', '?')}, cohesion "
                    f"{scene.get('cohesion', '?')}. {scene.get('comment', '')}", model="muse-spark-1.3",
                    sec=round(time.time() - t, 1), usd=_cost_since(L, n0), status="done")

    # ------------------------------------------------------------------ result
    after = comp
    R.save_img(after, "after.jpg", 93)
    # lossless pair so anyone can re-check the pixel claim (the web page diffs them in the browser)
    imgutil.save_png(room, R.dir / "before.png")
    imgutil.save_png(after, R.dir / "after.png")
    changed = np.any(after != room, axis=2)
    d = C.diag(h, w)
    edit = best["edit_mask"].copy()
    new_union = np.zeros((h, w), dtype=bool)
    for k in range(len(products)):
        if masks[k] is not None:
            edit |= C._dilate(masks[k], 0.01 * d)
            new_union |= masks[k]
    slot_k = {s["id"]: k for k, s in enumerate(slots)}
    for fx in fixes:  # an accepted redraw pasted its own region: count it as product pixels
        if fx.get("kind") == "clear":
            continue
        m = masks[slot_k[fx["slot"]]]
        if fx["accepted"] and m is not None:
            edit |= C._dilate(m, 0.01 * d)
    new_zone = C._dilate(new_union, 0.006 * d)
    edit |= cleared_mask
    cleared_zone = (C._dilate(best["old_union"], 0.025 * d) | cleared_mask) & ~new_zone
    prod_px = changed & (new_zone | (edit & ~cleared_zone))
    cleared_px = changed & cleared_zone & ~prod_px
    shade_px = changed & ~prod_px & ~cleared_px
    layers = np.zeros((h, w, 4), dtype=np.uint8)
    layers[prod_px] = (94, 230, 181, 255)
    layers[cleared_px] = (255, 120, 100, 255)
    layers[shade_px] = (120, 150, 255, 255)
    imgutil.save_png(layers, R.dir / "layers.png")
    edit_png = (C.feather(edit) * 255).astype(np.uint8)
    imgutil.save(np.dstack([edit_png] * 3), R.dir / "edit_mask.png")
    R.save_img(C.heatmap(best["de"]), "drift.jpg", 85)
    np.savez_compressed(R.dir / "masks.npz", **{f"product_{s['id']}": masks[k] for k, s in enumerate(slots)
                                                 if masks[k] is not None},
                        **{f"object_{o['id']}": o["_mask"] for o in sc["objects"] if o.get("_mask") is not None})
    prods_out = []
    for k, (s, p) in enumerate(zip(slots, products)):
        m = masks[k]
        crop = imgutil.crop_box(after, boxes[k], margin=0.12)
        R.save_img(crop, f"crop_{s['id']}.jpg", 90)
        prods_out.append({"slot": s["id"], "product": p.id, "box": list(boxes[k]),
                          "outline": render.mask_outline(m) if m is not None else "", "crop": f"crop_{s['id']}.jpg",
                          "verdict": verdicts.get(k + 1, {}), "fixed": bool(verdicts.get(k + 1, {}).get("fixed"))})
    absorbed = set(best.get("absorbed", []))
    removed_out = [{"id": o["id"], "name": o["name"], "box": o["box"], "outline": o["outline"],
                    **({"absorbed": True} if o["id"] in absorbed else {})}
                   for o in sc["objects"] if o["class"] in ("remove", "replace") or o["id"] in absorbed]
    drift = best["preservation"]
    pct = lambda m: round(100 * float(m.mean()), 2)  # noqa: E731
    R.data["result"] = {
        "after": "after.jpg", "edit_mask": "edit_mask.png", "drift": "drift.jpg", "layers": "layers.png",
        "lossless": {"before": "before.png", "after": "after.png"},
        "edit_outline": render.mask_outline(edit, 400),
        "products": prods_out, "removed": removed_out,
        "proof": {
            "untouched_pct": round(100 - pct(changed), 2),
            "edited_pct": pct(changed),
            "layers_pct": {"products": pct(prod_px), "cleared": pct(cleared_px), "shadows": pct(shade_px)},
            "model_drift_dE": drift["dE_mean"], "model_drift_changed_pct": round(100 * drift["changed_frac_dE10"], 2),
            "ssim_static": drift["ssim_static"], "alignment_shift_px": best["align"]["max_corner_shift_px"],
        },
        "scene": scene,
    }
    if scene_first is not None:  # kept for the record: the first render's verdict and Spark's final per-product view
        R.data["result"]["scene_first_render"] = scene_first
        R.data["result"]["final_check_products"] = final_products
    matches = sum(1 for k in range(len(products)) if verdicts.get(k + 1, {}).get("verdict") == "match")
    lp = R.data["result"]["proof"]["layers_pct"]
    R.event("result", f"{matches} of {len(products)} products verified as exact matches",
            f"{R.data['result']['proof']['untouched_pct']:.1f}% of the photo is pixel-identical to the original "
            f"(new products {lp['products']:.1f}%, cleared {lp['cleared']:.1f}%, shadows {lp['shadows']:.1f}%). "
            f"The image model drifted everywhere else (mean dE {drift['dE_mean']}); those pixels were restored.",
            status="done")
