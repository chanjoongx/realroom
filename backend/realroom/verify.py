"""Verify: Muse Spark 1.3 inspects every rendered product against its real catalog photo.

A render only ships when a shopper would recognise each new piece as the product they would buy.
The judge sees the original photo, the composite, and for each product its catalog photo next to a
crop of the composite where it should appear. Products that come out 'partial' get one focused
redraw (render.redraw_product) and a second look.
"""
from __future__ import annotations

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
        return meta.spark_json(ledger, f"{tag}:judge", parts, SCHEMA, "render_judgement")
    except meta.ApiError as exc:
        return {"error": str(exc)[:300]}


def judge_one(ledger: meta.Ledger, product, card, crop, tag: str) -> dict:
    parts = [ONE_PROMPT, f"Reference photo ({product.title}, {product.kind.replace('_', ' ')}, {_dims(product)}):",
             imgutil.jpeg_bytes(card, 90, 768), "Crop of the edited room photo:", imgutil.jpeg_bytes(crop, 90, 768)]
    try:
        out = meta.spark_json(ledger, f"{tag}:judge1", parts, ONE_SCHEMA, "product_judgement")
        return out["product"]
    except (meta.ApiError, KeyError) as exc:
        return {"error": str(exc)[:300]}


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
