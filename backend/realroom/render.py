"""Render the plan with the real product photos, then make it honest.

1. generate  Muse Image 1.0 /images/edits with the room, a numbered placement guide and one
             reference photo per product; n candidates in one call.
2. composite every candidate: align it to the original (ECC homography on regions that must not
             change), match its colour drift, find each new product with SAM 3.1, and paste back
             only the products, their shadows and the cleared clutter. Every other pixel is the
             original photo, so walls, floor and windows are provably untouched.
3. redraw    (verify.py decides) a product that came out only partly right is cut out, enlarged and
             redrawn on its own against its reference photo, then pasted back the same way.
"""
from __future__ import annotations

import concurrent.futures as cf
import time

import cv2
import numpy as np

from . import composite as C
from . import imgutil, meta

SYNONYMS = {
    "rug": ["area rug", "carpet"], "area rug": ["rug", "carpet"], "floor lamp": ["lamp", "standing lamp"],
    "table lamp": ["lamp"], "throw pillow": ["pillow", "cushion"], "pillow": ["cushion", "throw pillow"],
    "cushion": ["pillow"], "duvet": ["blanket", "comforter", "bedding"], "duvet cover": ["comforter", "bedding", "blanket"],
    "comforter": ["blanket", "bedding"], "coverlet": ["blanket", "bedding"], "accent chair": ["armchair", "chair"],
    "armchair": ["chair"], "side table": ["end table", "table"], "coffee table": ["table"], "sofa": ["couch"],
    "wall art": ["framed picture", "picture frame", "painting"], "framed print": ["picture frame", "painting"],
    "ottoman": ["pouf", "footstool"], "planter": ["plant pot", "potted plant"], "mirror": ["wall mirror"],
}

RULES = (
    "Hard rules: keep the exact same camera position, framing, lens distortion and perspective. "
    "Keep the walls, wallpaper, floor, ceiling, ceiling light, windows, blinds or curtains, doors and every object "
    "that is not listed exactly as they are. Keep the existing light direction, color temperature and exposure; "
    "new products receive the same light and cast soft, physically plausible contact shadows. "
    "The result must look like an unedited phone photo of the same room, not a render. "
    "Do not add anything else: no extra decor, no text, no numbers, no colored boxes or outlines."
)


# Every reference the model may use is an input we pass: the room and the catalog photos. Its own web
# and image search would bring in pictures we hold no rights to, and nothing here needs a shell.
NO_TOOLS = {"enable_image_search": False, "enable_web_search": False, "enable_shell": False}


def size_param(w: int, h: int, long_side: int = 1792) -> str:
    s = long_side / max(w, h)
    return f"{int(round(w * s / 16) * 16)}x{int(round(h * s / 16) * 16)}"


def guide_image(room: np.ndarray, slots: list[dict]) -> np.ndarray:
    items = [{"label": str(i + 1), "color": imgutil.GUIDE_COLORS[i % len(imgutil.GUIDE_COLORS)],
              "box_px": tuple(s["target_px"])} for i, s in enumerate(slots)]
    return imgutil.draw_guide(room, items)


def dims_text(size: dict) -> str:
    return f"{size['w']:g} x {size['d']:g} x {size['h']:g} cm (width x depth x height)"


