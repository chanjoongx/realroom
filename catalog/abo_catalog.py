"""Build the Realroom product catalog candidates from Amazon Berkeley Objects (ABO) listings.

ABO is CC BY 4.0 (data credit "Amazon.com"). This step only reads the listing metadata that is
already on disk and writes one JSON line per usable product:

  - amazon.com listings in room categories, sorted into design "kinds" (rug, floor_lamp, ...)
  - dimensions converted to centimetres and checked against plausible ranges per kind
  - rug sizes cross-checked against the size written in the product name
  - names cleaned (the "Amazon Brand" prefix and every en/em dash removed)
  - every catalog image of the product (main first) with its archive path and pixel size

usage: python catalog/abo_catalog.py [--listings ...] [--images ...] [--out ...]
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import html
import json
import math
import os
import pathlib
import re
import sys

DATA = pathlib.Path(os.environ.get("REALROOM_DATA", pathlib.Path(__file__).resolve().parents[1] / "data"))
ABO = DATA / "abo"
IN2CM = 2.54
# dash-like characters, listed by code point (house style: no dashes in outputs)
DASH_CHARS = "".join(map(chr, (0x2012, 0x2013, 0x2014, 0x2015, 0x2212)))
DASHES = re.compile(f"[{DASH_CHARS}]")

# kind: (ABO product types, name/node patterns that select it, patterns that exclude it)
# Order matters: the first kind whose rule matches wins.
KINDS: list[tuple[str, set[str], str, str]] = [
    ("runner_rug", {"RUG", "HOME_FURNITURE_AND_DECOR", "HOME"}, r"\brunner\b", r"table runner|stair|tread|mat\b"),
    ("rug", {"RUG", "HOME_FURNITURE_AND_DECOR", "HOME"}, r"\brug\b|\bcarpet\b", r"rug pad|gripper|outdoor|doormat|bath"),
    ("floor_lamp", {"LAMP", "LIGHT_FIXTURE", "HOME"}, r"floor lamp|torchiere|arc lamp|tripod lamp|standing lamp", r"bulb only|replacement"),
    ("table_lamp", {"LAMP", "HOME"}, r"table lamp|desk lamp|bedside lamp|nightstand lamp|buffet lamp|accent lamp|\blamp\b", r"floor|bulb only|replacement|clip|clamp"),
    ("throw_pillow", {"PILLOW", "HOME_FURNITURE_AND_DECOR", "HOME"}, r"pillow|cushion", r"patio|outdoor|chair pad|seat cushion|bed pillow|sleeping|insert only|lumbar support|bench cushion|pillowcase|pillow case|allergy|down alternative|memory foam|protector|body pillow|standard pillow|queen pillow|king pillow|pillows? - (standard|queen|king)|wedge"),
    ("bedding", {"HOME_BED_AND_BATH", "HOME", "FLAT_SHEET"}, r"duvet|comforter|coverlet|quilt set|\bquilt\b|bedspread", r"sheet set|pillowcase|baby|crib|swaddle|insert only|mattress|wall art|print"),
    ("throw_blanket", {"BLANKET", "HOME_FURNITURE_AND_DECOR", "HOME", "HOME_BED_AND_BATH"}, r"throw blanket|\bthrow\b", r"pillow|ladder|baby|swaddle"),
    ("sectional", {"SOFA", "HOME_FURNITURE_AND_DECOR"}, r"sectional", r"cover only|slipcover only"),
    ("sofa", {"SOFA", "HOME_FURNITURE_AND_DECOR"}, r"sofa|couch|loveseat|settee", r"sofa table|cover only|slipcover only|sleeper chair"),
    ("accent_chair", {"CHAIR", "HOME_FURNITURE_AND_DECOR"}, r"accent chair|arm ?chair|club chair|lounge chair|swivel chair|barrel chair|slipper chair|wingback|recliner|rocking chair|chaise|living room chair|reading chair|papasan", r"dining|kitchen|office|desk|gaming|bar stool|counter|outdoor|patio|folding"),
    ("dining_chair", {"CHAIR"}, r"dining|kitchen", r"outdoor|patio|bar stool|counter"),
    ("office_chair", {"CHAIR"}, r"office|desk chair|task chair|executive", r"gaming"),
    ("coffee_table", {"TABLE", "HOME_FURNITURE_AND_DECOR"}, r"coffee table|cocktail table", r"outdoor|patio"),
    ("side_table", {"TABLE", "HOME_FURNITURE_AND_DECOR", "CABINET"}, r"side table|end table|accent table|nightstand|night stand|bedside table|c table|drink table", r"outdoor|patio"),
    ("console_table", {"TABLE"}, r"console|sofa table|entryway table", r"outdoor|patio"),
    ("dining_table", {"TABLE"}, r"dining table|kitchen table|dining room table", r"outdoor|patio"),
    ("desk", {"DESK", "TABLE"}, r"\bdesk\b|writing table", r"lamp|organizer"),
    ("ottoman", {"OTTOMAN", "HOME_FURNITURE_AND_DECOR"}, r"ottoman|pouf|footstool|foot stool", r"cover only"),
    ("bench", {"BENCH", "OTTOMAN"}, r"bench", r"garden|outdoor|patio|piano"),
    ("bed", {"BED"}, r"\bbed\b|headboard|bed frame|daybed", r"bed pillow|bed rail|bed skirt|dog|cat|pet"),
    ("bookcase", {"SHELF", "HOME_FURNITURE_AND_DECOR", "CABINET"}, r"bookcase|book shelf|bookshelf|shelving unit|etagere|ladder shelf|storage shelf|shelves", r"wall mount|floating|bracket"),
    ("wall_shelf", {"SHELF", "HOME"}, r"floating shelf|wall shelf|wall mount", r""),
    ("wall_art", {"WALL_ART", "HOME", "HOME_FURNITURE_AND_DECOR"}, r"wall art|canvas|print|painting|poster|wall decor|framed", r"photo frame|picture frame|instax|mirror|clock|hook|rack"),
    ("mirror", {"HOME", "HOME_FURNITURE_AND_DECOR", "MIRROR"}, r"mirror", r"compact|makeup|vanity light|car"),
    ("planter", {"PLANTER", "HOME", "HOME_FURNITURE_AND_DECOR"}, r"planter|plant pot|flower pot|plant stand", r"hanging|wall mount|saucer only"),
    ("vase", {"VASE", "HOME", "HOME_FURNITURE_AND_DECOR"}, r"vase", r""),
]

# plausible real size per kind, centimetres: (min, max) for width (largest footprint side),
# depth (smaller footprint side) and height
RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "rug": {"w": (90, 460), "d": (60, 370), "h": (0.1, 6)},
    "runner_rug": {"w": (120, 460), "d": (45, 100), "h": (0.1, 5)},
    "floor_lamp": {"w": (15, 120), "d": (15, 120), "h": (110, 215)},
    "table_lamp": {"w": (8, 60), "d": (8, 60), "h": (20, 95)},
    "throw_pillow": {"w": (25, 80), "d": (5, 70), "h": (5, 80)},
    "throw_blanket": {"w": (90, 260), "d": (60, 260), "h": (0.1, 10)},
    "bedding": {"w": (150, 290), "d": (150, 260), "h": (0.1, 15)},
    "sofa": {"w": (120, 280), "d": (65, 130), "h": (60, 110)},
    "sectional": {"w": (180, 420), "d": (80, 300), "h": (60, 110)},
    "accent_chair": {"w": (45, 120), "d": (45, 170), "h": (55, 125)},
    "dining_chair": {"w": (35, 75), "d": (35, 75), "h": (70, 115)},
    "office_chair": {"w": (45, 85), "d": (45, 85), "h": (75, 140)},
    "coffee_table": {"w": (55, 160), "d": (40, 110), "h": (25, 55)},
    "side_table": {"w": (25, 80), "d": (25, 70), "h": (35, 80)},
    "console_table": {"w": (70, 200), "d": (20, 55), "h": (60, 95)},
    "dining_table": {"w": (70, 240), "d": (60, 120), "h": (65, 82)},
    "desk": {"w": (70, 200), "d": (40, 90), "h": (65, 95)},
    "ottoman": {"w": (30, 160), "d": (30, 120), "h": (25, 55)},
    "bench": {"w": (60, 200), "d": (25, 70), "h": (35, 60)},
    "bed": {"w": (95, 240), "d": (150, 250), "h": (25, 160)},
    "bookcase": {"w": (30, 200), "d": (20, 60), "h": (60, 230)},
    "wall_shelf": {"w": (20, 150), "d": (8, 40), "h": (2, 60)},
    "wall_art": {"w": (20, 200), "d": (0.5, 10), "h": (20, 200)},
    "mirror": {"w": (20, 200), "d": (0.5, 10), "h": (20, 210)},
    "planter": {"w": (8, 70), "d": (8, 70), "h": (8, 120)},
    "vase": {"w": (5, 45), "d": (5, 45), "h": (8, 90)},
}

RUG_SIZE = re.compile(
    r"(\d{1,2})\s*'\s*(?:(\d{1,2})\s*\"\s*)?\s*x\s*(\d{1,2})\s*'\s*(?:(\d{1,2})\s*\")?", re.I)
ROUND = re.compile(r"\b(round|circle|circular)\b", re.I)


def en(d: dict, key: str) -> str | None:
    for x in d.get(key, []) or []:
        if str(x.get("language_tag", "")).startswith("en_"):
            return str(x.get("value"))
    xs = d.get(key) or []
    return str(xs[0].get("value")) if xs and isinstance(xs[0], dict) else None


def lang(d: dict, key: str, tag: str) -> str | None:
    for x in d.get(key, []) or []:
        if x.get("language_tag") == tag:
            return str(x.get("value"))
    return None


def clean_text(s: str | None) -> str:
    """Drop the 'Amazon Brand' prefix and every en/em dash (house style: no dashes in outputs)."""
    if not s:
        return ""
    s = html.unescape(str(s))
    s = re.sub(r"^\s*Amazon\s*Brand\s*[" + DASH_CHARS + r"\-:]+\s*", "", s, flags=re.I)
    s = re.sub(r"(?<=\d)\s*[" + DASH_CHARS + r"]\s*(?=\d)", "-", s)  # a range like 2 to 3 written with a dash -> 2-3
    s = DASHES.sub(",", s)  # any other dash between words becomes a comma
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r",\s*,", ",", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ,")
    return s


def inches(v: dict | None) -> float | None:
    if not v:
        return None
    nv = v.get("normalized_value") or {}
    unit, val = (nv.get("unit") or v.get("unit")), (nv.get("value") if nv.get("value") is not None else v.get("value"))
    if val is None:
        return None
    val = float(val)
    unit = (unit or "").lower()
    if unit.startswith("inch"):
        return val
    if unit.startswith("cent"):
        return val / IN2CM
    if unit.startswith("milli"):
        return val / 25.4
    if unit.startswith("feet") or unit.startswith("foot"):
        return val * 12
    if unit.startswith("meter") or unit == "m":
        return val * 39.3701
    return val


def rug_name_size(name: str) -> tuple[float, float] | None:
    """Size written in a rug name, e.g. 5' x 8' or 7' 6" x 2' 3", as (long, short) in cm."""
    m = RUG_SIZE.search(name)
    if not m:
        return None
    a = int(m.group(1)) * 12 + int(m.group(2) or 0)
    b = int(m.group(3)) * 12 + int(m.group(4) or 0)
    if a <= 0 or b <= 0:
        return None
    return max(a, b) * IN2CM, min(a, b) * IN2CM


