"""The three recorded runs must replay: every file they name exists and the numbers in the README hold."""
from __future__ import annotations

import json
import re

import pytest

from conftest import DEMO_IDS, DEMO_RUNS

IMAGE_REF = re.compile(r"^[A-Za-z0-9_.-]+\.(?:jpg|png|webp)$")


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


def _run(run_id: str) -> dict:
    return json.loads((DEMO_RUNS / run_id / "run.json").read_text(encoding="utf-8"))


def _ledger(run_id: str) -> list[dict]:
    text = (DEMO_RUNS / run_id / "ledger.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_demos_list_matches_folders():
    listed = json.loads((DEMO_RUNS / "demos.json").read_text(encoding="utf-8"))
    assert tuple(d["id"] for d in listed) == DEMO_IDS


@pytest.mark.parametrize("run_id", DEMO_IDS)
def test_run_is_complete_and_labeled(run_id):
    run = _run(run_id)
    assert run["status"] == "done"
    assert run["room"]["ai_generated"] is True, "demo rooms are generated with Muse Image and must say so"
    assert "trace" not in run or isinstance(run["trace"], (list, dict))
    proof = run["result"]["proof"]
    assert 0 < proof["untouched_pct"] < 100
    assert abs(proof["untouched_pct"] + proof["edited_pct"] - 100) < 0.05


@pytest.mark.parametrize("run_id", DEMO_IDS)
def test_every_named_image_exists(run_id):
    run = _run(run_id)
    names = {s for s in _strings(run) if IMAGE_REF.match(s)}
    assert names, "a run names its images"
    missing = sorted(n for n in names if not (DEMO_RUNS / run_id / n).exists())
    assert not missing, f"{run_id} names images that are not in the folder: {missing}"


@pytest.mark.parametrize("run_id", DEMO_IDS)
def test_cost_is_the_ledger_total(run_id):
    run = _run(run_id)
    total = round(sum(r.get("usd", 0.0) for r in _ledger(run_id)), 4)
    assert abs(total - run["cost_usd"]) < 0.001


@pytest.mark.parametrize("run_id", DEMO_IDS)
def test_ledger_names_only_meta_models(run_id):
    allowed_models = {"muse-spark-1.3", "muse-image-1.0", "sam-3.1", "pe-core-b16-224"}
    allowed_kinds = {"spark", "sam", "image_gen", "image_edit", "image_compose", "pe_core"}
    for row in _ledger(run_id):
        assert row["kind"] in allowed_kinds, row
        if "model" in row:
            assert row["model"] in allowed_models, row


def test_readme_table_matches_runs():
    """The README's demo table quotes untouched pixels, seconds and dollars from each run."""
    readme = (DEMO_RUNS.parent / "README.md").read_text(encoding="utf-8")
    for run_id in DEMO_IDS:
        run = _run(run_id)
        assert f"{run['result']['proof']['untouched_pct']:.1f}%" in readme, run_id
        assert f"{round(run['elapsed_s'])}&nbsp;s" in readme, run_id
        assert f"${run['cost_usd']:.2f}" in readme, run_id


RANK = {"match": 4, "partial": 3, "mismatch": 2, "missing": 1}


def record_verdict(own: dict, final: dict | None) -> dict:
    """The worse of a product's own verdict and the final check of the finished room, the rule the app shows."""
    seen = [v for v in (own, final) if v]
    return min(seen, key=lambda v: (RANK.get(v.get("verdict"), 0), v.get("score") or 0)) if seen else {}


def matched(run: dict) -> int:
    res = run["result"]
    finals = {f.get("index"): f for f in res.get("final_check_products") or []}
    return sum(1 for k, p in enumerate(res["products"])
               if record_verdict(p.get("verdict") or {}, finals.get(k + 1)).get("verdict") == "match")


def test_readme_matched_column():
    """A product counts as matched only when every check of its final pixels says match (studio: the rug is partial)."""
    readme = (DEMO_RUNS.parent / "README.md").read_text(encoding="utf-8").splitlines()
    rows = {"living": "Living room, 2005 apartment", "moving-day": "Empty room, move-in day",
            "studio": "Studio, first apartment"}
    counts = {}
    for run_id, label in rows.items():
        run = _run(run_id)
        counts[run_id] = matched(run)
        line = next(x for x in readme if x.startswith(f"| {label} |"))
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert cells[3].startswith(f"{counts[run_id]} of {len(run['result']['products'])}"), (run_id, cells[3])
    assert counts == {"living": 3, "moving-day": 5, "studio": 2}
