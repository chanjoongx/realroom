"""Verify: Muse Spark 1.3 inspects every rendered product against its real catalog photo.

A render only ships when a shopper would recognise each new piece as the product they would buy.
judge_scene sees the original photo, a candidate composite, and for each product its catalog photo next to
a crop of the composite where it should appear; it ranks the candidates and rates the scene. On the chosen
candidate, record_checks then judges every product RECORD_CHECKS times, each look a separate judge_one
request, and the majority of those looks steers the fix step. A product they call missing is first looked
for in the whole photo (locate), since the render sometimes draws a product away from its planned spot.
Products the checks call partial, mismatch or missing (missing first, then the lowest score, up to max_fixes
of them) are redrawn (render.redraw_product) in up to fix_rounds rounds of fix_tries tries; judge_one scores
every try, and a try replaces the current version only when it scores higher. A product that was redrawn, or whose own pixels a
later redraw or clearing pass reached, is checked RECORD_CHECKS times again on the finished photo. These
majorities are the verdicts of record that the result counts and shows. When a redraw or a clearing pass
was kept, judge_scene also rates the finished room once more.

Answers are read through normalize_judgement and normalize_product, so a score that came back as a
string or outside 0 to 10, an index that names no product, or a verdict outside the four words
cannot reach the ranking or the page.
"""
from __future__ import annotations

import concurrent.futures as cf
from collections import Counter

from . import imgutil, meta

JUDGE_PROMPT = """You are a strict quality inspector for a shopping app that edits real room photos. Every product placed in the edited photo must be the exact real product the shopper will buy.

You get the original photo, the edited photo, and for each product its reference photo plus a crop of the edited photo where it should appear.

For each product decide: match (a shopper would recognize it as the same product at a glance: shape, proportions, color, material and pattern all agree), partial (same kind of item but at least one clear difference), mismatch (a different product), or missing. Score 0 to 10, where 9 or 10 is only for match. Judge scale against the room (is it plausibly its real size?) and placement (is it physically sensible: on the floor, not floating, not blocking a door?). List concrete issues, and write one short instruction that would fix the biggest issue.

For the scene: photorealism 0 to 10 (does it look like an unedited phone photo?), cohesion 0 to 10 (do the new items look like they belong together and with the room?), whether walls, floor, windows and fixtures are unchanged (renter rule), whether requested removals are clean, visible artifacts, and an overall 0 to 10. Be critical; do not reward effort. Answer in English."""

PRODUCT = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["match", "partial", "mismatch", "missing"]},
        "score": {"type": "number"},
        "shape": {"type": "string"},
        "color": {"type": "string"},
        "material_pattern": {"type": "string"},
        "scale_ok": {"type": "boolean"},
        "placement_ok": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "fix": {"type": "string"},
    },
    "required": ["index", "verdict", "score", "shape", "color", "material_pattern", "scale_ok", "placement_ok",
                 "issues", "fix"],
}

SCHEMA = {
    "type": "object",
    "properties": {
        "products": {"type": "array", "items": PRODUCT},
        "scene": {
            "type": "object",
            "properties": {
                "photoreal": {"type": "number"}, "cohesion": {"type": "number"},
                "renter_rule_ok": {"type": "boolean"}, "removals_ok": {"type": "boolean"},
                "artifacts": {"type": "array", "items": {"type": "string"}},
                "overall": {"type": "number"}, "comment": {"type": "string"},
            },
            "required": ["photoreal", "cohesion", "renter_rule_ok", "removals_ok", "artifacts", "overall", "comment"],
        },
    },
    "required": ["products", "scene"],
}

ONE_PROMPT = """You are a strict quality inspector for a shopping app that edits real room photos. Compare the product in the crop of an edited room photo with the product's reference photo. Decide: match (a shopper would recognize it as the same product at a glance: shape, proportions, color, material and pattern all agree), partial (same kind of item, at least one clear difference), mismatch, or missing. Score 0 to 10, 9 or 10 only for match. List concrete issues and one short instruction that would fix the biggest one. Be critical. Answer in English."""

