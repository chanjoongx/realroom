"""Pure helpers: geometry, scoring, prompts and the catalog index. No model calls."""
from __future__ import annotations

import numpy as np
import pytest

from conftest import CATALOG_INDEX


def test_tidy_removes_dashes_from_model_text():
    from realroom.pipeline import tidy

    em, en = chr(0x2014), chr(0x2013)
    assert tidy(f"a sofa {em} light grey") == "a sofa, light grey"
    assert tidy(f"80{en}120 cm") == "80 to 120 cm"
    assert tidy("90 - 140 cm") == "90 to 140 cm"
    assert tidy({"a": [f"x{em}y", 3]}) == {"a": ["x, y", 3]}
    assert tidy("mid-century oak") == "mid-century oak"


def test_norm_to_px_clamps_and_orders():
    from realroom.imgutil import norm_to_px

    assert norm_to_px([500, 500, 0, 0], 1000, 800) == (0, 0, 500, 400)
    assert norm_to_px([-50, 0, 1200, 1000], 1000, 800) == (0, 0, 999, 799)


def test_box_iou():
    from realroom.scan import box_iou

    assert box_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert box_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert box_iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(50 / 150)
    assert box_iou((0, 0, 0, 0), (0, 0, 0, 0)) == 0.0


def test_size_param_is_a_multiple_of_16():
    from realroom.render import size_param

    for w, h in ((1792, 1344), (1200, 1600), (1000, 1000)):
        sw, sh = (int(v) for v in size_param(w, h).split("x"))
        assert sw % 16 == 0 and sh % 16 == 0 and max(sw, sh) <= 1792 + 8


def test_box_prompt_clamps_to_the_image():
    from realroom.meta import box_prompt

    p = box_prompt({"o1": (-5, 10, 2000, 50)}, 1000, 800)
    assert p == "<0f>o1<|box;x1=0;y1=10;x2=999;y2=50;w=1000;h=800|>"


def test_candidate_score_prefers_matching_products():
    from realroom.verify import candidate_score

    scene = {"overall": 8, "photoreal": 8, "cohesion": 8, "renter_rule_ok": True}
    good = {"products": [{"index": 1, "verdict": "match", "score": 9}], "scene": scene}
    weak = {"products": [{"index": 1, "verdict": "partial", "score": 6}], "scene": scene}
    assert candidate_score(good, 1) > candidate_score(weak, 1)
    assert candidate_score({}, 1) == -1.0
    broke_rule = dict(good, scene=dict(scene, renter_rule_ok=False))
    assert candidate_score(broke_rule, 1) < candidate_score(good, 1)


def test_preservation_of_an_identical_image():
    from realroom.composite import preservation

    rng = np.random.default_rng(0)
    img = rng.integers(0, 255, (120, 160, 3), dtype=np.uint8)
    static = np.ones((120, 160), bool)
    stats, de = preservation(img, img.copy(), static)
    assert stats["dE_mean"] == 0 and stats["changed_frac_dE10"] == 0
    assert float(de.max()) == 0


def test_sniff_mime():
    from realroom.meta import sniff_mime

    from conftest import jpeg_bytes

    assert sniff_mime(jpeg_bytes(64, 64)) == "image/jpeg"
    assert sniff_mime(jpeg_bytes(64, 64, fmt="PNG")) == "image/png"
    with pytest.raises(ValueError):
        sniff_mime(b"hello")


@pytest.fixture(scope="module")
def catalog():
    from realroom.catalog import Catalog

    return Catalog(CATALOG_INDEX)


def test_catalog_loads(catalog):
    assert len(catalog.products) == 4375
    assert len(set(catalog.ids)) == len(catalog.ids)
    assert catalog.emb is not None and catalog.emb.shape[1] == 1024
    missing = [pid for pid in catalog.ids if pid not in catalog.emb_row]
    assert not missing, f"{len(missing)} products have no Perception Encoder embedding"


def test_catalog_rows_are_usable(catalog):
    for p in catalog.products.values():
        assert p.kind and p.url.startswith("https://")
        assert all(float(p.size_cm[k]) > 0 for k in ("w", "d", "h")), p.id


def test_bedding_is_sized_by_the_bed_it_covers(catalog):
    """A duvet drapes over the mattress, so a 120 cm bed takes a 173 cm twin comforter (11-02 live run:
    the old plus-or-minus band found none of the 17 for a 120 cm single and dropped the slot)."""
    from realroom.catalog import bedding_band

    assert bedding_band({"w": 120, "d": 200, "h": 15}) == (114.0, 220.0)
    assert bedding_band({"w": 0, "d": 200, "h": 15}) == (0.0, 0.0)
    for bed, must in ((100, 173.0), (120, 173.0), (150, 173.0), (200, 228.0)):
        res = catalog.search("bedding", "plain white duvet cover", {"w": bed, "d": 200, "h": 15})
        widths = {catalog.products[i["id"]].size_cm["w"] for i in res["items"]}
        assert must in widths and min(widths) >= 0.95 * bed, (bed, widths)
        assert res["funnel"]["fit_rule"].endswith("cm wide")


