"""Robustness of the model layer and the pipeline around it, with no network: every failure of a model call
becomes meta.ApiError, retries follow the rate-limit rules, model answers are read safely (and well-formed
answers, including the recorded runs', pass through unchanged), and a run that fails or degrades says so
in plain words. The end-to-end runs replace the three model clients with local fakes."""
from __future__ import annotations

import base64
import copy
import email.utils
import json
import re
import time
from types import SimpleNamespace

import httpx
import numpy as np
import openai
import pytest
import requests
from PIL import Image

from conftest import CATALOG_INDEX, DEMO_IDS, DEMO_RUNS
from realroom import imgutil, meta, pipeline, render, scan, verify

PHOTOS = DEMO_RUNS / "_photos"


def _run_json(rid: str) -> dict:
    return json.loads((DEMO_RUNS / rid / "run.json").read_text(encoding="utf-8"))


def _png(w: int = 64, h: int = 48, seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    return imgutil.png_bytes(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))


# ---------------------------------------------------------------- meta: HTTP calls


class FakeResp:
    def __init__(self, status: int = 200, payload=None, text: str | None = None, headers: dict | None = None):
        self.status_code = status
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


@pytest.fixture()
def http(monkeypatch):
    """Scripted requests.post: each call takes the next item (an exception to raise or a FakeResp)."""
    state = SimpleNamespace(script=[], calls=[], sleeps=[])

    def post(url, headers=None, json=None, timeout=None):
        state.calls.append({"url": url, "body": json, "timeout": timeout})
        item = state.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(meta, "_headers", lambda: {"Authorization": "Bearer test", "Content-Type": "application/json"})
    monkeypatch.setattr(meta.requests, "post", post)
    monkeypatch.setattr(meta, "_sleep", state.sleeps.append)
    return state


OK = {"choices": [{"finish_reason": "stop", "message": {"content": '{"a": 1}'}}],
      "usage": {"prompt_tokens": 10, "completion_tokens": 2}}


def _refused():
    import urllib3

    return requests.exceptions.ConnectionError(
        urllib3.exceptions.MaxRetryError(None, "/", reason=urllib3.exceptions.NewConnectionError(None, "refused")))


@pytest.mark.parametrize("make", [_refused, lambda: requests.exceptions.ConnectTimeout("connect")])
def test_connections_never_made_are_retried_then_become_api_error(http, make):
    http.script = [make() for _ in range(meta.ATTEMPTS)]
    with pytest.raises(meta.ApiError) as e:
        meta._post("/chat/completions", {}, 180)
    assert e.value.status == 0 and "connection failed" in str(e.value)
    assert len(http.calls) == meta.ATTEMPTS and len(http.sleeps) == meta.ATTEMPTS - 1
    assert http.calls[0]["timeout"] == (meta.CONNECT_S, 180)


def test_a_connection_dropped_after_sending_is_retried_once_only(http):
    http.script = [requests.exceptions.ConnectionError("reset")] * meta.ATTEMPTS
    with pytest.raises(meta.ApiError, match="connection dropped"):
        meta._post("/images/edits", {}, meta.READ_S["image"])
    assert len(http.calls) == 1 + meta.TIMEOUT_RETRIES  # the request went out: it may be billed


def test_a_read_timeout_is_retried_once_only(http):
    http.script = [requests.exceptions.ReadTimeout("slow"), FakeResp(200, OK)]
    data, _ = meta._post("/images/edits", {}, meta.READ_S["image"])
    assert data == OK and len(http.calls) == 2

    http.calls.clear()
    http.script = [requests.exceptions.ReadTimeout("slow"), requests.exceptions.ReadTimeout("slow"), FakeResp(200, OK)]
    with pytest.raises(meta.ApiError, match="no answer within 300 s"):
        meta._post("/images/edits", {}, meta.READ_S["image"])
    assert len(http.calls) == 2  # a timed-out image call may be billed: never a third try


def test_a_read_timeout_while_the_body_streams_counts_as_a_timeout(http):
    import urllib3

    stalled = requests.exceptions.ConnectionError(urllib3.exceptions.ReadTimeoutError(None, "/", "read timed out"))
    http.script = [stalled, stalled, FakeResp(200, OK)]
    with pytest.raises(meta.ApiError, match="no answer within"):
        meta._post("/images/edits", {}, 300)
    assert len(http.calls) == 2


def test_other_request_errors_become_api_error(http):
    http.script = [requests.exceptions.InvalidURL("bad")]
    with pytest.raises(meta.ApiError, match="request failed"):
        meta._post("/chat/completions", {}, 180)


def test_server_errors_are_retried_and_the_message_stays_short(http):
    http.script = [FakeResp(500, text="x" * 5000)] * meta.ATTEMPTS
    with pytest.raises(meta.ApiError) as e:
        meta._post("/chat/completions", {}, 180)
    assert e.value.status == 500 and len(http.calls) == meta.ATTEMPTS
    assert len(str(e.value)) <= meta.BODY_CHARS + 20
    # waits grow (jittered exponential backoff)
    assert http.sleeps[0] < http.sleeps[-1] <= meta.BACKOFF_CAP_S


def test_429_waits_at_least_what_retry_after_asks(http):
    http.script = [FakeResp(429, text="slow down", headers={"Retry-After": "7"}), FakeResp(200, OK)]
    data, _ = meta._post("/chat/completions", {}, 180)
    assert data == OK and http.sleeps and http.sleeps[0] >= 7