ALL_TYPES = set().union(*(t for _, t, _, _ in KINDS))
THROW_SIZE = re.compile(r"(\d{2})\s*\"\s*x\s*(\d{2})\s*\"")
NAME_W = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*(?:\"|''|in\.?|inch(?:es)?)\s*W\b", re.I)
NAME_H = re.compile(r"(\d{2}(?:\.\d+)?)\s*(?:-|\s)?\s*(?:\"|inches|inch|in\b)\s*(?:tall|high|h\b)?", re.I)
EXCLUDE_ANY = re.compile(r"\bsham\b|slipcover only|cover only|replacement|bulb only|\bdog\b|\bcat\b|\bpet\b|outdoor|patio|garden", re.I)


def kind_of(ptype: str, name: str, node: str) -> str | None:
    """The product name is reliable, ABO product types and nodes are sometimes wrong
    (a loveseat filed as OTTOMAN), so the name decides first and the node only breaks ties."""
    if ptype not in ALL_TYPES or EXCLUDE_ANY.search(name):
        return None
    low = name.lower()
    for kind, _types, pat, excl in KINDS:
        if re.search(pat, low) and not (excl and re.search(excl, low)):
            return kind
    text = f"{low} {node.lower()}"
    for kind, types, pat, excl in KINDS:
        if ptype in types and re.search(pat, text) and not (excl and re.search(excl, text)):
            return kind
    return None


