"""Pick: Muse Spark 1.3 looks at every slot's short list at once and chooses a set that goes together.

Each slot's short list (products of the right kind that fit the room, ranked by look) becomes one
numbered contact sheet. Spark sees the room photo and all sheets in a single request, so the picks
are judged as a set (the rug against the chair against the walls), not one by one.
"""
from __future__ import annotations

import numpy as np

from . import imgutil, meta
from .catalog import Catalog, contact_sheet

SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string"},
                    "pick": {"type": "integer"},
                    "alternates": {"type": "array", "items": {"type": "integer"}},
                    "reason": {"type": "string"},
                },
                "required": ["slot", "pick", "alternates", "reason"],
            },
        },
        "set_comment": {"type": "string"},
    },
    "required": ["picks", "set_comment"],
}

PROMPT = """You are choosing real catalog products for a room makeover. Image 1 is the room. The plan: "{title}". {idea} Palette: {palette}.

For every slot below there is one numbered contact sheet of real products that already fit the room's measurements. For each slot pick the single best number and two alternates, so that the chosen products look good together and with this room's walls, floor and light. Prefer products whose photo shows the whole item clearly, with clear shapes and plain or bold surfaces; avoid fine detailed patterns and thin wiry structures, because the products will be redrawn into the photo and must stay recognisable. Give a short reason a shopper would understand.

Slots:
{slots}"""


def pick_products(ledger: meta.Ledger, cat: Catalog, room: np.ndarray, plan: dict, slots: list[dict],
                  shortlists: dict[str, list[str]], tag: str = "pick") -> dict:
    lines, parts = [], []
    sheets = {}
    for i, s in enumerate(slots):
        ids = shortlists.get(s["id"], [])
        if not ids:
            continue
        sheets[s["id"]] = contact_sheet(cat, ids, cols=6, tile=224)
        sz = s["size_cm"]
        lines.append(f"- slot {s['id']} ({s['kind'].replace('_', ' ')}, about {sz['w']:g} x {sz['d']:g} x {sz['h']:g} cm): "
                     f"{s['look']} Sheet for {s['id']} has {len(ids)} options.")
    prompt = PROMPT.format(title=plan.get("title", ""), idea=plan.get("idea", ""),
                           palette=", ".join(plan.get("palette", [])), slots="\n".join(lines))
    parts = [prompt, "Image 1, the room:", imgutil.jpeg_bytes(room, 90, 1280)]
    for sid, sheet in sheets.items():
        parts += [f"Contact sheet for slot {sid}:", imgutil.jpeg_bytes(sheet, 88, 1400)]
    data = meta.spark_json(ledger, f"{tag}:spark", parts, SCHEMA, "product_picks")
    out = {}
    for p in data.get("picks", []):
        sid = p.get("slot", "")
        ids = shortlists.get(sid)
        if not ids:
            continue

        def at(n: int) -> str | None:
            return ids[n - 1] if isinstance(n, int) and 1 <= n <= len(ids) else None

        chosen = at(p.get("pick")) or ids[0]
        alts = [x for x in (at(a) for a in p.get("alternates", [])) if x and x != chosen][:2]
        out[sid] = {"pick": chosen, "alternates": alts, "reason": p.get("reason", ""),
                    "pick_number": p.get("pick")}
    for s in slots:  # a slot Spark skipped falls back to the top of its short list
        if s["id"] not in out and shortlists.get(s["id"]):
            ids = shortlists[s["id"]]
            out[s["id"]] = {"pick": ids[0], "alternates": ids[1:3], "reason": "top of the short list",
                            "pick_number": 1}
    return {"picks": out, "set_comment": data.get("set_comment", ""), "sheets": sheets}
