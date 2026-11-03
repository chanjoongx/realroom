"""Product catalog: real products with real dimensions (Amazon Berkeley Objects, CC BY 4.0).

The search narrows a slot the planner asked for ("a natural jute rug, about 160 x 230 cm") down to a
short list an agent can look at:

  kind  ->  fits the room (real size within tolerance of the planned size)  ->  looks right
  (text similarity over the open-weight model's product descriptions, plus Meta Perception Encoder
  image embeddings when they are available)  ->  render-aware penalty (fine patterns and spindly
  shapes are hard for image models to copy exactly)  ->  top k.

Every step reports its count, so the interface can show the funnel (670 rugs -> 112 fit -> 24).
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

# Meta Perception Encoder, PE-Core B16-224 (Meta FAIR, Apache 2.0), loaded through open_clip from
# timm/PE-Core-B-16. The catalog photos are embedded once (catalog/modal_catalog.py --step embed);
# a search runs only the text tower, on the look the planner wrote.
EMB_MODEL, EMB_PRETRAINED, EMB_MODEL_ID = "PE-Core-B-16", "meta", "pe-core-b16-224"
EMB_FILE, EMB_IDS = "emb_pe_core_b16.npy", "emb_pe_core_b16_ids.json"

KINDS = [
    "rug", "runner_rug", "floor_lamp", "table_lamp", "throw_pillow", "throw_blanket", "bedding", "sofa",
    "sectional", "accent_chair", "dining_chair", "office_chair", "coffee_table", "side_table", "console_table",
    "dining_table", "desk", "ottoman", "bench", "bed", "bookcase", "wall_shelf", "wall_art", "mirror", "planter",
    "vase",
]

# which real dimensions must match the planned size, and how loosely (ratio band)
SIZE_RULES: dict[str, tuple[tuple[str, ...], float]] = {
    "rug": (("w", "d"), 0.30), "runner_rug": (("w", "d"), 0.35), "floor_lamp": (("h",), 0.18),
    "table_lamp": (("h",), 0.30), "throw_pillow": (("w",), 0.30), "throw_blanket": (("w",), 0.40),
    "bedding": (("w",), 0.18), "sofa": (("w",), 0.22), "sectional": (("w",), 0.25),
    "accent_chair": (("w",), 0.30), "dining_chair": (("w",), 0.35), "office_chair": (("w",), 0.35),
    "coffee_table": (("w",), 0.30), "side_table": (("h",), 0.30), "console_table": (("w",), 0.30),
    "dining_table": (("w",), 0.30), "desk": (("w",), 0.30), "ottoman": (("w",), 0.40), "bench": (("w",), 0.35),
    "bed": (("w",), 0.20), "bookcase": (("h",), 0.30), "wall_shelf": (("w",), 0.40), "wall_art": (("w", "h"), 0.40),
    "mirror": (("h",), 0.40), "planter": (("h",), 0.50), "vase": (("h",), 0.50),
}

# Bedding (a duvet, comforter or coverlet) hangs over the mattress edges, so it is always wider than the
# bed it covers: the planner sizes a bedding slot by that bed (a 120 cm mattress), and a product fits from
# about the mattress width up to this much wider (both sides draping at most to the floor).
BEDDING_DRAPE_CM = 100

# Products whose design is someone else's character or trademark (a Disney comforter, a London
# Underground map). The photos are CC BY 4.0, but the characters printed on the products are not ours to
# restage in someone's room, so the search never offers them.
LICENSED = re.compile(r"\b(disney|marvel|star wars|minnie|mickey|spider-?man|batman|superman|avengers|pok[eé]mon|"
                      r"hello kitty|paw patrol|harry potter|peppa|sesame street|barbie|lego|jurassic|transformers|"
                      r"minecraft|fortnite|super mario|nintendo|looney tunes|scooby|spongebob|nfl|nba|mlb|ncaa|"
                      r"london underground|coca-?cola)\b|frozen character", re.I)

RISK_PENALTY = {"low": 0.0, "medium": 0.06, "high": 0.25}
WORD = re.compile(r"[a-z]+")
STOP = set("a an the and or with of in on for to by from at as is are this that its it set piece pcs inch inches cm".split())


def tokens(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP and len(w) > 1]


def bedding_band(size: dict | None, widen: float = 1.0) -> tuple[float, float]:
    """The widths (cm) a bedding product may have for a bed this wide: (0, 0) when no width is planned."""
    want = float((size or {}).get("w") or 0)
    if want <= 0:
        return 0.0, 0.0
    return round(want * 0.95, 1), round(want + BEDDING_DRAPE_CM * widen, 1)


@dataclass
class Product:
    id: str
    kind: str
    title: str
    name: str
    brand: str
    size_cm: dict
    color: str | None
    color_family: str | None
    materials: list[str]
    style: list[str]
    description: str
    render_risk: str
    cutout: bool
    sam_phrase: str
    url: str
    raw: dict = field(repr=False, default_factory=dict)

    def size_text(self) -> str:
        s = self.size_cm
        return f"{s['w']:g} x {s['d']:g} x {s['h']:g} cm"

    def public(self) -> dict:
        """What the interface needs about a product (no image bytes)."""
        s = self.size_cm
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "name": self.name, "brand": self.brand,
            "size_cm": s, "size_in": {k: round(v / 2.54, 1) for k, v in s.items()},
            "color": self.color, "materials": self.materials[:3], "style": self.style[:3],
            "description": self.description, "render_risk": self.render_risk, "url": self.url,
            "thumb": f"catalog/thumb/{self.id}.webp", "image": f"catalog/img/{self.id}.jpg",
            "source": "Amazon Berkeley Objects (Amazon.com, CC BY 4.0)",
        }


class Catalog:
    def __init__(self, root: str | pathlib.Path, photos: str | pathlib.Path | None = None):
        """root holds catalog.jsonl and the embeddings; photos holds img/ and thumb/ (default: root)."""
        self.root = pathlib.Path(root)
        self.photos = pathlib.Path(photos) if photos else self.root
        self.products: dict[str, Product] = {}
        for line in (self.root / "catalog.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            t = r.get("tag") or {}
            self.products[r["id"]] = Product(
                id=r["id"], kind=r["kind"], title=t.get("title") or r["name"][:60], name=r["name"],
                brand=r.get("brand") or "", size_cm=r["size_cm"],
                color=", ".join(t.get("colors") or []) or (r.get("color") or {}).get("name"),
                color_family=t.get("color_family"), materials=t.get("materials") or [], style=t.get("style") or [],
                description=t.get("visual_description") or r["name"], render_risk=t.get("render_risk") or "medium",
                cutout=bool(r.get("cutout")), sam_phrase=t.get("sam_phrase") or r["kind"].replace("_", " "),
                url=r["url"], raw=r)
        self.ids = list(self.products)
        self.licensed = {pid for pid, p in self.products.items()
                         if LICENSED.search(" ".join([p.name, p.title, p.description]))}
        # the kinds a plan may ask for: the ones this catalog can actually offer
        offered = {p.kind for pid, p in self.products.items() if pid not in self.licensed}
        self.kinds = [k for k in KINDS if k in offered]
        self._index_text()
        self.emb = None
        self.emb_row: dict[str, int] = {}
        self.emb_error: str | None = None
        ep, ip = self.root / EMB_FILE, self.root / EMB_IDS
        if ep.exists() and ip.exists():
            self.emb = np.load(ep).astype(np.float32)
            self.emb_row = {pid: i for i, pid in enumerate(json.loads(ip.read_text()))}
        else:
            self.emb_error = f"no {EMB_FILE} in the catalog folder"
        self._text_model = None

    # ------------------------------------------------------------------ text index
    def _doc(self, p: Product) -> str:
        return " ".join([p.title, p.description, p.color or "", p.color_family or "", " ".join(p.materials),
                         " ".join(p.style), p.name])

    def _index_text(self) -> None:
        docs = {pid: Counter(tokens(self._doc(p))) for pid, p in self.products.items()}
        df = Counter()
        for c in docs.values():
            df.update(c.keys())
        n = max(1, len(docs))
        self.idf = {w: math.log((n + 1) / (d + 0.5)) for w, d in df.items()}
        self.doc_vec = {}
        for pid, c in docs.items():
            v = {w: (1 + math.log(f)) * self.idf[w] for w, f in c.items()}
            norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
            self.doc_vec[pid] = {w: x / norm for w, x in v.items()}

    def text_sim(self, query: str, pids: list[str]) -> np.ndarray:
        q = Counter(tokens(query))
        qv = {w: (1 + math.log(f)) * self.idf.get(w, 0.0) for w, f in q.items()}
        qn = math.sqrt(sum(x * x for x in qv.values())) or 1.0
        return np.array([sum(qv[w] / qn * self.doc_vec[p].get(w, 0.0) for w in qv) for p in pids], dtype=np.float32)

    # ------------------------------------------------------------------ image embeddings
    def _encode_text(self, text: str) -> np.ndarray | None:
        if self.emb is None:
            return None
        try:
            if self._text_model is None:
                import open_clip
                import torch  # noqa: F401

                model, _, _ = open_clip.create_model_and_transforms(EMB_MODEL, pretrained=EMB_PRETRAINED)
                model.visual = None  # the photos are embedded already; a search needs only the text tower
                self._text_model = (model.eval(), open_clip.get_tokenizer(EMB_MODEL))
            import torch

            model, tok = self._text_model
            with torch.no_grad():
                f = model.encode_text(tok([text.lower()]))
                f = torch.nn.functional.normalize(f.float(), dim=-1)
            return f[0].numpy()
        except Exception as e:  # noqa: BLE001  (no torch or open_clip: text similarity alone still works)
            self.emb = None
            self.emb_error = f"{type(e).__name__}: {e}"[:200]
            print(f"catalog: {EMB_MODEL_ID} unavailable, ranking by text only ({self.emb_error})", file=sys.stderr)
            return None

    def image_sim(self, query: str, pids: list[str]) -> np.ndarray | None:
        q = self._encode_text(f"a photo of {query}")
        if q is None:
            return None
        rows = [self.emb_row.get(p) for p in pids]
        out = np.full(len(pids), np.nan, dtype=np.float32)
        have = [i for i, r in enumerate(rows) if r is not None]
        if have:
            out[have] = self.emb[[rows[i] for i in have]] @ q
        return out

    # ------------------------------------------------------------------ search
    def fits(self, p: Product, kind: str, size: dict | None, widen: float = 1.0) -> bool:
        if not size:
            return True
        dims, tol = SIZE_RULES.get(kind, (("w",), 0.3))
        tol *= widen
        s = p.size_cm
        if kind == "bedding":
            lo, hi = bedding_band(size, widen)
            return lo <= 0 or lo <= s["w"] <= hi
        if kind in ("rug", "runner_rug"):
            a, b = sorted((size.get("w", 0), size.get("d", 0)), reverse=True)
            x, y = sorted((s["w"], s["d"]), reverse=True)
            if a <= 0 or b <= 0:
                return True
            return (1 - tol) <= x / a <= (1 + tol) and (1 - tol) <= y / b <= (1 + tol)
        for d in dims:
            want = float(size.get(d) or 0)
            if want <= 0:
                continue
            if not ((1 - tol) <= s[d] / want <= (1 + tol)):
                return False
        return True

    def search(self, kind: str, look: str, size: dict | None = None, k: int = 24, exclude: set[str] | None = None,
               color_family: str | None = None) -> dict:
        exclude = (exclude or set()) | self.licensed
        pool = [p for p in self.products.values() if p.kind == kind and p.id not in exclude]
        funnel = {"kind": len(pool)}
        # widen the size band only until there is a real choice (never so far that everything "fits")
        need = min(len(pool) // 2, max(8, k // 2))
        fit, widen = [], 1.0
        while True:
            fit = [p for p in pool if self.fits(p, kind, size, widen)]
            if len(fit) >= need or widen >= 2.0:
                break
            widen += 0.25
        funnel["fits"] = len(fit)
        funnel["size_tolerance"] = round(SIZE_RULES.get(kind, (("w",), 0.3))[1] * widen, 2)
        if kind == "bedding" and size:
            lo, hi = bedding_band(size, widen)
            if lo > 0:
                funnel["fit_rule"] = f"{lo:.0f} to {hi:.0f} cm wide"
        clean = [p for p in fit if p.raw.get("tag", {}).get("matches_listing", True)]
        funnel["clean"] = len(clean)
        if not clean:
            return {"funnel": funnel, "items": []}
        pids = [p.id for p in clean]
        ts = self.text_sim(look, pids)
        isim = self.image_sim(look, pids)
        score = (ts - ts.mean()) / (ts.std() + 1e-6)
        used = ["text"]
        if isim is not None and not np.all(np.isnan(isim)):
            iz = np.where(np.isnan(isim), np.nanmean(isim), isim)
            score = 0.45 * score + 0.55 * (iz - iz.mean()) / (iz.std() + 1e-6)
            used.append("pe-core")
        for i, p in enumerate(clean):
            score[i] -= 4 * RISK_PENALTY.get(p.render_risk, 0.06)
            if not p.cutout:
                score[i] -= 0.5
            if color_family and p.color_family == color_family:
                score[i] += 0.3
        order = np.argsort(-score)[:k]
        items = [{"id": pids[i], "score": round(float(score[i]), 3)} for i in order]
        funnel["shortlist"] = len(items)
        funnel["ranked_by"] = used
        return {"funnel": funnel, "items": items}

    def image_path(self, pid: str) -> pathlib.Path:
        return self.photos / "img" / f"{pid}.jpg"

    def thumb_path(self, pid: str) -> pathlib.Path:
        return self.photos / "thumb" / f"{pid}.webp"


def contact_sheet(cat: Catalog, pids: list[str], cols: int = 6, tile: int = 256, label: bool = True) -> np.ndarray:
    """Numbered grid of product thumbnails (1-based numbers) for an agent to look at in one image."""
    from PIL import Image, ImageDraw

    from .imgutil import FONT_BOLD, _font

    rows = math.ceil(len(pids) / cols)
    pad = 6
    sheet = Image.new("RGB", (cols * (tile + pad) + pad, rows * (tile + pad) + pad), (236, 233, 228))
    draw = ImageDraw.Draw(sheet)
    font = _font(FONT_BOLD, 30)  # Arial Bold on Windows, DejaVu Sans Bold on Linux, Pillow's own font last
    for i, pid in enumerate(pids):
        r, c = divmod(i, cols)
        x, y = pad + c * (tile + pad), pad + r * (tile + pad)
        p = cat.thumb_path(pid)
        if p.exists():
            im = Image.open(p).convert("RGB").resize((tile, tile), Image.LANCZOS)
            sheet.paste(im, (x, y))
        if label:
            t = str(i + 1)
            tw, th = draw.textbbox((0, 0), t, font=font)[2:]
            draw.rounded_rectangle([x + 6, y + 6, x + 6 + tw + 16, y + 6 + th + 12], radius=8, fill=(20, 20, 20))
            draw.text((x + 14, y + 8), t, fill=(255, 255, 255), font=font)
    return np.asarray(sheet)