def name_width_cm(name: str) -> float | None:
    m = NAME_W.search(name)
    return float(m.group(1)) * IN2CM if m else None


def name_lamp_height_cm(name: str, lo_in: float, hi_in: float) -> float | None:
    for m in NAME_H.finditer(name):
        v = float(m.group(1))
        if lo_in <= v <= hi_in:
            return v * IN2CM
    return None


def dims_cm(kind: str, dims: dict, name: str) -> tuple[dict | None, list[str]]:
    """(w, d, h) in cm where w >= d is the footprint and h is the vertical size, plus notes.

    ABO dimensions are sometimes the shipping box or the lamp base, and sometimes the width is
    filed as height. Sizes written in the product name (116"W, 58 Inches, 5' x 8') win."""
    notes = []
    h = inches(dims.get("height"))
    l = inches(dims.get("length"))
    w = inches(dims.get("width"))
    if not all(x and x > 0 for x in (h, l, w)):
        return None, ["missing_or_zero"]
    h, l, w = h * IN2CM, l * IN2CM, w * IN2CM
    if kind == "throw_pillow":
        a, b, c = sorted((h, l, w), reverse=True)
        out = {"w": a, "d": max(c, 8.0) if c < 3 else c, "h": b}   # flat covers: assume a filled insert
        if c < 3:
            notes.append("cover_thickness_assumed")
    elif kind in ("floor_lamp", "table_lamp"):
        lo, hi = (45, 85) if kind == "floor_lamp" else (10, 36)
        nh = name_lamp_height_cm(name, lo, hi)
        out = {"w": max(l, w), "d": min(l, w), "h": h}
        if nh and abs(nh - h) / nh > 0.1:
            notes.append(f"name_height_fix:{h:.0f}->{nh:.0f}")
            out["h"] = nh
            if kind == "floor_lamp" and out["w"] > 0.6 * nh:
                out["w"], out["d"] = min(out["w"], 50.0), min(out["d"], 50.0)
    elif kind not in ("wall_art", "mirror", "wall_shelf", "rug", "runner_rug", "throw_blanket") and name_width_cm(name):
        nw = name_width_cm(name)
        vals = {"height": h, "length": l, "width": w}
        close = min(vals, key=lambda k: abs(vals[k] - nw))
        if abs(vals[close] - nw) / nw < 0.1:
            others = [vals[k] for k in vals if k != close]
            if close == "height":
                notes.append("width_filed_as_height")
                out = {"w": nw, "d": max(others), "h": min(others)}
            else:
                rest = [vals[k] for k in ("length", "width") if k != close]
                out = {"w": nw, "d": rest[0], "h": h}
        else:
            notes.append("name_width_only")
            out = {"w": nw, "d": min(l, w, nw), "h": h}
    elif kind in ("wall_art", "mirror", "wall_shelf"):
        # hung on a wall: the two larger values are the face, the smallest is the depth
        a, b, c = sorted((h, l, w), reverse=True)
        out = {"w": a, "d": c, "h": b}
        if "portrait" in name.lower() or (h >= max(l, w) * 0.98):
            out = {"w": b, "d": c, "h": a}
    elif kind == "bedding":
        # ABO dimensions of bedding are the folded package; the bed size in the name is the real size
        low = name.lower()
        size = next((v for k, v in (("california king", (183, 213)), ("king", (264, 228)), ("queen", (228, 228)),
                                    ("full", (228, 228)), ("twin xl", (173, 228)), ("twin", (173, 218)))
                     if k in low), None)
        if not size:
            return None, ["bedding_size_unknown"]
        out = {"w": float(size[0]), "d": float(size[1]), "h": 3.0}
        notes.append("bed_size_from_name")
    elif kind == "throw_blanket" and THROW_SIZE.search(name):
        m = THROW_SIZE.search(name)
        a, b = sorted((float(m.group(1)) * IN2CM, float(m.group(2)) * IN2CM), reverse=True)
        out = {"w": a, "d": b, "h": 1.0}
        notes.append("throw_size_from_name")
    elif kind in ("rug", "runner_rug", "throw_blanket"):
        a, b, c = sorted((h, l, w), reverse=True)
        out = {"w": a, "d": b, "h": c}
        named = rug_name_size(name) if kind != "throw_blanket" else None
        if named:
            nl, ns = named
            if abs(nl - a) / nl > 0.12 or abs(ns - b) / ns > 0.12:
                notes.append(f"name_size_fix:{a:.0f}x{b:.0f}->{nl:.0f}x{ns:.0f}")
                out = {"w": nl, "d": ns, "h": min(c, 3.0)}
        if ROUND.search(name):
            notes.append("round")
    else:
        out = {"w": max(l, w), "d": min(l, w), "h": h}
    rng = RANGES.get(kind)
    if rng:
        for k, (lo, hi) in rng.items():
            if not (lo <= out[k] <= hi):
                notes.append(f"out_of_range:{k}={out[k]:.0f}")
    return {k: round(v, 1) for k, v in out.items()}, notes