ONE_SCHEMA = {"type": "object", "properties": {"product": PRODUCT}, "required": ["product"]}

VERDICT_WEIGHT = {"match": 1.0, "partial": 0.55, "mismatch": 0.15, "missing": 0.0}

# The verdict of record. Spark's verdict on the same pixels moves: on 11-03 four products recorded as match 9,
# judged eight more times each, drew partial in 13 of the 32 looks. judge_scene, which sees every product of a
# candidate in one request, called match three products in 14 test runs that each of three separate looks
# called partial. And the fix loop keeps the best of several judged redraws, so the score it keeps is the
# luckiest look. So every product is judged RECORD_CHECKS times on the pixels it ships with, each look on its
# own, and a majority decides.
RECORD_CHECKS = 3


def _score(v):
    """A 0 to 10 score from a number or numeric string; None when there is none. In range it is unchanged."""
    n = meta.as_number(v)
    return None if n is None else min(10, max(0, n))


def _texts(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if not isinstance(v, list):
        return []
    return [meta.as_text(x) for x in v if isinstance(x, (str, int, float))]


def _clean_fields(d: dict, numbers: tuple, flags: tuple, texts: tuple, lists: tuple) -> dict:
    """A copy of d where the named fields have the right type; fields that cannot be read are dropped."""
    out = dict(d)
    for k in numbers:
        if k in out:
            s = _score(out[k])
            if s is None:
                del out[k]
            elif s != out[k] or isinstance(out[k], str):
                out[k] = s
    for k in flags:
        if k in out and not isinstance(out[k], bool):
            b = meta.as_bool(out[k])
            if b is None:
                del out[k]
            else:
                out[k] = b
    for k in texts:
        if k in out and not isinstance(out[k], str):
            out[k] = meta.as_text(out[k])
    for k in lists:
        if k in out:
            v = _texts(out[k])
            if v != out[k]:
                out[k] = v
    return out


def normalize_product(p) -> dict | None:
    """One product verdict with one of the four verdict words and a 0 to 10 score, or None when it lacks
    either (it is then treated as not judged). A well-formed verdict comes back equal."""
    if not isinstance(p, dict):
        return None
    verdict = p.get("verdict")
    if isinstance(verdict, str) and verdict not in VERDICT_WEIGHT:
        verdict = verdict.strip().lower()
    if verdict not in VERDICT_WEIGHT or _score(p.get("score")) is None:
        return None
    q = _clean_fields(p, ("score",), ("scale_ok", "placement_ok", "fixed"),
                      ("shape", "color", "material_pattern", "fix"), ("issues",))
    if q.get("verdict") != verdict:
        q["verdict"] = verdict
    return q


def normalize_judgement(j: dict, n_products: int) -> dict:
    """A judge_scene answer with only usable product verdicts: index 1 to n_products, each index once
    (the first wins), verdict and score as in normalize_product. Scene scores are clamped to 0 to 10."""
    out = dict(j)
    products, seen = [], set()
    for p in j.get("products") if isinstance(j.get("products"), list) else []:
        q = normalize_product(p)
        if q is None:
            continue
        idx = meta.as_int(q.get("index"))
        if idx is None or not 1 <= idx <= n_products or idx in seen:
            continue
        if q.get("index") is not idx:
            q["index"] = idx
        seen.add(idx)
        products.append(q)
    out["products"] = products
    if isinstance(j.get("scene"), dict):
        out["scene"] = _clean_fields(j["scene"], ("photoreal", "cohesion", "overall"),
                                     ("renter_rule_ok", "removals_ok"), ("comment",), ("artifacts",))
    elif "scene" in j:
        out["scene"] = {}
    return out


def _dims(p) -> str:
    s = p.size_cm
    return f"{s['w']:g} x {s['d']:g} x {s['h']:g} cm"


def judge_scene(ledger: meta.Ledger, room, comp, products: list, cards: list, crops: list, removals: list[str],
                tag: str) -> dict:
    parts = [JUDGE_PROMPT, "Original photo:", imgutil.jpeg_bytes(room, 90, 1280),
             "Edited photo:", imgutil.jpeg_bytes(comp, 90, 1280)]
    for i, p in enumerate(products):
        parts += [f"Product {i + 1} reference photo ({p.title}, {p.kind.replace('_', ' ')}, {_dims(p)}):",
                  imgutil.jpeg_bytes(cards[i], 90, 768),
                  f"Product {i + 1} as it appears in the edited photo (crop):",
                  imgutil.jpeg_bytes(crops[i], 90, 768)]
    if removals:
        parts.append("Requested removals: " + ", ".join(removals) + ".")
    try:
        out = meta.spark_json(ledger, f"{tag}:judge", parts, SCHEMA, "render_judgement")
    except meta.ApiError as exc:
        return {"error": str(exc)[:300]}
    return normalize_judgement(out, len(products))


def judge_one(ledger: meta.Ledger, product, card, crop, tag: str) -> dict:
    parts = [ONE_PROMPT, f"Reference photo ({product.title}, {product.kind.replace('_', ' ')}, {_dims(product)}):",
             imgutil.jpeg_bytes(card, 90, 768), "Crop of the edited room photo:", imgutil.jpeg_bytes(crop, 90, 768)]
    try:
        out = meta.spark_json(ledger, f"{tag}:judge1", parts, ONE_SCHEMA, "product_judgement")
    except meta.ApiError as exc:
        return {"error": str(exc)[:300]}
    p = normalize_product(out.get("product"))
    return p if p is not None else {"error": "the product check gave no usable verdict"}


LOCATE_PROMPT = """Image 1 is an edited photo of a room. Image 2 is the catalog photo of one product. A check of the spot where the product was planned found no such item there, but the image model may have drawn it somewhere else. Look at the whole of image 1: is an item of the same kind as the product (its color or details may differ) anywhere in it? If so, give the tightest box around that item on a 0 to 1000 grid, [x_min, y_min, x_max, y_max]. If there is none, answer found false."""

LOCATE_SCHEMA = {"type": "object", "properties": {
    "found": {"type": "boolean"},
    "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}},
    "required": ["found", "box"]}


def locate(ledger: meta.Ledger, img, product, card, tag: str) -> tuple[int, int, int, int] | None:
    """Where in img Spark sees an item of the product's kind, as a pixel box (x2, y2 inclusive), or None.

    Asked only when the checks call a product missing at its planned spot: the render sometimes draws it
    elsewhere, and adding it at the planned spot would then show it twice (11-04: a planter drawn where a
    moving box had been, beside the sofa, that SAM 3.1 did not know as a planter)."""
    h, w = img.shape[:2]
    parts = [LOCATE_PROMPT, "Image 1:", imgutil.jpeg_bytes(img, 92, 1600),
             f"Image 2 ({product.title}, {product.kind.replace('_', ' ')}, {_dims(product)}):",
             imgutil.jpeg_bytes(card, 90, 768)]
    try:
        out = meta.spark_json(ledger, f"{tag}:locate", parts, LOCATE_SCHEMA, "product_location")
    except meta.ApiError:
        return None
    b = out.get("box")
    if out.get("found") is not True or not isinstance(b, (list, tuple)) or len(b) != 4:
        return None
    nums = [meta.as_number(v) for v in b]
    if any(v is None or not 0 <= v <= 1000 for v in nums):
        return None
    x1, y1, x2, y2 = imgutil.norm_to_px(nums, w, h)
    return (x1, y1, x2, y2) if x2 - x1 >= 4 and y2 - y1 >= 4 else None


def majority(looks: list, n: int = RECORD_CHECKS) -> dict:
    """The verdict of record from up to n independent judge_one looks at the same final pixels.

    match only when more than half of n looks say match; otherwise the most common other verdict (a tie
    goes to the worse one). The score is the median of the usable looks, and the notes (issues, fix) come
    from the middle look among those with the winning verdict. When too few looks came back to make a
    majority, the product is "unverified". `checks` keeps every usable look and `of` is n."""
    usable = [x for x in looks if isinstance(x, dict) and x.get("verdict") in VERDICT_WEIGHT
              and isinstance(x.get("score"), (int, float))]
    checks = [{"verdict": x["verdict"], "score": x["score"]} for x in usable]
    need = n // 2 + 1
    if len(usable) < need:
        return {"verdict": "unverified", "issues": [], "fix": "", "checks": checks, "of": n}
    votes = Counter(x["verdict"] for x in usable)
    if votes["match"] >= need:
        verdict = "match"
    else:
        others = [v for v in votes if v != "match"] or ["partial"]
        verdict = max(others, key=lambda v: (votes[v], -VERDICT_WEIGHT[v]))
    scores = sorted(float(x["score"]) for x in usable)
    mid = len(scores) // 2
    score = scores[mid] if len(scores) % 2 else (scores[mid - 1] + scores[mid]) / 2
    same = sorted((x for x in usable if x["verdict"] == verdict), key=lambda x: float(x["score"]))
    rep = same[len(same) // 2] if same else {}
    out = {k: rep[k] for k in ("shape", "color", "material_pattern", "scale_ok", "placement_ok", "issues", "fix")
           if k in rep}
    out.setdefault("issues", [])
    out.setdefault("fix", "")
    return {**out, "verdict": verdict, "score": round(score, 1), "checks": checks, "of": n}


def record_checks(ledger: meta.Ledger, img, boxes: list, products: list, cards: list, n: int = RECORD_CHECKS,
                  tag: str = "record", index: list[int] | None = None) -> list[dict]:
    """Judge every product n times on `img` (a close crop next to its catalog photo, each look a separate
    request) and return one verdict per product (see majority) with its 1-based index: by default the
    position in `products`, or the run's own product numbers in `index` when only some products are checked."""
    index = list(index) if index is not None else list(range(1, len(products) + 1))
    crops = [imgutil.crop_box(img, b, margin=0.12) for b in boxes]
    jobs = [(k, i) for k in range(len(products)) for i in range(n)]
    if not jobs:
        return []

    def look(job):
        k, i = job
        return judge_one(ledger, products[k], cards[k], crops[k], tag=f"{tag}:p{index[k]}c{i + 1}")

    # every look in one wave (4 products x 3 looks = 12 requests; Spark allows 3,000 a minute per team), so the
    # checks add about one judge call of waiting, not two
    with cf.ThreadPoolExecutor(max_workers=min(16, len(jobs))) as pool:
        looks = list(pool.map(look, jobs))
    return [{**majority([j for (kk, _), j in zip(jobs, looks) if kk == k], n), "index": index[k]}
            for k in range(len(products))]


def candidate_score(j: dict, n_products: int) -> float:
    """One number to rank candidates: products dominate, the scene breaks ties."""
    if "products" not in j:
        return -1.0
    by_idx = {p["index"]: p for p in j["products"]}
    prod = 0.0
    for i in range(1, n_products + 1):
        p = by_idx.get(i)
        if not p:
            continue
        prod += VERDICT_WEIGHT.get(p["verdict"], 0) * float(p["score"]) / 10
    sc = j.get("scene", {})
    scene = (0.5 * float(sc.get("overall", 0)) + 0.3 * float(sc.get("photoreal", 0))
             + 0.2 * float(sc.get("cohesion", 0))) / 10
    penalty = 0.0 if sc.get("renter_rule_ok", True) else 0.3
    return round(prod / max(1, n_products) * 0.75 + scene * 0.25 - penalty, 4)