def test_a_very_long_retry_after_ends_the_call_without_waiting(http):
    http.script = [FakeResp(429, text="later", headers={"Retry-After": "3600"})]
    with pytest.raises(meta.ApiError) as e:
        meta._post("/chat/completions", {}, 180)
    assert e.value.status == 429 and not http.sleeps


def test_an_answer_that_is_not_json_is_an_api_error(http):
    http.script = [FakeResp(200, None, text="<html>gateway</html>")]
    with pytest.raises(meta.ApiError, match="not JSON"):
        meta._post("/chat/completions", {}, 180)


def test_retry_after_forms():
    assert meta._retry_after({"retry-after": "7"}) == 7.0
    assert meta._retry_after({"retry-after-ms": "1500"}) == 1.5
    later = email.utils.formatdate(time.time() + 30, usegmt=True)
    assert 25 <= meta._retry_after({"retry-after": later}) <= 31
    assert meta._retry_after({"retry-after": "soon"}) is None
    assert meta._retry_after(None) is None


# ---------------------------------------------------------------- meta: Muse Spark answers


def _choice(content, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


def test_spark_json_sends_the_same_request_as_before(http, tmp_path):
    http.script = [FakeResp(200, _choice('{"x": 1}'))]
    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    img = _png()
    out = meta.spark_json(meta.Ledger(tmp_path / "l.jsonl"), "t", ["hello", img], schema, "thing")
    assert out == {"x": 1}
    assert http.calls[0]["body"] == {
        "model": meta.SPARK,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img).decode()}}]}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "thing", "schema": schema}},
    }


@pytest.mark.parametrize("bad", [_choice('{"x": 1', "length"), _choice("not json"), _choice("[1, 2]"),
                                 {"choices": []}])
def test_spark_json_asks_once_more_for_an_unusable_answer(http, tmp_path, bad):
    ledger = meta.Ledger(tmp_path / "l.jsonl")
    http.script = [FakeResp(200, bad), FakeResp(200, _choice('{"x": 2}'))]
    assert meta.spark_json(ledger, "t", ["q"], {}, "thing") == {"x": 2}
    rows = ledger.rows()
    assert len(rows) == 2 and "retry" not in rows[0] and rows[1]["retry"] == 1

    http.script = [FakeResp(200, bad), FakeResp(200, bad)]
    with pytest.raises(meta.ApiError, match="Muse Spark"):
        meta.spark_json(ledger, "t", ["q"], {}, "thing")


def test_spark_json_reads_picks_through_the_normalizer(http, tmp_path):
    answer = {"picks": [{"slot": "s1", "pick": "2", "alternates": ["3", "x", 4.0], "reason": "ok"}], "set_comment": "c"}
    http.script = [FakeResp(200, _choice(json.dumps(answer)))]
    out = meta.spark_json(meta.Ledger(tmp_path / "l.jsonl"), "pick:spark", ["q"], {}, "product_picks")
    assert out["picks"][0]["pick"] == 2 and out["picks"][0]["alternates"] == [3, 4]


# ---------------------------------------------------------------- meta: Muse Image answers


def test_bytes_that_are_not_an_image_are_an_api_error(http, tmp_path):
    ledger = meta.Ledger(tmp_path / "l.jsonl")
    bad = {"data": [{"b64_json": base64.b64encode(b"<html>oops</html>").decode()}]}
    assert meta._decode_images(bad["data"]) == ([], 1)
    cut = {"b64_json": base64.b64encode(_png(256, 256)[:200]).decode()}  # a PNG header, then nothing
    assert meta._decode_images([cut]) == ([], 1)
    http.script = [FakeResp(200, bad)]
    with pytest.raises(meta.ApiError, match="not an image"):
        meta.image_edit(ledger, "t", "p", [_png()], n=1)
    row = ledger.rows()[-1]
    assert row["unusable"] == 1 and row["usd"] == meta.USD_IMAGE  # a returned image is billed even if unusable


def test_image_edit_takes_at_most_ten_inputs(http, tmp_path):
    with pytest.raises(meta.ApiError, match="1 to 10"):
        meta.image_edit(meta.Ledger(tmp_path / "l.jsonl"), "t", "p", [_png()] * 11)
    assert not http.calls


def test_image_edit_body_is_unchanged(http, tmp_path):
    good = {"data": [{"b64_json": base64.b64encode(_png()).decode()}]}
    http.script = [FakeResp(200, good)]
    img = _png(seed=1)
    out = meta.image_edit(meta.Ledger(tmp_path / "l.jsonl"), "t", "prompt", [img], n=2, size="1792x1344",
                          tools=render.NO_TOOLS)
    assert len(out) == 1
    assert http.calls[0]["body"] == {
        "model": meta.IMAGE, "prompt": "prompt", "images": [{"image_url": meta.data_url(img)}], "n": 2,
        "output_format": "png", "reasoning_strength": "high", "size": "1792x1344", "tool_enablement": render.NO_TOOLS}