def test_licensed_characters_are_never_offered(catalog):
    assert len(catalog.licensed) >= 7
    names = " ".join(catalog.products[pid].title for pid in catalog.licensed).lower()
    assert "spiderman" in names and "minnie" in names and "london underground" in names
    for kind, look in (("bedding", "kids character comforter, spiderman frozen star wars"),
                       ("wall_art", "london underground map in a black frame")):
        res = catalog.search(kind, look, None, k=200)
        assert not {i["id"] for i in res["items"]} & catalog.licensed


def test_plans_only_ask_for_kinds_the_catalog_offers(catalog):
    from realroom import scan
    from realroom.catalog import KINDS

    assert "dining_table" in KINDS and "dining_table" not in catalog.kinds
    assert set(catalog.kinds) <= set(KINDS) and "rug" in catalog.kinds
    enum = scan.schema_for(catalog.kinds)["properties"]["plan"]["properties"]["slots"]["items"]["properties"]["kind"]["enum"]
    assert enum == catalog.kinds
    assert scan.schema_for(None) is scan.SCHEMA  # the hashed schema stays the full one
    slot = {"slot": "a", "action": "add", "replaces": "", "target_box": [1, 2, 3, 4],
            "size_cm": {"w": 120, "d": 80, "h": 75}, "look": "oak table", "color_family": "brown",
            "placement": "middle", "why": "eat"}
    reading = {"plan": {"slots": [dict(slot, kind="dining_table"), dict(slot, kind="rug")]}}
    kept = scan.normalize_reading(reading, 3, catalog.kinds)["plan"]["slots"]
    assert [s["kind"] for s in kept] == ["rug"]
    assert [s["kind"] for s in scan.normalize_reading(reading, 3)["plan"]["slots"]] == ["dining_table", "rug"]


def test_text_index_ranks_the_obvious_match_first(catalog):
    sofas = [pid for pid, p in catalog.products.items() if p.kind == "sofa"]
    lamps = [pid for pid, p in catalog.products.items() if p.kind == "floor_lamp"]
    assert sofas and lamps
    pids = sofas[:50] + lamps[:50]
    sim = catalog.text_sim("floor lamp with a warm shade", pids)
    assert sim.shape == (100,) and float(sim.min()) >= 0 and float(sim.max()) <= 1.0001
    top = [pids[i] for i in np.argsort(-sim)[:10]]
    assert sum(catalog.products[pid].kind == "floor_lamp" for pid in top) >= 8


def test_sam_phrases_fall_back_to_the_kind_and_the_head_noun():
    from types import SimpleNamespace

    from realroom.render import sam_phrases
    assert sam_phrases(SimpleNamespace(kind="accent_chair", sam_phrase="swivel chair")) == \
        ["swivel chair", "accent chair", "armchair"]
    assert sam_phrases(SimpleNamespace(kind="runner_rug", sam_phrase="runner rug")) == ["runner rug", "rug", "area rug"]
    assert sam_phrases(SimpleNamespace(kind="floor_lamp", sam_phrase="")) == ["floor lamp", "lamp", "standing lamp"]


def test_a_phrase_sam_does_not_find_falls_back_to_a_broader_one_before_the_box(monkeypatch):
    from realroom import meta, render
    h, w = 60, 80
    mask = np.zeros((h, w), bool)
    mask[20:50, 10:40] = True
    asked = []

    def fake_sam(ledger, tag, image_b, prompt, width, height):
        asked.append(prompt)
        if prompt != "accent chair":
            return {}
        return {"1": {"mask": mask, "conf": 0.9, "box": (10, 20, 40, 50), "area": int(mask.sum())}}

    monkeypatch.setattr(meta, "sam_masks", fake_sam)
    got, how = render.segment_product(None, b"", ["swivel chair", "accent chair", "armchair"], (8, 18, 42, 52), h, w,
                                      np.full((h, w), 20.0))
    assert how == "concept:accent chair" and asked == ["swivel chair", "accent chair"]
    assert got["box"] == (10, 20, 40, 50)


def test_saved_images_are_swapped_in_whole(tmp_path):
    """A page can fetch before.jpg while a live run writes it again (on upload and when the pipeline starts).
    The file is swapped in whole, never truncated in place, and a reader that has it open keeps the whole old
    file (11-04 smoke run: the page got a cut-off before.jpg)."""
    import threading
    import time

    from realroom import imgutil

    a = np.zeros((40, 60, 3), np.uint8)
    a[:, :30] = 200
    b = np.full((40, 60, 3), 90, np.uint8)
    for name in ("x.jpg", "x.png"):
        first = imgutil.save(a, tmp_path / name, 94).read_bytes()
        assert imgutil.save(a, tmp_path / name, 94).read_bytes() == first  # same pixels, same bytes
    assert imgutil.load_rgb(imgutil.save(a, tmp_path / "x.webp")).shape == (40, 60, 3)

    p = imgutil.save(a, tmp_path / "before.jpg", 94)
    old = p.read_bytes()
    with open(p, "rb") as f:
        t = threading.Thread(target=imgutil.save, args=(b, p, 94))
        t.start()
        time.sleep(0.1)
        got = f.read()
    t.join(5)
    assert got == old and not t.is_alive()
    assert np.abs(imgutil.load_rgb(p).astype(int) - 90).max() <= 2
    assert sorted(x.name for x in tmp_path.iterdir()) == ["before.jpg", "x.jpg", "x.png", "x.webp"]