def edit_prompt(scan: dict, slots: list[dict], products: list, n_extra_images: int) -> str:
    objs = {o["id"]: o for o in scan["objects"]}
    lines = []
    for i, (slot, p) in enumerate(zip(slots, products), start=1):
        rep = objs.get(slot.get("replaces") or "")
        verb = f"Replace the {rep['name']} with" if slot["action"] == "replace" and rep else "Add"
        lines.append(
            f"{i}. {verb} product {i}: {p.title} ({p.kind.replace('_', ' ')}), real size {dims_text(p.size_cm)}. "
            f"{slot.get('placement', '')} It belongs inside box {i} of the placement guide. Copy product {i} exactly "
            f"from its reference photo: {p.description} Same shape, proportions, color, material, texture, pattern "
            f"and details, scaled to its real size relative to the room.")
    removed = [o["name"] for o in scan["objects"] if o["class"] == "remove"]
    rem = ""
    if removed:
        rem = ("Remove: " + "; ".join(removed) + ". Show the floor and wall that were behind them, continuing the "
               "surrounding texture, so it looks like they were never there.")
    k = len(products)
    head = ("Image 1 is a real phone photo of a room to edit. Image 2 is a placement guide: the same photo with "
            "numbered translucent boxes showing where each product goes; use it only for position and scale and never "
            f"draw its boxes or numbers. Images 3 to {k + 2} are the reference photos of products 1 to {k}, in order.")
    return "\n".join([head] + lines + ([rem] if rem else []) + [RULES])


def generate(ledger: meta.Ledger, room: np.ndarray, guide: np.ndarray, cards: list[np.ndarray], prompt: str,
             n: int = 3, tag: str = "render") -> tuple[list[bytes], float]:
    h, w = room.shape[:2]
    images = [imgutil.png_bytes(room), imgutil.jpeg_bytes(guide, quality=90)] + \
             [imgutil.jpeg_bytes(c, quality=92) for c in cards]
    t0 = time.time()
    outs = meta.image_edit(ledger, tag, prompt, images, n=n, size=size_param(w, h), reasoning="high", tools=NO_TOOLS)
    return outs, round(time.time() - t0, 1)


# ---------------------------------------------------------------------------------------- compositing

def pick_instances(masks: dict, target: tuple, h: int, w: int, de: np.ndarray, count: int = 1) -> list[dict]:
    """SAM instances of a concept that belong to the slot and actually changed (a 'cushion' query also
    finds the sofa's own back cushions, which did not change)."""
    zone = C.boxes_mask(h, w, [target], grow=0.04)
    scored = []
    for m in masks.values():
        inter = float((m["mask"] & zone).sum())
        if inter <= 0:
            continue
        inside = inter / max(1.0, float(m["area"]))
        changed = float((de[m["mask"]] > 8).mean()) if m["area"] else 1.0
        scored.append((inside * np.sqrt(inter) * max(changed, 0.05), inside, changed, m))
    scored.sort(key=lambda t: -t[0])
    if not scored:
        return []
    fresh = [s for s in scored if s[2] >= 0.35] or scored
    if count <= 1:
        return [fresh[0][3]]
    good = [m for _, inside, _, m in fresh if inside > 0.5][:count]
    return good or [fresh[0][3]]


def segment_product(ledger, image_b: bytes, phrase: str, target: tuple, h: int, w: int, de: np.ndarray,
                    count: int = 1, tag: str = "seg") -> tuple[dict | None, str]:
    tried = []
    for alt in [phrase] + SYNONYMS.get(phrase.lower(), []):
        if alt in tried:
            continue
        tried.append(alt)
        got = meta.sam_masks(ledger, f"{tag}:{alt}", image_b, alt, w, h)
        picked = pick_instances(got, target, h, w, de, count)
        if picked:
            return _merge(picked), f"concept:{alt}"
        if len(tried) >= 3:
            break
    got = meta.sam_masks(ledger, f"{tag}:box", image_b, meta.box_prompt({"1": target}, w, h), w, h)
    if got.get("1"):
        m = got["1"]
        # a box prompt sometimes returns only a rug's border band: fill the hull where pixels changed
        hull = C.hull_mask(m["mask"]) & (de > 6)
        m = {**m, "mask": m["mask"] | hull}
        return _merge([m]), "box"
    return None, "none"