def test_size_fallback_only_when_the_size_is_refused(monkeypatch, tmp_path):
    calls = []

    def edit(ledger, tag, prompt, images, n=1, size=None, **kw):
        calls.append(size)
        if size and status:
            raise meta.ApiError(status, "no")
        return [b"img"]

    monkeypatch.setattr(meta, "image_edit", edit)
    status = 503
    with pytest.raises(meta.ApiError):
        render._edit_sized(None, "t", "p", [b""], 1, "1024x768")
    assert calls == ["1024x768"]  # an outage is not paid for twice
    calls.clear()
    status = 400
    assert render._edit_sized(None, "t", "p", [b""], 1, "1024x768") == [b"img"]
    assert calls == ["1024x768", None]


# ---------------------------------------------------------------- meta: SAM 3.1


class FakeSamClient:
    made: list = []
    error: BaseException | None = None

    def __init__(self, **kw):
        self.kw, self.closed = kw, False
        FakeSamClient.made.append(self)
        self.responses = SimpleNamespace(create=self._create)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    async def _create(self, **kw):
        if FakeSamClient.error is not None:
            raise FakeSamClient.error
        return SimpleNamespace()


@pytest.fixture()
def sam_client(monkeypatch):
    FakeSamClient.made, FakeSamClient.error = [], None
    sleeps = []
    monkeypatch.setattr(openai, "AsyncOpenAI", FakeSamClient)
    monkeypatch.setattr(meta, "_key", lambda: "test-key")
    monkeypatch.setattr(meta, "_sleep", sleeps.append)
    return SimpleNamespace(cls=FakeSamClient, sleeps=sleeps)


REQ = httpx.Request("POST", "https://api.meta.ai/v1/responses")


@pytest.mark.parametrize("exc, status, tries", [
    (openai.APIConnectionError(request=REQ), 0, meta.ATTEMPTS),
    (openai.APITimeoutError(request=REQ), 0, 1 + meta.TIMEOUT_RETRIES),
    (openai.APIStatusError("bad", response=httpx.Response(400, request=REQ), body=None), 400, 1),
    (openai.APIStatusError("busy", response=httpx.Response(503, request=REQ), body=None), 503, meta.ATTEMPTS),
])
def test_sam_errors_become_api_error_and_the_client_is_closed(sam_client, tmp_path, exc, status, tries):
    sam_client.cls.error = exc
    with pytest.raises(meta.ApiError) as e:
        meta.sam_masks(meta.Ledger(tmp_path / "l.jsonl"), "t", _png(), "sofa", 64, 48)
    assert e.value.status == status
    assert len(sam_client.cls.made) == tries and all(c.closed for c in sam_client.cls.made)
    assert all(c.kw["base_url"] == meta.BASE_URL and c.kw["max_retries"] == 0 for c in sam_client.cls.made)


def test_a_sam_stream_parser_error_becomes_api_error(sam_client, monkeypatch, tmp_path):
    import meta_sam_parser

    def broken(stream, fmt):
        raise meta_sam_parser.ResponsesStreamParserError("bad frame")

    monkeypatch.setattr(meta_sam_parser, "parse_responses_stream", broken)
    with pytest.raises(meta.ApiError, match="could not be read"):
        meta.sam_masks(meta.Ledger(tmp_path / "l.jsonl"), "t", _png(), "sofa", 64, 48)
    assert sam_client.cls.made and all(c.closed for c in sam_client.cls.made)


def test_scan_stops_when_sam_answers_nothing(monkeypatch, tmp_path):
    def down(*a, **k):
        raise meta.ApiError(503, "down")

    monkeypatch.setattr(meta, "sam_masks", down)
    room = np.full((48, 64, 3), 120, np.uint8)
    objs = [{"id": "o1", "name": "sofa", "box": [100, 100, 500, 500], "sam_phrase": "sofa"},
            {"id": "o2", "name": "lamp", "box": [600, 100, 900, 900], "sam_phrase": "lamp"}]
    with pytest.raises(meta.ApiError, match="SAM 3.1 did not answer"):
        scan.refine_with_sam(meta.Ledger(tmp_path / "l.jsonl"), room, objs)


def test_leftover_does_not_read_a_failed_check_as_cleared(monkeypatch):
    def down(*a, **k):
        raise meta.ApiError(0, "down")

    monkeypatch.setattr(meta, "sam_masks", down)
    img = np.full((48, 64, 3), 120, np.uint8)
    o = {"id": "o1", "name": "box", "sam_phrase": "box", "_mask": np.ones((48, 64), bool)}
    with pytest.raises(meta.ApiError):
        render.leftover(None, img, [o], {"o1": np.ones((48, 64), bool)}, "clear:check")


# ---------------------------------------------------------------- normalizers


def _recorded_reading(run: dict) -> dict:
    """Spark's room reading as the recorded run received it (boxes stand in for the 0 to 1000 grid ones)."""
    sc = run["scan"]
    return {
        "photo_check": sc["photo_check"], "room_type": sc["room_type"], "summary": sc["summary"],
        "dimensions_cm": sc["dimensions_cm"], "light": sc["light"],
        "fixed_elements": [{"name": f["name"], "box": list(f["box"])} for f in sc["fixed"]],
        "objects": [{"id": o["id"], "name": o["name"], "category": o["category"], "box": list(o["spark_box"]),
                     "sam_phrase": o["sam_phrase"], "decision": o["class"], "reason": o["reason"]}
                    for o in sc["objects"]],
        "plan": {**{k: v for k, v in sc["plan"].items() if k != "slots"},
                 "slots": [{k: v for k, v in s.items() if k not in ("id", "target_px")} for s in sc["plan"]["slots"]]},
    }