def load_images(path: pathlib.Path) -> dict[str, tuple[int, int, str]]:
    img = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            img[r["image_id"]] = (int(r["width"]), int(r["height"]), r["path"])
    return img


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--listings", default=str(ABO / "listings.json.gz"))
    ap.add_argument("--images", default=str(ABO / "images.csv.gz"))
    ap.add_argument("--out", default=str(ABO / "catalog_candidates.jsonl"))
    args = ap.parse_args()

    img = load_images(pathlib.Path(args.images))
    stats = collections.Counter()
    by_kind = collections.Counter()
    dropped = collections.Counter()
    fixes = collections.Counter()
    seen = set()
    rows = []
    with gzip.open(args.listings, "rt", encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            if d.get("domain_name") != "amazon.com":
                continue
            ptype = d.get("product_type")
            if isinstance(ptype, list):
                ptype = ptype[0].get("value") if ptype and isinstance(ptype[0], dict) else None
            name_raw = en(d, "item_name") or ""
            node = " ".join(str(n.get("node_name") or "") for n in d.get("node") or [] if isinstance(n, dict))
            kind = kind_of(ptype or "", name_raw, node)
            if not kind:
                continue
            stats["matched"] += 1
            key = d["item_id"]
            if key in seen:
                dropped["duplicate"] += 1
                continue
            seen.add(key)
            dims = d.get("item_dimensions") or {}
            cm, notes = dims_cm(kind, dims, name_raw)
            if cm is None:
                dropped[f"{kind}:no_dims"] += 1
                continue
            if any(n.startswith("out_of_range") for n in notes):
                dropped[f"{kind}:range"] += 1
                continue
            for n in notes:
                if n.startswith("name_size_fix"):
                    fixes[kind] += 1
            ids = [d.get("main_image_id")] + list(d.get("other_image_id") or [])
            images = []
            for iid in ids:
                if iid and iid in img and iid not in [x["id"] for x in images]:
                    w, h, p = img[iid]
                    images.append({"id": iid, "path": p, "w": w, "h": h})
            if not images:
                dropped[f"{kind}:no_image"] += 1
                continue
            color = None
            for x in d.get("color", []) or []:
                if str(x.get("language_tag", "")).startswith("en_"):
                    color = {"name": clean_text(x.get("value")),
                             "std": [clean_text(s) for s in x.get("standardized_values") or []]}
                    break
            brand = clean_text(en(d, "brand"))
            name = clean_text(name_raw)
            row = {
                "id": key,
                "kind": kind,
                "abo_type": ptype,
                "brand": brand,
                "name": name,
                "name_ko": clean_text(lang(d, "item_name", "ko_KR")) or None,
                "amazon_brand": bool(re.match(r"^\s*Amazon\s*Brand", name_raw, re.I)),
                "size_cm": cm,
                "size_notes": notes,
                "weight_lb": (d.get("item_weight") or [{}])[0].get("value"),
                "color": color,
                "color_code": (d.get("color_code") or [None])[0],
                "material": clean_text(en(d, "material")) or None,
                "fabric": clean_text(en(d, "fabric_type")) or None,
                "finish": clean_text(en(d, "finish_type")) or None,
                "style": clean_text(en(d, "style")) or None,
                "pattern": clean_text(en(d, "pattern")) or None,
                "shape": clean_text(en(d, "item_shape")) or None,
                "node": clean_text(node) or None,
                "bullets": [clean_text(x.get("value")) for x in (d.get("bullet_point") or [])
                            if str(x.get("language_tag", "")).startswith("en_")][:6],
                "images": images,
                "has_3d": bool(d.get("3dmodel_id")),
                "url": f"https://www.amazon.com/dp/{key}",
            }
            rows.append(row)
            by_kind[kind] += 1

    out = pathlib.Path(args.out)
    with out.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    dash_left = sum(1 for r in rows if DASHES.search(json.dumps(r, ensure_ascii=False)))
    print(f"matched {stats['matched']} -> kept {len(rows)} -> {out}")
    print("kept by kind:", ", ".join(f"{k} {v}" for k, v in by_kind.most_common()))
    print("rug name-size fixes:", dict(fixes))
    print("dropped:", ", ".join(f"{k} {v}" for k, v in dropped.most_common(30)))
    print("images per product (mean):", round(sum(len(r["images"]) for r in rows) / max(1, len(rows)), 2))
    print("rows still containing an en/em dash:", dash_left)


if __name__ == "__main__":
    main()
