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


def test_text_index_ranks_the_obvious_match_first(catalog):
    sofas = [pid for pid, p in catalog.products.items() if p.kind == "sofa"]
    lamps = [pid for pid, p in catalog.products.items() if p.kind == "floor_lamp"]
    assert sofas and lamps
    pids = sofas[:50] + lamps[:50]
    sim = catalog.text_sim("floor lamp with a warm shade", pids)
    assert sim.shape == (100,) and float(sim.min()) >= 0 and float(sim.max()) <= 1.0001
    top = [pids[i] for i in np.argsort(-sim)[:10]]
    assert sum(catalog.products[pid].kind == "floor_lamp" for pid in top) >= 8