@pytest.mark.parametrize("rid", DEMO_IDS)
def test_normalizers_leave_the_recorded_answers_unchanged(rid):
    run = _run_json(rid)
    reading = _recorded_reading(run)
    assert scan.normalize_reading(copy.deepcopy(reading), len(reading["plan"]["slots"])) == reading
    n = len(run["result"]["products"])
    for c in run["render"]["candidates"]:
        if "products" in c["judge"]:
            assert verify.normalize_judgement(copy.deepcopy(c["judge"]), n) == c["judge"]
    final = run["result"].get("final_check_products")
    if final is not None:
        assert verify.normalize_judgement({"products": copy.deepcopy(final)}, n)["products"] == final
    for f in run.get("fixes", []):
        if f.get("kind") != "clear":
            assert verify.normalize_product(copy.deepcopy(f["after"])) == f["after"]
            assert verify.normalize_product(copy.deepcopy(f["before"])) == f["before"]
    # the pick answer, rebuilt from the slots: numbers are 1-based places in each short list
    answer = {"picks": [{"slot": s["id"], "pick": s["pick_number"],
                         "alternates": [s["shortlist"].index(a) + 1 for a in s["alternates"]],
                         "reason": s["reason"]} for s in run["plan"]["slots"]],
              "set_comment": run["plan"]["set_comment"]}
    assert meta.normalize_picks(copy.deepcopy(answer)) == answer


def test_normalize_reading_repairs_and_bounds_a_loose_answer():
    obj = {"id": "o1", "name": "box", "category": "clutter", "box": ["10", 20, 30.5, "40"], "sam_phrase": "box",
           "decision": "toss", "reason": "r"}
    slot = {"slot": "rug", "kind": "rug", "action": "add", "replaces": "", "target_box": [0, 600, 500, 1000],
            "size_cm": {"w": "160", "d": "230 cm", "h": None}, "look": "jute rug", "color_family": "beige",
            "placement": "floor", "why": "warmth"}
    data = {
        "photo_check": {"is_room_interior": "true", "people_visible": "unclear", "problem": None},
        "room_type": "bedroom", "summary": "s",
        "dimensions_cm": {"width": "350", "depth": 300, "ceiling": "tall", "confidence": "sure", "basis": "door"},
        "light": None,
        "fixed_elements": [{"name": "wall", "box": [0, 0, 1000]}, {"name": "floor", "box": [0, 500, 1000, 1000]}],
        "objects": [obj] + [dict(obj, id=f"x{i}") for i in range(60)] + [dict(obj, id="o1")],
        "plan": {"title": "t", "style": "s", "palette": ["sand", 3], "idea": "i",
                 "slots": [slot, dict(slot, kind="spaceship"), dict(slot, target_box=None), dict(slot, kind="Floor lamp"),
                           slot, slot]},
    }
    out = scan.normalize_reading(data, n_slots=3)
    assert out["photo_check"] == {"is_room_interior": True, "people_visible": None, "problem": ""}
    assert out["dimensions_cm"]["width"] == 350 and out["dimensions_cm"]["ceiling"] == 0
    assert out["dimensions_cm"]["confidence"] == "low"
    assert out["light"] == {"direction": "", "color_temperature": "", "quality": ""}
    assert [f["name"] for f in out["fixed_elements"]] == ["floor"]
    assert len(out["objects"]) == scan.MAX_OBJECTS and len({o["id"] for o in out["objects"]}) == scan.MAX_OBJECTS
    assert out["objects"][0]["box"] == [10, 20, 30.5, 40] and out["objects"][0]["decision"] == "keep"
    assert out["plan"]["palette"] == ["sand", "3"]
    slots = out["plan"]["slots"]
    assert [s["kind"] for s in slots] == ["rug", "floor_lamp", "rug"]
    assert slots[0]["size_cm"] == {"w": 160, "d": 230, "h": 0}


def test_normalize_judgement_keeps_only_usable_verdicts():
    good = {"index": 1, "verdict": "match", "score": 9, "issues": [], "fix": ""}
    j = {"products": [
        good,
        {"index": "2", "verdict": "Partial", "score": "6.5", "issues": "colour", "scale_ok": "false"},
        {"index": 2, "verdict": "match", "score": 9},     # second verdict for product 2: dropped
        {"index": 7, "verdict": "match", "score": 9},     # no product 7: dropped
        {"index": 3, "verdict": "great", "score": 9},     # not a verdict: dropped
        {"index": 3, "verdict": "mismatch", "score": 14},
        "junk",
    ], "scene": {"overall": "7.5", "photoreal": -2, "cohesion": 8, "renter_rule_ok": "false", "artifacts": "seam"}}
    out = verify.normalize_judgement(j, 3)
    assert out["products"][0] == good
    assert out["products"][1] == {"index": 2, "verdict": "partial", "score": 6.5, "issues": ["colour"], "scale_ok": False}
    assert out["products"][2] == {"index": 3, "verdict": "mismatch", "score": 10}
    assert len(out["products"]) == 3
    assert out["scene"] == {"overall": 7.5, "photoreal": 0, "cohesion": 8, "renter_rule_ok": False,
                            "artifacts": ["seam"]}
    assert verify.normalize_product({"verdict": "match"}) is None  # no score
    assert verify.candidate_score(out, 3) > 0


