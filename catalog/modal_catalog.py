"""Build the Realroom catalog images and embeddings on Modal (ABO, CC BY 4.0, credit Amazon.com).

Runs in Modal's cloud so the home connection makes no bulk requests:
  1. score   stream archives/abo-images-small.tar once (3.25 GB, 256 px images) and score every
             image of every candidate product: is it a clean product shot on white?
  2. choose  (local) pick one reference image per product: the main image when it is a clean
             cutout, otherwise the best clean alternative, otherwise the main image (lifestyle)
  3. fetch   download that one original per product from images/original/<path>, trim white
             margins, write img/<id>.jpg (1024 px) and thumb/<id>.webp (480 px square)
  4. tag     Muse Spark Open (open weights; Baseten first, Fireworks as fallback) reads each product
             photo with its listing and writes the tags the search uses (resumable, tags.jsonl)
  5. embed   SigLIP 2 image embeddings (CPU) for text-to-image look search

usage:
  modal run catalog/modal_catalog.py --step score
  python catalog/abo_choose.py
  modal run catalog/modal_catalog.py --step fetch
  modal run catalog/modal_catalog.py --step tag
  modal run catalog/modal_catalog.py --step embed
"""
from __future__ import annotations

import json
import os
import pathlib

import modal

S3 = "https://amazon-berkeley-objects.s3.amazonaws.com"
EMB_MODEL = "google/siglip2-base-patch16-256"
UA = "Realroom catalog builder (Meta AI hackathon 2026, github.com/chanjoongx/realroom)"

app = modal.App("realroom-catalog")
vol = modal.Volume.from_name("realroom-catalog", create_if_missing=True)
cpu_image = modal.Image.debian_slim(python_version="3.12").pip_install("requests", "pillow", "numpy")
# CPU only: the workspace has credits but no payment method, and Modal refuses GPU functions then
emb_image = (modal.Image.debian_slim(python_version="3.12")
             .pip_install("torch==2.6.0", index_url="https://download.pytorch.org/whl/cpu")
             .pip_install("transformers==4.51.3", "pillow", "numpy", "sentencepiece", "protobuf"))

DATA = pathlib.Path(os.environ.get("REALROOM_DATA", pathlib.Path(__file__).resolve().parents[1] / "data"))
LOCAL = DATA / "abo"


