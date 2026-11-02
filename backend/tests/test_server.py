"""HTTP API against the recorded runs, plus the live-run gate with a fake launcher (no model calls)."""
from __future__ import annotations

import io
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from conftest import CATALOG_INDEX, DEMO_IDS, DEMO_RUNS, jpeg_bytes
from realroom.server import Limits, create_app


@pytest.fixture
def replay_client():
    app = create_app(DEMO_RUNS, CATALOG_INDEX, None, Limits(live=False), demos_file=DEMO_RUNS / "demos.json")
    return TestClient(app)


class FakeLauncher:
    def __init__(self, fail: Exception | None = None):
        self.calls: list[tuple] = []
        self.fail = fail

    def __call__(self, run_id, jpeg, brief, mode):
        if self.fail:
            raise self.fail
        self.calls.append((run_id, jpeg, brief, mode))


@pytest.fixture
def live(tmp_path):
    def make(launcher=None, **kw):
        launcher = launcher or FakeLauncher()
        limits = Limits(live=True, state_file=tmp_path / "_quota.json", **kw)
        app = create_app(tmp_path / "runs", CATALOG_INDEX, None, limits, launcher)
        return TestClient(app), launcher, limits
    return make


def post_photo(client, data=None, brief="A brighter room.", mode="refresh", headers=None, name="room.jpg"):
    data = jpeg_bytes() if data is None else data
    return client.post("/api/runs", files={"photo": (name, io.BytesIO(data), "image/jpeg")},
                       data={"brief": brief, "mode": mode}, headers=headers or {})


# ------------------------------------------------------------------ replay

def test_config_without_live(replay_client):
    c = replay_client.get("/api/config").json()
    assert c["live"] is False and c["runs_left_today"] is None
    assert c["catalog_size"] == 4375


def test_demos_listed_in_order(replay_client):
    demos = replay_client.get("/api/demos").json()
    assert [d["id"] for d in demos] == list(DEMO_IDS)
    for d in demos:
        assert d["before"] and d["after"] and 0 < d["untouched_pct"] < 100


def test_run_json_is_served_without_trace(replay_client):
    r = replay_client.get("/api/runs/living")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert "trace" not in body and body["status"] == "done"


@pytest.mark.parametrize("path", [
    "/api/runs/..%2Fdemos", "/api/runs/%2e%2e", "/api/runs/does-not-exist",
    "/runs/living/run.json", "/runs/living/ledger.jsonl", "/runs/living/masks.npz",
    "/runs/..%2Fliving/after.jpg", "/runs/living/..%2F..%2FREADME.md",
    "/catalog/img/../../README.md", "/catalog/thumb/B075QFCHM9.jpg", "/catalog/other/B075QFCHM9.jpg",
])
def test_files_outside_the_allowed_set_are_not_served(replay_client, path):
    assert replay_client.get(path).status_code == 404


def test_run_images_are_served_with_cache(replay_client):
    r = replay_client.get("/runs/living/after.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert "max-age" in r.headers.get("cache-control", "")


def test_security_headers(replay_client):
    r = replay_client.get("/api/config")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_live_off_refuses_uploads(replay_client):
    assert post_photo(replay_client).status_code == 503


# ------------------------------------------------------------------ live gate (fake launcher)

def test_live_run_starts_and_cleans_the_photo(live):
    from PIL import Image

    client, launcher, _ = live()
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    exif[0x8825] = {2: (37.0, 33.0, 0.0), 4: (126.0, 58.0, 0.0)}  # GPS latitude and longitude
    r = post_photo(client, jpeg_bytes(2400, 1800, exif=exif.tobytes()), brief="  A   calmer\n room  ")
    assert r.status_code == 200, r.text
    run_id, jpeg, brief, mode = launcher.calls[0]
    assert r.json() == {"id": run_id}
    assert brief == "A calmer room" and mode == "refresh"
    im = Image.open(io.BytesIO(jpeg))
    assert im.format == "JPEG" and max(im.size) == 1792
    assert not im.getexif(), "location and camera metadata must not reach the model or the runs volume"


def test_empty_brief_gets_a_default(live):
    client, launcher, _ = live()
    assert post_photo(client, brief="   ").status_code == 200
    assert launcher.calls[0][2]


@pytest.mark.parametrize("data,code", [
    (b"not an image at all", 400),
    (jpeg_bytes(500, 400), 400),     # too small
    (jpeg_bytes(3000, 900), 400),    # too wide
])
def test_bad_photos_are_refused_before_spending(live, data, code):
    client, launcher, limits = live()
    assert post_photo(client, data).status_code == code
    assert not launcher.calls
    assert limits.left_today() == 40


def test_unknown_mode(live):
    client, launcher, _ = live()
    assert post_photo(client, mode="destroy").status_code == 400
    assert not launcher.calls


def test_oversized_upload(live):
    client, launcher, _ = live(max_upload_mb=1)
    big = b"\xff\xd8\xff" + b"0" * (1024 * 1024 + 10)
    assert post_photo(client, big).status_code == 413
    assert not launcher.calls


def test_per_visitor_and_daily_caps(live):
    client, launcher, limits = live(per_day=4, per_client_per_day=3)
    for _ in range(3):
        assert post_photo(client).status_code == 200
    r = post_photo(client)
    assert r.status_code == 429 and "3 live runs" in r.json()["detail"]
    assert limits.left_today() == 1


def test_budget_cap(live):
    client, launcher, limits = live(budget_usd=1.0, est_run_usd=0.4, per_client_per_day=10)
    assert post_photo(client).status_code == 200
    assert post_photo(client).status_code == 200
    assert post_photo(client).status_code == 429  # a third reservation would pass $1.00
    limits.settle(0.1)  # the first run cost $0.10, not the $0.40 it reserved: $0.50 left, one more run
    assert limits.left_today() == 1


def test_failed_launch_is_refunded(live):
    client, launcher, limits = live(launcher=FakeLauncher(fail=RuntimeError("busy")))
    with pytest.raises(RuntimeError):
        post_photo(client)
    st = json.loads(limits.state_file.read_text())
    assert st["total"] == 0 and st["spent_usd"] == 0


def test_quota_survives_a_restart(live, tmp_path):
    client, _, limits = live(per_client_per_day=1)
    assert post_photo(client).status_code == 200
    again = Limits(live=True, per_client_per_day=1, state_file=limits.state_file)
    app = create_app(tmp_path / "runs", CATALOG_INDEX, None, again, FakeLauncher())
    assert post_photo(TestClient(app)).status_code == 429


# ------------------------------------------------------------------ retention

def test_purge_keeps_demos_and_busy_runs(tmp_path):
    import os
    import time

    from realroom.server import purge_expired

    runs = tmp_path / "runs"
    for rid, ai in (("living", True), ("u11020101aaaaaa", False), ("u11020102bbbbbb", False), ("u11020103cccccc", False)):
        d = runs / rid
        d.mkdir(parents=True)
        (d / "run.json").write_text(json.dumps({"room": {"ai_generated": ai}}))
        (d / "before.jpg").write_bytes(b"x")
        old = time.time() - 20 * 3600
        for f in d.iterdir():
            os.utime(f, (old, old))
    (runs / "_quota.json").write_text("{}")
    gone = purge_expired(runs, 18, keep={"u11020103cccccc"}, busy={"u11020102bbbbbb"})
    assert gone == ["u11020101aaaaaa"]
    assert (runs / "living").exists() and (runs / "_quota.json").exists()
    shutil.rmtree(runs)