# ---------------------------------------------------------------- prompts and stored text


def test_prompt_builders_produce_the_text_that_was_sent_before():
    p = SimpleNamespace(title="Oak Side Table", description="A round oak table on three legs.", kind="side_table")
    issues, fix = ["legs too thin", "top too dark", "wrong shape", "fourth"], "Make the legs thicker."
    issue_text = "; ".join(issues[:3])
    before = (
        f"Image 1 is a crop from a real room photo. Image 2 is the reference photo of the product: {p.title}, "
        f"{p.description} Redraw only the {p.kind.replace('_', ' ')} in image 1 so it is exactly this "
        f"product: same shape, proportions, color, material, texture, pattern and details. Fix these differences: "
        f"{issue_text}. {fix} Keep its position, size and perspective. Keep everything else in image 1 exactly as it "
        f"is, with the same light, shadows and photo grain. No text, no outlines.")
    assert render.redraw_prompt(p, issues, fix) == before
    assert "small differences from the reference" in render.redraw_prompt(p, [], "")
    names = "; ".join(["moving box", "rolled mat"])
    before = (
        f"Image 1 is a crop from a real room photo. Remove these completely: {names}. Where they were, show the "
        f"empty floor, skirting board and wall that were hidden behind them, continuing the same floorboards, wall "
        f"color, light and photo grain. Keep every other object exactly where and as it is. Do not add anything "
        f"new. No text, no outlines.")
    assert render.clear_prompt(["moving box", "rolled mat"]) == before


def test_the_render_prompt_of_the_living_run_is_rebuilt_exactly():
    from realroom.catalog import Catalog

    run = _run_json("living")
    cat = Catalog(CATALOG_INDEX)
    slots = run["plan"]["slots"]
    products = [cat.products[s["pick"]] for s in slots]
    prompt = render.edit_prompt(run["scan"], slots, products, 2)
    assert pipeline.tidy(prompt) == run["render"]["prompt"]


def test_tidy_keeps_prompts_and_technical_text_exact():
    em = chr(0x2014)
    raw = f"Place it 80 - 120 cm away {em} by the wall"
    data = {"render": {"prompt": raw}, "trace": raw, "build": {"prompts": {"x": raw}}, "note": raw}
    out = pipeline.tidy(data)
    assert out["render"]["prompt"] == raw and out["trace"] == raw and out["build"]["prompts"]["x"] == raw
    assert out["note"] == "Place it 80 to 120 cm away, by the wall"


def test_build_info_hashes_every_prompt(monkeypatch):
    monkeypatch.setenv("REALROOM_VERSION", "v-test")
    b = pipeline.build_info()
    assert b["version"] == "v-test"
    assert set(b["prompts"]) == {"scan", "scan_refresh", "scan_furnish", "pick", "judge", "judge_one", "render",
                                 "redraw", "clear"}
    assert all(re.fullmatch(r"[0-9a-f]{12}", h) for h in [*b["prompts"].values(), *b["schemas"].values()])


# ---------------------------------------------------------------- match count


def _v(verdict, score=None, **kw):
    return {"verdict": verdict, **({"score": score} if score is not None else {}), **kw}


def test_a_match_needs_both_looks_when_the_final_check_ran():
    own = [_v("match", 9), _v("match", 9, fixed=True), _v("partial", 6), _v("partial", 5)]
    assert pipeline.match_count(own, None) == (2, [])
    final = [{"index": 1, **_v("match", 9)}, {"index": 2, **_v("partial", 6)}, {"index": 3, **_v("match", 9)},
             {"index": 4, **_v("mismatch", 2)}]
    assert pipeline.match_count(own, final) == (1, [1, 2])
    # a product the final check did not judge keeps its own verdict
    assert pipeline.match_count(own, final[1:]) == (1, [1, 2])
    # "not verified" never counts, and is not a disagreement
    assert pipeline.match_count([pipeline.unverified(1)], [{"index": 1, **_v("match", 9)}]) == (0, [])


def test_the_studio_run_counts_two_matches_and_names_the_rug():
    run = _run_json("studio")
    own = [p["verdict"] for p in run["result"]["products"]]
    final = run["result"]["final_check_products"]
    assert pipeline.match_count(own, final) == (2, [2])
    titles = [SimpleNamespace(title=run["products"][p["product"]]["title"]) for p in run["result"]["products"]]
    text = pipeline._dispute_sentence(titles, own, final, [2])
    assert text.startswith("Spark's two checks disagree on the ") and "match 9" in text and "partial 6" in text
    assert text.endswith("so it is not counted as a match.")
    assert chr(0x2013) not in text and chr(0x2014) not in text


# ---------------------------------------------------------------- files: provenance and blurring