def _merge(ms: list[dict]) -> dict:
    mask = np.logical_or.reduce([m["mask"] for m in ms])
    ys, xs = np.where(mask)
    confs = [m.get("conf") for m in ms if m.get("conf") is not None]
    return {"mask": mask, "box": (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
            "area": int(mask.sum()), "conf": round(float(np.mean(confs)), 3) if confs else None, "n": len(ms)}


def composite_candidate(ledger: meta.Ledger, room: np.ndarray, out_bytes: bytes, scan: dict, slots: list[dict],
                        products: list, tag: str) -> dict:
    """Align, colour-match, segment and paste one generated candidate. Returns arrays and stats."""
    h, w = room.shape[:2]
    out = imgutil.load_rgb(out_bytes)
    raw_wh = [out.shape[1], out.shape[0]]
    out_r = imgutil.resize_to(out, w, h)
    objs = {o["id"]: o for o in scan["objects"]}
    gone = [o for o in scan["objects"] if o["class"] in ("remove", "replace")]
    targets = [tuple(s["target_px"]) for s in slots]
    changed_boxes = targets + [tuple(o["box"]) for o in gone]
    static = ~C.boxes_mask(h, w, changed_boxes, grow=0.03)
    align = C.estimate_alignment(room, out_r, static)
    aligned = align["ok"] and align["max_corner_shift_px"] > 1.0
    out_a = C.apply_alignment(out_r, align["H"], w, h) if aligned else out_r
    out_a, flow = C.refine_alignment(room, out_a, static)
    align = {**align, "flow": flow}
    pres, de = C.preservation(room, out_a, static)
    out_h = C.harmonize(room, out_a, static)
    out_b = imgutil.png_bytes(out_h)

    def seg(i):
        p = products[i]
        count = 2 if p.kind == "throw_pillow" and "2" in (p.name.lower().split("set of")[-1][:3]) else 1
        return segment_product(ledger, out_b, p.sam_phrase or p.kind.replace("_", " "), targets[i], h, w, de, count,
                               f"{tag}:p{i + 1}")

    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        found = list(pool.map(seg, range(len(products))))
    old_masks = []
    for o in gone:
        m = objs[o["id"]].get("_mask")
        old_masks.append(m if m is not None else C.boxes_mask(h, w, [tuple(o["box"])]))
    # SAM stops at thin and dark parts (stand legs, lamp poles): grow each mask over the pixels that
    # changed with it, never into kept objects, removed objects or another product
    fixed = np.zeros((h, w), dtype=bool)
    for o in scan["objects"]:
        if o["class"] not in ("remove", "replace") and o.get("_mask") is not None:
            fixed |= o["_mask"]
    for m in old_masks:
        fixed |= m
    for i, (f, how) in enumerate(found):
        if f is None:
            continue
        others = np.zeros((h, w), dtype=bool)
        for j, (g, _) in enumerate(found):
            if j != i and g is not None:
                others |= g["mask"]
        grown = C.complete_mask(room, out_h, f["mask"], targets[i], fixed | others)
        ys, xs = np.nonzero(grown)
        found[i] = ({**f, "mask": grown, "area": int(grown.sum()),
                     "box": (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)}, how)
    new_masks = [f[0]["mask"] for f in found if f[0] is not None]
    new_union = np.zeros((h, w), dtype=bool)
    for m in new_masks:
        new_union |= m
    # an object the plan keeps but a new product now mostly covers (the floor mattress under a new bed
    # frame) is gone in effect: its uncovered edges must come from the render, not peek out of the photo
    absorbed = []
    for o in scan["objects"]:
        m = o.get("_mask")
        if o["class"] in ("remove", "replace") or m is None or not m.any():
            continue
        if float((m & new_union).sum()) / float(m.sum()) >= 0.5:
            absorbed.append(o["id"])
            gone.append(o)
            old_masks.append(m)
    kept = np.zeros((h, w), dtype=bool)
    for o in scan["objects"]:
        if o["class"] not in ("remove", "replace") and o["id"] not in absorbed and o.get("_mask") is not None:
            kept |= o["_mask"]
    old_zone = np.zeros((h, w), dtype=bool)
    for o in gone:
        if objs[o["id"]].get("_mask") is not None:
            old_zone |= clutter_zone(objs[o["id"]], h, w)
    em, parts = C.edit_mask_v2(room, out_h, new_masks, old_masks, old_zone=old_zone, keep=kept)
    # contact shadows darken the original pixels around each product (smooth, no mask edge)
    shade = C.shade_layer(room, out_h, new_masks, skip=em)
    base = C.apply_shade(room, shade)
    alpha = C.feather(em)
    comp = C.paste_seamless(base, out_h, em, alpha)
    boxes = []
    for i, (f, how) in enumerate(found):
        if f is not None:
            boxes.append(tuple(f["box"]))
        else:
            x1, y1, x2, y2 = targets[i]
            boxes.append((x1, y1, x2 + 1, y2 + 1))
    old_union = np.zeros((h, w), dtype=bool)
    for m in old_masks:
        old_union |= m
    return {"raw": out, "raw_wh": raw_wh, "aligned": aligned, "align": {k: v for k, v in align.items() if k != "H"},
            "preservation": pres, "de": de, "harmonized": out_h, "comp": comp, "edit_mask": em, "alpha": alpha,
            "edit_parts": parts, "found": found, "boxes": boxes, "shade": shade, "old_union": old_union,
            "shade_px_frac": round(float((shade < 1.0).mean()), 4), "absorbed": absorbed,
            "seg_how": [how for _, how in found]}


# ---------------------------------------------------------------------------------------- redraw one product

def _square_region(box: tuple, h: int, w: int, grow: float = 0.45, min_side: int = 320) -> tuple:
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    side_w = max(min_side, bw * (1 + 2 * grow))
    side_h = max(min_side, bh * (1 + 2 * grow))
    # keep the crop at most 2:1 so the generator sees enough context
    if side_w > 2 * side_h:
        side_h = side_w / 2
    if side_h > 2 * side_w:
        side_w = side_h / 2
    rx1, ry1 = int(max(0, cx - side_w / 2)), int(max(0, cy - side_h / 2))
    rx2, ry2 = int(min(w, cx + side_w / 2)), int(min(h, cy + side_h / 2))
    return rx1, ry1, rx2, ry2


def redraw_product(ledger: meta.Ledger, comp: np.ndarray, room: np.ndarray, product, card: np.ndarray,
                   box: tuple, prev_mask: np.ndarray | None, issues: list[str], fix: str, tag: str,
                   forbid: np.ndarray | None = None, n: int = 1) -> list[dict]:
    """Cut the product's neighbourhood out of the composite and redraw it against the reference at a
    larger scale, n tries in one call. Each try is aligned back, found again with SAM 3.1 and pasted
    through its new mask; the caller judges them."""
    h, w = comp.shape[:2]
    rx1, ry1, rx2, ry2 = _square_region(box, h, w)
    crop = comp[ry1:ry2, rx1:rx2].copy()
    ch, cw = crop.shape[:2]
    issue_text = "; ".join(issues[:3]) if issues else "small differences from the reference"
    prompt = (
        f"Image 1 is a crop from a real room photo. Image 2 is the reference photo of the product: {product.title}, "
        f"{product.description} Redraw only the {product.kind.replace('_', ' ')} in image 1 so it is exactly this "
        f"product: same shape, proportions, color, material, texture, pattern and details. Fix these differences: "
        f"{issue_text}. {fix} Keep its position, size and perspective. Keep everything else in image 1 exactly as it "
        f"is, with the same light, shadows and photo grain. No text, no outlines.")
    inputs = [imgutil.png_bytes(crop), imgutil.jpeg_bytes(card, quality=92)]
    try:
        outs = meta.image_edit(ledger, f"{tag}:redraw", prompt, inputs, n=n, size=size_param(cw, ch, long_side=1536),
                               reasoning="high", tools=NO_TOOLS)
    except meta.ApiError:
        outs = meta.image_edit(ledger, f"{tag}:redraw", prompt, inputs, n=n, reasoning="high", tools=NO_TOOLS)
    lb = (box[0] - rx1, box[1] - ry1, box[2] - rx1, box[3] - ry1)
    static = ~C.boxes_mask(ch, cw, [lb], grow=0.06)
    forbid_c = forbid[ry1:ry2, rx1:rx2] if forbid is not None else None
    prev_c = prev_mask[ry1:ry2, rx1:rx2] if prev_mask is not None else None
    phrase = product.sam_phrase or product.kind.replace("_", " ")

    def place(i_raw: tuple[int, bytes]) -> dict | None:
        i, raw = i_raw
        new = imgutil.resize_to(imgutil.load_rgb(raw), cw, ch)
        align = C.estimate_alignment(crop, new, static)
        if align["ok"] and align["max_corner_shift_px"] > 1.0:
            new = C.apply_alignment(new, align["H"], cw, ch)
        new = C.harmonize(crop, new, static)
        pres, de = C.preservation(crop, new, static)
        seg, how = segment_product(ledger, imgutil.png_bytes(new), phrase, lb, ch, cw, de, 1, f"{tag}:reseg{i + 1}")
        if seg is None:
            return None
        segm = C.complete_mask(crop, new, seg["mask"], lb, forbid_c)
        ys, xs = np.nonzero(segm)
        m = segm | prev_c if prev_c is not None else segm.copy()
        m = C._dilate(m, 0.01 * C.diag(ch, cw))
        full_new = comp.copy()
        full_new[ry1:ry2, rx1:rx2] = new
        full_mask = np.zeros((h, w), dtype=bool)
        full_mask[ry1:ry2, rx1:rx2] = m
        out = C.paste_seamless(comp, full_new, full_mask, C.feather(full_mask))
        pmask = np.zeros((h, w), dtype=bool)
        pmask[ry1:ry2, rx1:rx2] = segm
        new_box = (int(xs.min()) + rx1, int(ys.min()) + ry1, int(xs.max()) + 1 + rx1, int(ys.max()) + 1 + ry1)
        return {"comp": out, "box": new_box, "mask": pmask, "region": (rx1, ry1, rx2, ry2), "seg_how": how,
                "crop_before": crop, "crop_after": out[ry1:ry2, rx1:rx2].copy(), "local_preservation": pres}

    if not outs:
        return []
    with cf.ThreadPoolExecutor(max_workers=len(outs)) as pool:
        return [r for r in pool.map(place, enumerate(outs)) if r is not None]


# ---------------------------------------------------------------------------------------- clear clutter

def clutter_zone(o: dict, h: int, w: int) -> np.ndarray:
    """Where an object the plan removes can still be: its old footprint, grown, plus Spark's box."""
    z = C._dilate(o["_mask"], 0.02 * C.diag(h, w))
    sb = o.get("spark_box") or o["box"]
    return z | C.boxes_mask(h, w, [tuple(int(v) for v in sb)], grow=0.05)


def leftover(ledger: meta.Ledger, img: np.ndarray, objs: list[dict], zones: dict[str, np.ndarray],
             tag: str) -> tuple[dict[str, float], dict[str, np.ndarray]]:
    """How much of each object the plan removes is still in `img`. A SAM 3.1 instance of the same
    concept that lies mostly in the object's zone counts; the score is its area over the old
    footprint (0 = gone, 1 = all there)."""
    h, w = img.shape[:2]
    b = imgutil.png_bytes(img)
    phrase = {o["id"]: (o.get("sam_phrase") or o["name"]).strip().lower()[:40] for o in objs}

    def run(c: str) -> tuple[str, dict]:
        try:
            return c, meta.sam_masks(ledger, f"{tag}:{c}", b, c, w, h)
        except meta.ApiError:
            return c, {}

    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        inst = dict(pool.map(run, sorted(set(phrase.values()))))
    fracs, hits = {}, {}
    for o in objs:
        z = zones[o["id"]]
        hit = np.zeros((h, w), dtype=bool)
        for m in inst.get(phrase[o["id"]], {}).values():
            if m["area"] and (m["mask"] & z).sum() >= 0.3 * m["area"]:
                hit |= m["mask"]
        hits[o["id"]] = hit
        fracs[o["id"]] = round(min(1.0, float(hit.sum()) / max(1, int(o["_mask"].sum()))), 3)
    return fracs, hits


def clear_objects(ledger: meta.Ledger, comp: np.ndarray, objs: list[dict], hits: dict[str, np.ndarray],
                  protect: np.ndarray, tag: str, n: int = 2) -> dict | None:
    """Clutter the render left behind: cut its neighbourhood out of the composite, have Muse Image
    remove it, align the result back and paste it over the clutter only (its old footprint and what
    SAM still finds of it), never over a new product or an object that stays. Of n tries, keep the
    one where SAM finds the least left."""
    h, w = comp.shape[:2]
    union = np.zeros((h, w), dtype=bool)
    for o in objs:
        union |= o["_mask"] | hits.get(o["id"], False)
    ys, xs = np.nonzero(union)
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    rx1, ry1, rx2, ry2 = _square_region(box, h, w, grow=0.35, min_side=384)
    crop = comp[ry1:ry2, rx1:rx2].copy()
    ch, cw = crop.shape[:2]
    dc = C.diag(ch, cw)
    names = "; ".join(o["name"] for o in objs)
    prompt = (
        f"Image 1 is a crop from a real room photo. Remove these completely: {names}. Where they were, show the "
        f"empty floor, skirting board and wall that were hidden behind them, continuing the same floorboards, wall "
        f"color, light and photo grain. Keep every other object exactly where and as it is. Do not add anything "
        f"new. No text, no outlines.")
    try:
        outs = meta.image_edit(ledger, f"{tag}:clear", prompt, [imgutil.png_bytes(crop)], n=n,
                               size=size_param(cw, ch, long_side=1536), reasoning="high", tools=NO_TOOLS)
    except meta.ApiError:
        outs = meta.image_edit(ledger, f"{tag}:clear", prompt, [imgutil.png_bytes(crop)], n=n, reasoning="high",
                               tools=NO_TOOLS)
    if not outs:
        return None
    foot = union[ry1:ry2, rx1:rx2]
    paste_m = C._dilate(foot, 0.015 * dc) & ~C._dilate(protect[ry1:ry2, rx1:rx2], 0.003 * dc)
    static = ~C._dilate(foot, 0.04 * dc)
    local = [{**o, "_mask": o["_mask"][ry1:ry2, rx1:rx2]} for o in objs]
    zones = {o["id"]: C._dilate(foot, 0.02 * dc) for o in objs}
    alpha = C.feather(paste_m)
    best = None
    for i, raw in enumerate(outs):
        new = imgutil.resize_to(imgutil.load_rgb(raw), cw, ch)
        align = C.estimate_alignment(crop, new, static)
        if align["ok"] and align["max_corner_shift_px"] > 1.0:
            new = C.apply_alignment(new, align["H"], cw, ch)
        new = C.harmonize(crop, new, static)
        out = C.paste_seamless(crop, new, paste_m, alpha)
        left, _ = leftover(ledger, out, local, zones, f"{tag}:check{i + 1}")
        score = max(left.values()) if left else 0.0
        if best is None or score < best["score"]:
            best = {"score": score, "left": left, "crop_after": out}
    full = comp.copy()
    full[ry1:ry2, rx1:rx2] = best["crop_after"]
    pmask = np.zeros((h, w), dtype=bool)
    pmask[ry1:ry2, rx1:rx2] = paste_m
    return {"comp": full, "region": (rx1, ry1, rx2, ry2), "crop_before": crop, "crop_after": best["crop_after"],
            "left": best["left"], "score": best["score"], "mask": pmask}


def mask_outline(mask: np.ndarray, max_points: int = 220) -> str:
    from .scan import _outline

    return _outline(mask, max_points)