def _score(im) -> dict:
    """Cutout score of a small catalog image: white border, object coverage and solidity."""
    import numpy as np

    a = np.asarray(im.convert("RGB"), dtype=np.int16)
    h, w = a.shape[:2]
    white = (a.min(axis=2) >= 236) & ((a.max(axis=2) - a.min(axis=2)) <= 14)
    b = max(2, min(h, w) // 40)
    ring = np.concatenate([white[:b].ravel(), white[-b:].ravel(), white[:, :b].ravel(), white[:, -b:].ravel()])
    border = float(ring.mean())
    obj = ~white
    cov = float(obj.mean())
    if obj.any():
        ys, xs = np.where(obj)
        y1, y2, x1, x2 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        solid = float(obj[y1:y2, x1:x2].mean())
        fill = float((y2 - y1) * (x2 - x1)) / (h * w)
    else:
        solid, fill = 0.0, 0.0
    sat = float((a.max(axis=2) - a.min(axis=2))[obj].mean()) if obj.any() else 0.0
    return {"border": round(border, 3), "cov": round(cov, 3), "solid": round(solid, 3), "fill": round(fill, 3),
            "sat": round(sat, 1), "w": int(w), "h": int(h)}


@app.function(image=cpu_image, volumes={"/cat": vol}, timeout=3600, cpu=4.0, memory=8192)
def score_small(wanted: list[str]) -> dict:
    import io
    import tarfile

    import requests
    from PIL import Image

    want = set(wanted)
    out: dict[str, dict] = {}
    n = 0
    with requests.get(f"{S3}/archives/abo-images-small.tar", stream=True, timeout=900,
                      headers={"User-Agent": UA}) as r:
        r.raise_for_status()
        r.raw.decode_content = True
        with tarfile.open(fileobj=r.raw, mode="r|") as tf:
            for m in tf:
                if not m.isfile():
                    continue
                n += 1
                rel = m.name.split("small/", 1)[-1]
                if rel not in want:
                    continue
                data = tf.extractfile(m).read()
                try:
                    out[rel] = _score(Image.open(io.BytesIO(data)))
                except Exception as exc:  # noqa: BLE001
                    out[rel] = {"error": str(exc)[:120]}
    pathlib.Path("/cat/meta").mkdir(parents=True, exist_ok=True)
    pathlib.Path("/cat/meta/small_scores.json").write_text(json.dumps(out))
    vol.commit()
    return {"members": n, "scored": len(out)}


def _trim_box(arr, pad_frac: float = 0.04):
    import numpy as np

    a = arr.astype(np.int16)
    white = (a.min(axis=2) >= 236) & ((a.max(axis=2) - a.min(axis=2)) <= 14)
    obj = ~white
    if not obj.any():
        return None
    # ignore isolated specks: a row/column counts only if it has a few object pixels
    rows = np.where(obj.sum(axis=1) > max(2, obj.shape[1] // 400))[0]
    cols = np.where(obj.sum(axis=0) > max(2, obj.shape[0] // 400))[0]
    if rows.size == 0 or cols.size == 0:
        return None
    h, w = obj.shape
    y1, y2, x1, x2 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    pad = int(max(y2 - y1, x2 - x1) * pad_frac)
    return max(0, x1 - pad), max(0, y1 - pad), min(w, x2 + pad), min(h, y2 + pad)


@app.function(image=cpu_image, volumes={"/cat": vol}, timeout=3600, cpu=4.0, memory=8192)
def fetch_originals(items: list[dict]) -> list[dict]:
    """items: {id, path, cutout}. Writes img/<id>.jpg and thumb/<id>.webp. Never raises per item."""
    import concurrent.futures as cf
    import io
    import time

    import numpy as np
    import requests
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = 80_000_000
    pathlib.Path("/cat/img").mkdir(parents=True, exist_ok=True)
    pathlib.Path("/cat/thumb").mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers["User-Agent"] = UA

    def one(it: dict) -> dict:
        url = f"{S3}/images/original/{it['path']}"
        for attempt in range(3):
            try:
                r = sess.get(url, timeout=60)
                if r.status_code == 200:
                    break
            except requests.RequestException:
                r = None
            time.sleep(1.5 * (attempt + 1))
        if r is None or r.status_code != 200:
            return {"id": it["id"], "error": f"http {getattr(r, 'status_code', 'none')}"}
        try:
            im = Image.open(io.BytesIO(r.content))
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, im).convert("RGB")
            ow, oh = im.size
            score = None
            if it.get("cutout") is None:  # not in the small-image pass: score a 256 px copy now
                small = im.copy()
                small.thumbnail((256, 256))
                score = _score(small)
                it["cutout"] = (score["border"] >= 0.85 and score["cov"] >= 0.10 and score["solid"] >= 0.30
                                and score["fill"] >= 0.15)
            box = _trim_box(np.asarray(im)) if it.get("cutout") else None
            if box:
                im = im.crop(box)
            big = im.copy()
            big.thumbnail((1024, 1024), Image.LANCZOS)
            big.save(f"/cat/img/{it['id']}.jpg", "JPEG", quality=90, optimize=True)
            # square thumbnail on white, object at 88 % of the side
            side = 480
            t = im.copy()
            t.thumbnail((int(side * 0.88), int(side * 0.88)), Image.LANCZOS)
            canvas = Image.new("RGB", (side, side), (255, 255, 255))
            if not it.get("cutout"):
                t = im.copy()
                t.thumbnail((side * 3, side), Image.LANCZOS)  # lifestyle: cover-crop to a square
                l = max(0, (t.width - side) // 2)
                t = t.crop((l, 0, l + min(side, t.width), min(side, t.height)))
            canvas.paste(t, ((side - t.width) // 2, (side - t.height) // 2))
            canvas.save(f"/cat/thumb/{it['id']}.webp", "WEBP", quality=86, method=6)
            return {"id": it["id"], "orig_wh": [ow, oh], "trim_box": [int(v) for v in box] if box else None,
                    "img_wh": list(big.size), "bytes": len(r.content), "cutout": bool(it.get("cutout")),
                    "late_score": score}
        except Exception as exc:  # noqa: BLE001
            return {"id": it["id"], "error": str(exc)[:160]}

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        res = list(pool.map(one, items))
    vol.commit()
    return res


@app.function(image=emb_image, volumes={"/cat": vol}, timeout=3600, cpu=8.0, memory=16384)
def embed_images(ids: list[str]) -> dict:
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModel, AutoProcessor

    torch.set_num_threads(8)
    model = AutoModel.from_pretrained(EMB_MODEL).eval()
    proc = AutoProcessor.from_pretrained(EMB_MODEL)
    vecs, kept = [], []
    bs = 64
    for i in range(0, len(ids), bs):
        chunk = ids[i:i + bs]
        ims, ok = [], []
        for pid in chunk:
            p = pathlib.Path(f"/cat/img/{pid}.jpg")
            if p.exists():
                ims.append(Image.open(p).convert("RGB"))
                ok.append(pid)
        if not ims:
            continue
        with torch.no_grad():
            inp = proc(images=ims, return_tensors="pt")
            f = model.get_image_features(**inp)
            f = torch.nn.functional.normalize(f.float(), dim=-1)
        vecs.append(f.cpu().numpy().astype(np.float16))
        kept += ok
    emb = np.concatenate(vecs, axis=0)
    pathlib.Path("/cat/meta").mkdir(parents=True, exist_ok=True)
    np.save("/cat/meta/emb_siglip2.npy", emb)
    pathlib.Path("/cat/meta/emb_ids.json").write_text(json.dumps(kept))
    # sanity: a few text queries against the catalog
    probes = ["a natural jute area rug", "a white floor lamp with a linen drum shade", "a green velvet accent chair",
              "a framed black and white photo", "a round wooden coffee table"]
    with torch.no_grad():
        t = proc(text=probes, padding="max_length", max_length=64, return_tensors="pt")
        tf = torch.nn.functional.normalize(model.get_text_features(**t).float(), dim=-1).cpu().numpy()
    sims = tf @ emb.astype(np.float32).T
    top = {q: [kept[j] for j in np.argsort(-sims[k])[:5]] for k, q in enumerate(probes)}
    vol.commit()
    return {"n": len(kept), "dim": int(emb.shape[1]), "probe_top5": top}


TAG_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "matches_listing": {"type": "boolean"},
        "clean_cutout": {"type": "boolean"},
        "pieces": {"type": "integer"},
        "view": {"type": "string", "enum": ["front", "three-quarter", "side", "top-down", "detail", "room scene"]},
        "colors": {"type": "array", "items": {"type": "string"}},
        "color_family": {"type": "string", "enum": ["white", "cream", "beige", "brown", "wood", "black", "grey", "blue",
                                                    "green", "red", "pink", "orange", "yellow", "purple", "metallic",
                                                    "multicolor"]},
        "materials": {"type": "array", "items": {"type": "string"}},
        "style": {"type": "array", "items": {"type": "string"}},
        "pattern": {"type": "string", "enum": ["solid", "subtle texture", "bold simple pattern", "fine detailed pattern"]},
        "structure": {"type": "string", "enum": ["simple solid form", "moderate", "thin or intricate parts",
                                                 "transparent or reflective"]},
        "render_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "sam_phrase": {"type": "string"},
        "visual_description": {"type": "string"},
    },
    "required": ["title", "matches_listing", "clean_cutout", "pieces", "view", "colors", "color_family", "materials",
                 "style", "pattern", "structure", "render_risk", "sam_phrase", "visual_description"],
}

TAG_PROMPT = """You index a furniture catalog for an AI that will redraw these products inside real room photos.
Look at the product photo and its listing, then return JSON:
- title: a clean 2 to 6 word product title without brand or size (e.g. "Striated jute area rug")
- matches_listing: false if the photo clearly shows a different kind of product than the listing
- clean_cutout: true only if the whole product is shown alone on a plain light background with no text, dimension lines or graphics
- pieces: how many separate items are shown (a set of 2 pillows is 2)
- view: the camera view of the product
- colors: up to 3 color names as a person would say them, most dominant first; color_family: the main one
- materials, style: up to 3 short words each
- pattern: how hard the surface pattern is to redraw exactly
- structure: how hard the shape is to redraw exactly
- render_risk: low if an image model can copy it convincingly, high if small details decide the identity
- sam_phrase: a 1 to 3 word noun phrase a segmentation model can use to find it in a room photo
- visual_description: one sentence (max 30 words) with shape, proportions, color, material and distinctive details

Listing: {listing}"""


def _listing_text(r: dict) -> str:
    s = r["size_cm"]
    parts = [r["name"], f"kind: {r['kind'].replace('_', ' ')}",
             f"size: {s['w']:g} x {s['d']:g} x {s['h']:g} cm (W x D x H)"]
    if r.get("color"):
        parts.append(f"color: {r['color']['name']}")
    for k in ("material", "fabric", "style", "pattern", "shape"):
        if r.get(k):
            parts.append(f"{k}: {r[k]}")
    return ". ".join(parts)


@app.function(image=cpu_image, volumes={"/cat": vol}, timeout=3600, cpu=2.0, memory=2048, max_containers=4,
              secrets=[modal.Secret.from_name("realroom-keys")])
def tag_products(rows: list[dict]) -> list[dict]:
    """Muse Spark Open (Baseten, Fireworks as fallback) reads each product photo and listing."""
    import base64
    import concurrent.futures as cf
    import os
    import time

    import requests

    providers = [
        ("baseten", "https://inference.baseten.co/v1/chat/completions", os.environ["BASETEN_API_KEY"],
         "meta-models/muse-spark-open-1026"),
        ("fireworks", "https://api.fireworks.ai/inference/v1/chat/completions", os.environ["FIREWORKS_API_KEY"],
         "accounts/fireworks/models/muse-spark-open"),
    ]
    sess = requests.Session()

    def one(r: dict) -> dict:
        p = pathlib.Path(f"/cat/thumb/{r['id']}.webp")
        if not p.exists():
            return {"id": r["id"], "error": "no thumb"}
        img = "data:image/webp;base64," + base64.b64encode(p.read_bytes()).decode()
        body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": TAG_PROMPT.replace("{listing}", _listing_text(r))},
            {"type": "image_url", "image_url": {"url": img}}]}],
            "max_tokens": 3000, "reasoning_effort": "low",
            "response_format": {"type": "json_schema", "json_schema": {"name": "catalog_tag", "schema": TAG_SCHEMA}}}
        last = ""
        for name, url, key, model in providers:
            for attempt in range(3):
                t0 = time.time()
                try:
                    resp = sess.post(url, json={**body, "model": model}, timeout=180,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
                except requests.RequestException as exc:
                    last = f"{name}: {exc}"[:200]
                    time.sleep(2 * (attempt + 1))
                    continue
                if resp.status_code == 200:
                    data = resp.json()
                    try:
                        tag = json.loads(data["choices"][0]["message"]["content"] or "")
                    except (json.JSONDecodeError, KeyError, TypeError):
                        last = f"{name}: bad json"
                        continue
                    u = data.get("usage") or {}
                    return {"id": r["id"], "tag": tag, "provider": name, "sec": round(time.time() - t0, 2),
                            "in_tok": u.get("prompt_tokens"), "out_tok": u.get("completion_tokens")}
                last = f"{name}: http {resp.status_code} {resp.text[:160]}"
                time.sleep((4 if resp.status_code == 429 else 2) * (attempt + 1))
        return {"id": r["id"], "error": last}

    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        return list(pool.map(one, rows))


@app.local_entrypoint()
def main(step: str = "score", limit: int = 0):
    rows = [json.loads(l) for l in (LOCAL / "catalog_candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    if limit:
        rows = rows[:limit]
    if step == "score":
        paths = sorted({im["path"] for r in rows for im in r["images"]})
        print(f"scoring {len(paths)} small images of {len(rows)} products")
        print(score_small.remote(paths))
    elif step == "fetch":
        chosen = json.loads((LOCAL / "chosen_images.json").read_text(encoding="utf-8"))
        items = [{"id": pid, "path": c["path"], "cutout": c["cutout"]} for pid, c in chosen.items()]
        chunks = [items[i:i + 150] for i in range(0, len(items), 150)]
        print(f"fetching {len(items)} originals in {len(chunks)} chunks")
        results = []
        for res in fetch_originals.map(chunks, order_outputs=False):
            results += res
            print(f"  {len(results)}/{len(items)}", flush=True)
        (LOCAL / "fetch_results.json").write_text(json.dumps(results, indent=0), encoding="utf-8")
        bad = [r for r in results if r.get("error")]
        print(f"done: {len(results) - len(bad)} ok, {len(bad)} errors", bad[:5])
    elif step == "embed":
        chosen = json.loads((LOCAL / "chosen_images.json").read_text(encoding="utf-8"))
        fr = {r["id"]: r for r in json.loads((LOCAL / "fetch_results.json").read_text(encoding="utf-8"))}
        ids = [pid for pid in chosen if pid in fr and not fr[pid].get("error")]
        res = embed_images.remote(ids)
        print(json.dumps(res, indent=1))
        (LOCAL / "embed_probe.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    elif step == "tag":
        chosen = json.loads((LOCAL / "chosen_images.json").read_text(encoding="utf-8"))
        fr = {r["id"]: r for r in json.loads((LOCAL / "fetch_results.json").read_text(encoding="utf-8"))}
        out_path = LOCAL / "tags.jsonl"
        done = set()
        if out_path.exists():
            done = {json.loads(l)["id"] for l in out_path.read_text(encoding="utf-8").splitlines()
                    if l.strip() and "tag" in json.loads(l)}
        todo = [r for r in rows if r["id"] in chosen and r["id"] in fr and not fr[r["id"]].get("error")
                and r["id"] not in done]
        slim = [{k: r.get(k) for k in ("id", "kind", "name", "size_cm", "color", "material", "fabric", "style",
                                       "pattern", "shape")} for r in todo]
        chunks = [slim[i:i + 64] for i in range(0, len(slim), 64)]
        print(f"tagging {len(slim)} products in {len(chunks)} chunks ({len(done)} already done)")
        n_ok = n_err = 0
        with out_path.open("a", encoding="utf-8") as fh:
            for res in tag_products.map(chunks, order_outputs=False):
                for x in res:
                    fh.write(json.dumps(x, ensure_ascii=False) + "\n")
                    n_ok += "tag" in x
                    n_err += "error" in x
                fh.flush()
                print(f"  ok {n_ok} err {n_err}", flush=True)
    else:
        raise SystemExit(f"unknown step {step}")