@pytest.mark.parametrize("name", ["after.png", "after.jpg"])
def test_xmp_is_written_without_changing_pixels(tmp_path, name):
    rng = np.random.default_rng(3)
    arr = rng.integers(0, 255, (60, 80, 3), dtype=np.uint8)
    path = tmp_path / name
    imgutil.save_png(arr, path) if name.endswith(".png") else imgutil.save(arr, path, quality=93)
    before = np.asarray(Image.open(path)).copy()
    assert imgutil.add_xmp(path)
    with Image.open(path) as im:
        after = np.asarray(im).copy()
        meta_text = im.info.get("XML:com.adobe.xmp") or im.info.get("xmp") or b""
    assert np.array_equal(before, after)
    if name.endswith(".png"):
        assert np.array_equal(after, arr)  # still lossless
    text = meta_text.encode() if isinstance(meta_text, str) else meta_text
    assert b"compositeWithTrainedAlgorithmicMedia" in text and b"Iptc4xmpExt:DigitalSourceType" in text
    size = path.stat().st_size
    assert imgutil.add_xmp(path) and path.stat().st_size == size  # written once


def test_blur_file_keeps_the_name_and_format_and_hides_the_photo(tmp_path):
    rng = np.random.default_rng(4)
    arr = rng.integers(0, 255, (600, 800, 3), dtype=np.uint8)
    for name in ("before.jpg", "before.png"):
        p = tmp_path / name
        imgutil.save(arr, p)
        imgutil.blur_file(p)
        with Image.open(p) as im:
            assert im.format == ("JPEG" if name.endswith(".jpg") else "PNG")
            assert max(im.size) <= 48
            small = np.asarray(im.convert("RGB")).astype(float)
        assert small.std() < arr.std() / 4  # a blurred smear, not the photo
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not an image")
    imgutil.blur_file(bad)
    assert Image.open(bad).format == "JPEG"


# ---------------------------------------------------------------- end to end with fake models

RUG, LAMP = (200, 40, 40), (40, 40, 200)
H, W = 360, 480


def _room() -> np.ndarray:
    import cv2

    yy, xx = np.mgrid[0:H, 0:W]
    base = np.stack([100 + 40 * xx / W, 110 + 30 * yy / H, 120 + 20 * (xx + yy) / (W + H)], axis=2)
    noise = cv2.GaussianBlur(np.random.default_rng(0).normal(0, 8, (H, W, 3)).astype(np.float32), (0, 0), 2)
    room = np.clip(base + noise, 0, 255).astype(np.uint8)
    room[150:300, 40:200] = (90, 80, 70)
    return room


READING = {
    "photo_check": {"is_room_interior": True, "people_visible": False, "problem": ""},
    "room_type": "living room", "summary": "A test room.",
    "dimensions_cm": {"width": 400, "depth": 350, "ceiling": 250, "confidence": "medium", "basis": "door"},
    "light": {"direction": "left", "color_temperature": "warm", "quality": "soft"},
    "fixed_elements": [{"name": "floor", "box": [0, 600, 1000, 1000]}],
    "objects": [{"id": "o1", "name": "sofa", "category": "furniture", "box": [83, 417, 417, 833], "sam_phrase": "sofa",
                 "decision": "keep", "reason": "it stays"}],
    "plan": {"title": "Test plan", "style": "modern", "palette": ["grey"], "idea": "Add a rug and a lamp.",
             "slots": [
                 {"slot": "rug", "kind": "rug", "action": "add", "replaces": "", "target_box": [458, 700, 875, 950],
                  "size_cm": {"w": 180, "d": 120, "h": 1}, "look": "grey wool rug", "color_family": "grey",
                  "placement": "on the floor", "why": "warmth"},
                 {"slot": "lamp", "kind": "floor_lamp", "action": "add", "replaces": "",
                  "target_box": [900, 100, 980, 700], "size_cm": {"w": 35, "d": 35, "h": 155},
                  "look": "black metal floor lamp", "color_family": "black", "placement": "in the corner",
                  "why": "light"}]},
}
PRODUCT_VERDICTS = [{"index": 1, "verdict": "match", "score": 9, "shape": "", "color": "", "material_pattern": "",
                     "scale_ok": True, "placement_ok": True, "issues": [], "fix": ""},
                    {"index": 2, "verdict": "partial", "score": 6, "shape": "", "color": "", "material_pattern": "",
                     "scale_ok": True, "placement_ok": True, "issues": ["the shade is too small"],
                     "fix": "Make the shade bigger."}]
SCENE = {"photoreal": 8, "cohesion": 8, "renter_rule_ok": True, "removals_ok": True, "artifacts": [], "overall": 8,
         "comment": "Fine."}


@pytest.fixture(scope="module")
def tiny_catalog(tmp_path_factory):
    """The catalog rows of the product photos that ship with the demo runs (no embeddings: text ranking)."""
    from realroom.catalog import Catalog

    ids = {p.stem for p in (PHOTOS / "img").glob("*.jpg")}
    root = tmp_path_factory.mktemp("catalog")
    rows = [line for line in (CATALOG_INDEX / "catalog.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line)["id"] in ids]
    (root / "catalog.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return Catalog(root, photos=PHOTOS)


def _patch_models(monkeypatch, reading=None, judge=None, sam_fails=lambda tag: False, edit_fails=lambda tag: False,
                  one=None):
    room = _room()
    seen = SimpleNamespace(spark=[], sam=[], edit=[], room=room)

    def spark_json(ledger, tag, parts, schema, schema_name, system=None, model=meta.SPARK, extra=None):
        seen.spark.append(tag)
        ledger.add(kind="spark", tag=tag, model=model, sec=0.0, in_tok=1000, out_tok=100, usd=0.002)
        if schema_name == "room_reading":
            return copy.deepcopy(reading or READING)
        if schema_name == "product_picks":
            return {"picks": [{"slot": "s1", "pick": 1, "alternates": [2, 3], "reason": "fits"},
                              {"slot": "s2", "pick": 1, "alternates": [2], "reason": "fits"}], "set_comment": "Calm."}
        if schema_name == "render_judgement":
            if judge == "fail":
                raise meta.ApiError(503, "busy")
            return {"products": copy.deepcopy(PRODUCT_VERDICTS), "scene": dict(SCENE)}
        return {"product": copy.deepcopy(one or PRODUCT_VERDICTS[1])}

    def sam_masks(ledger, tag, image, text, w, h):
        seen.sam.append(tag)
        if sam_fails(tag):
            raise meta.ApiError(503, "injected SAM failure")
        ledger.add(kind="sam", tag=tag, sec=0.0, n_masks=0, usd=meta.USD_SAM_IMAGE)
        out = {}
        if text.startswith("<0f>"):
            for oid, x1, y1, x2, y2 in re.findall(r"(\w+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);", text):
                m = np.zeros((h, w), bool)
                m[int(y1):int(y2) + 1, int(x1):int(x2) + 1] = True
                out[oid] = {"mask": m, "conf": 0.9, "box": (int(x1), int(y1), int(x2) + 1, int(y2) + 1),
                            "area": int(m.sum())}
            return out
        arr = imgutil.load_rgb(image).astype(int)
        for i, c in enumerate((RUG, LAMP)):
            m = np.all(np.abs(arr - c) < 12, axis=2)
            if m.sum() > 50:
                ys, xs = np.nonzero(m)
                out[str(i + 1)] = {"mask": m, "conf": 0.9, "box": (int(xs.min()), int(ys.min()), int(xs.max()) + 1,
                                                                     int(ys.max()) + 1), "area": int(m.sum())}
        return out

    def image_edit(ledger, tag, prompt, images, n=1, size=None, reasoning="high", output_format="png", tools=None):
        seen.edit.append(tag)
        if edit_fails(tag):
            raise meta.ApiError(503, "busy")
        ledger.add(kind="image_edit", tag=tag, sec=0.0, n=n, inputs=len(images), reasoning=reasoning,
                   usd=meta.USD_IMAGE * n, usage=None)
        if tag.endswith(":redraw"):
            return [images[0]] * n
        out = room.copy()
        for s, color in zip((reading or READING)["plan"]["slots"], (RUG, LAMP)):
            x1, y1, x2, y2 = imgutil.norm_to_px(s["target_box"], W, H)
            mx, my = (x2 - x1) // 8, (y2 - y1) // 8
            out[y1 + my:y2 - my, x1 + mx:x2 - mx] = color
        return [imgutil.png_bytes(out)] * n

    monkeypatch.setattr(meta, "spark_json", spark_json)
    monkeypatch.setattr(meta, "sam_masks", sam_masks)
    monkeypatch.setattr(meta, "image_edit", image_edit)
    return seen


def _go(tiny_catalog, tmp_path, seen, **kw):
    args = dict(mode="refresh", n_slots=2, n_candidates=1, max_fixes=1, fix_rounds=1, fix_tries=1)
    args.update(kw)
    return pipeline.run_pipeline(seen.room, tiny_catalog, tmp_path / "run", "r-test", "a calm room", **args)


def _saved(tmp_path) -> dict:
    return json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))


def test_a_sam_failure_in_the_fix_stage_is_skipped_and_the_run_finishes(monkeypatch, tmp_path, tiny_catalog, capsys):
    seen = _patch_models(monkeypatch, sam_fails=lambda tag: tag.startswith("fix:"))
    data = _go(tiny_catalog, tmp_path, seen)
    run = _saved(tmp_path)
    assert run["status"] == "done" and "error" not in run and "stopped_at" not in run
    skipped = [e for e in run["events"] if e["title"].startswith("Skipped redrawing")]
    assert len(skipped) == 1 and skipped[0]["detail"].startswith("The Meta Model API did not answer")
    assert any(t.startswith("fix:") for t in seen.sam) and "ApiError" in data["trace"]
    lamp = run["result"]["products"][1]
    assert lamp["verdict"]["verdict"] == "partial" and not lamp["fixed"]
    assert run["fixes"] == []
    assert run["events"][-1]["title"] == "1 of 2 products match their catalog photos"
    # provenance in both result files, pixels of the lossless file unchanged
    assert run["result"]["provenance"]["files"] == ["after.jpg", "after.png"]
    for name in ("after.jpg", "after.png"):
        assert imgutil.has_xmp((tmp_path / "run" / name).read_bytes())
    assert set(run["build"]["prompts"]) >= {"scan", "pick", "render", "judge"}
    out = capsys.readouterr()
    lines = [json.loads(x) for x in out.out.splitlines() if x.startswith('{"run"')]
    assert [x["stage"] for x in lines] == ["scan", "search", "pick", "render", "verify", "fix", "result"]
    assert all(set(x) == {"run", "stage", "sec", "usd"} and x["run"] == "r-test" for x in lines)
    assert "realroom run r-test: fix p2" in out.err


def test_a_person_in_the_photo_stops_before_sam_and_blurs_the_photo(monkeypatch, tmp_path, tiny_catalog):
    reading = copy.deepcopy(READING)
    reading["photo_check"]["people_visible"] = True
    seen = _patch_models(monkeypatch, reading=reading)
    _go(tiny_catalog, tmp_path, seen)
    run = _saved(tmp_path)
    assert run["status"] == "error" and run["stopped_at"] == "scan"
    assert run["error"] == pipeline.PERSON_SENTENCE
    assert seen.sam == [] and seen.edit == []
    with Image.open(tmp_path / "run" / "before.jpg") as im:
        assert max(im.size) <= 48


def test_when_every_judge_call_fails_the_products_are_unverified(monkeypatch, tmp_path, tiny_catalog):
    seen = _patch_models(monkeypatch, judge="fail")
    _go(tiny_catalog, tmp_path, seen)
    run = _saved(tmp_path)
    assert run["status"] == "done"
    assert [p["verdict"]["verdict"] for p in run["result"]["products"]] == ["unverified", "unverified"]
    assert run["events"][-1]["title"] == "The products could not be checked against their catalog photos"
    assert "0 of" not in json.dumps(run["events"])
    assert not any(t.startswith("fix:") for t in seen.edit)  # nothing to fix without a verdict


def test_past_the_time_limit_the_fixes_are_skipped(monkeypatch, tmp_path, tiny_catalog):
    seen = _patch_models(monkeypatch)
    _go(tiny_catalog, tmp_path, seen, deadline_s=0)
    run = _saved(tmp_path)
    assert run["status"] == "done" and run["limit_reached"] == "time"
    assert any(e["title"] == "Skipped the remaining fixes" for e in run["events"])
    assert not any(t.startswith("fix:") for t in seen.edit + seen.sam)


def test_an_api_failure_stops_the_run_with_a_plain_sentence(monkeypatch, tmp_path, tiny_catalog, capsys):
    seen = _patch_models(monkeypatch, edit_fails=lambda tag: tag == "render")
    _go(tiny_catalog, tmp_path, seen)
    run = _saved(tmp_path)
    assert run["status"] == "error" and run["stopped_at"] == "render"
    assert run["error"] == pipeline.API_SENTENCE
    assert run["events"][-1] == {**run["events"][-1], "stage": "error", "detail": pipeline.API_SENTENCE}
    assert "HTTP 503" in run["trace"]
    assert "realroom run r-test: render: ApiError" in capsys.readouterr().err


def test_a_redraw_the_final_check_disputes_is_not_counted_as_a_match(monkeypatch, tmp_path, tiny_catalog):
    seen = _patch_models(monkeypatch, one={**PRODUCT_VERDICTS[1], "verdict": "match", "score": 9, "issues": []})
    _go(tiny_catalog, tmp_path, seen)
    run = _saved(tmp_path)
    assert run["status"] == "done"
    lamp = run["result"]["products"][1]
    assert lamp["fixed"] and lamp["verdict"]["verdict"] == "match"  # the verdict objects are left as they were
    assert [p["verdict"] for p in run["result"]["final_check_products"]] == ["match", "partial"]
    last = run["events"][-1]
    assert last["title"] == "1 of 2 products match their catalog photos"
    title = run["products"][lamp["product"]]["title"]
    assert last["detail"].startswith(f"Spark's two checks disagree on the {title} (match 9 after its redraw, "
                                     "partial 6 in the final check), so it is not counted as a match.")


def test_a_failure_while_clearing_clutter_is_skipped(monkeypatch, tmp_path, tiny_catalog):
    reading = copy.deepcopy(READING)
    reading["objects"].append({"id": "o2", "name": "moving box", "category": "clutter", "box": [20, 750, 150, 950],
                               "sam_phrase": "cardboard box", "decision": "remove", "reason": "clutter"})
    seen = _patch_models(monkeypatch, reading=reading, sam_fails=lambda tag: tag.startswith("clear:"))
    _go(tiny_catalog, tmp_path, seen)
    run = _saved(tmp_path)
    assert run["status"] == "done"
    skipped = [e for e in run["events"] if e["title"] == "Skipped clearing the clutter"]
    assert len(skipped) == 1 and "keeps the version it had" in skipped[0]["detail"]
    assert not any(f.get("kind") == "clear" for f in run["fixes"]) and any(t.startswith("clear:") for t in seen.sam)


@pytest.mark.parametrize("pc,sentence", [
    ({"is_room_interior": True, "people_visible": True}, "PERSON_SENTENCE"),
    ({"is_room_interior": False, "people_visible": False}, "NOT_ROOM_SENTENCE"),
    ({"is_room_interior": True}, "UNCHECKED_SENTENCE"),                              # no people_visible at all
    ({"is_room_interior": "unclear", "people_visible": False}, "UNCHECKED_SENTENCE"),  # unreadable room answer
    (None, "UNCHECKED_SENTENCE"),                                                    # no photo_check at all
])
def test_photo_check_fails_closed(pc, sentence):
    reading = scan.normalize_reading({"photo_check": pc} if pc is not None else {}, n_slots=3)
    with pytest.raises(pipeline.UserFacingError) as e:
        pipeline.photo_check(reading)
    assert str(e.value).startswith(getattr(pipeline, sentence)) and e.value.hide_photo


def test_photo_check_passes_a_clear_room():
    pipeline.photo_check(scan.normalize_reading(
        {"photo_check": {"is_room_interior": True, "people_visible": False, "problem": ""}}, n_slots=3))
