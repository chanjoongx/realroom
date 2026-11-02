"""HTTP API against the recorded runs, plus the live-run gate with a fake launcher (no model calls)."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import os
import re
import shutil
import struct
import time
import zlib

import pytest
from fastapi.testclient import TestClient

from conftest import CATALOG_INDEX, DEMO_IDS, DEMO_RUNS, jpeg_bytes
from realroom import server
from realroom.server import FILE_NAME, RUN_ID, Limits, create_app, sweep_interrupted


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
    """peer sets the connection's own address; by default it is TestClient's "testclient", which is no
    address at all, so those visitors fall back to X-Forwarded-For or share the "unknown" bucket."""
    def make(launcher=None, peer=None, **kw):
        launcher = launcher or FakeLauncher()
        limits = Limits(live=True, state_file=tmp_path / "_quota.json", **kw)
        app = create_app(tmp_path / "runs", CATALOG_INDEX, None, limits, launcher)
        return (TestClient(app, client=peer) if peer else TestClient(app)), launcher, limits
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
    client, launcher, limits = live(per_day=4, per_client_per_day=3, peer=("8.8.8.8", 50000))
    for _ in range(3):
        assert post_photo(client).status_code == 200
    r = post_photo(client)
    assert r.status_code == 429 and "3 live runs" in r.json()["detail"]
    assert limits.left_today() == 1


def test_visitors_without_an_address_get_only_the_shared_caps(live):
    client, _, limits = live(per_day=5, per_client_per_day=1)  # TestClient's peer is no address, no X-Forwarded-For
    codes = [post_photo(client).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    assert limits.left_today() == 0


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
    client, _, limits = live(per_client_per_day=1, peer=("8.8.8.8", 50000))
    assert post_photo(client).status_code == 200
    again = Limits(live=True, per_client_per_day=1, state_file=limits.state_file)
    app = create_app(tmp_path / "runs", CATALOG_INDEX, None, again, FakeLauncher())
    assert post_photo(TestClient(app, client=("8.8.8.8", 50001))).status_code == 429


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


# ------------------------------------------------------------------ visitors

def test_spoofed_forwarded_for_does_not_change_the_bucket(live):
    client, _, _ = live(peer=("8.8.8.8", 50000))
    codes = [post_photo(client, headers={"x-forwarded-for": f"9.9.9.{i}"}).status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]


def test_forwarded_for_counts_its_right_most_public_entry(live):
    client, _, _ = live(peer=("10.0.0.7", 50000))  # a private peer, as behind a proxy that appends
    codes = [post_photo(client, headers={"x-forwarded-for": f"1.1.1.{i}, 9.9.9.9, 10.0.0.1"}).status_code
             for i in range(4)]
    assert codes == [200, 200, 200, 429]


def test_ipv6_visitors_count_per_64(tmp_path):
    limits = Limits(live=True, state_file=tmp_path / "_quota.json")
    app = create_app(tmp_path / "runs", CATALOG_INDEX, None, limits, FakeLauncher())
    for _ in range(3):
        assert post_photo(TestClient(app, client=("2001:4860:4860::8888", 1))).status_code == 200
    assert post_photo(TestClient(app, client=("2001:4860:4860::8844", 1))).status_code == 429  # same /64
    assert post_photo(TestClient(app, client=("2001:4860:4861::1", 1))).status_code == 200    # another /64


def test_the_log_line_names_the_source_but_never_the_address(live, capsys):
    for peer, xff, source in ((("8.8.8.8", 1), "1.2.3.4", "peer"), (("10.0.0.7", 1), "8.8.4.4", "forwarded"),
                              (None, None, "unknown")):
        client, _, _ = live(peer=peer)
        post_photo(client, headers={"x-forwarded-for": xff} if xff else None)
        out = capsys.readouterr().out
        assert f"POST /api/runs client source {source}" in out
        assert not re.search(r"\d+\.\d+\.\d+\.\d+", out)


def test_visitors_are_stored_as_salted_hashes_that_change_each_day(live):
    client, _, limits = live(peer=("8.8.8.8", 50000))
    assert post_photo(client).status_code == 200
    text = limits.state_file.read_text()
    st = json.loads(text)
    assert "8.8.8.8" not in text and len(st["salt"]) == 32 and list(st["clients"].values()) == [1]
    st["day"] = "2000-01-01"  # as if the UTC day had turned over
    limits.state_file.write_text(json.dumps(st))
    assert post_photo(client).status_code == 200
    st2 = json.loads(limits.state_file.read_text())
    assert st2["salt"] != st["salt"] and st2["total"] == 1 and list(st2["clients"].values()) == [1]


def test_old_counters_with_raw_addresses_are_rewritten(tmp_path):
    qf = tmp_path / "_quota.json"
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    qf.write_text(json.dumps({"day": today, "total": 2, "clients": {"8.8.8.8": 2}, "spent_usd": 0.8}))
    limits = Limits(live=True, state_file=qf)
    st = json.loads(qf.read_text())
    assert "8.8.8.8" not in qf.read_text() and st["clients"] == {} and st["total"] == 2 and st["spent_usd"] == 0.8
    assert limits.left_today() == 38


@pytest.mark.parametrize("site,code", [("cross-site", 403), ("same-site", 403), ("same-origin", 200), ("none", 200)])
def test_cross_site_uploads_are_refused(live, site, code):
    client, launcher, _ = live()
    assert post_photo(client, headers={"sec-fetch-site": site}).status_code == code
    assert len(launcher.calls) == (1 if code == 200 else 0)


# ------------------------------------------------------------------ upload size, format and pixels

MULTIPART = {"content-type": "multipart/form-data; boundary=x"}


def png_header(w: int, h: int) -> bytes:
    """A PNG that declares w x h pixels but holds almost no data, so only its header can be read."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b""))


def test_upload_without_content_length_is_refused(live):
    client, launcher, _ = live()
    r = client.post("/api/runs", content=iter([b"--x\r\n"]), headers=MULTIPART)  # chunked, no length
    assert r.status_code == 411 and not launcher.calls


def test_huge_content_length_is_refused_before_the_body_is_parsed(live):
    client, launcher, limits = live()
    r = client.post("/api/runs", content=b"not multipart at all",
                    headers={**MULTIPART, "content-length": str(13 * 1024 * 1024)})
    assert r.status_code == 413  # parsing this body would have answered 400
    assert not launcher.calls and limits.left_today() == 40


@pytest.mark.parametrize("w,h", [(8000, 6000), (14000, 14000)])  # past the 40 MP cap; past Pillow's own limit
def test_huge_dimensions_are_refused_from_the_header(live, monkeypatch, w, h):
    from PIL import ImageFile

    def no_decoding(self):
        raise AssertionError("the photo was decoded")

    monkeypatch.setattr(ImageFile.ImageFile, "load", no_decoding)
    client, launcher, limits = live()
    r = post_photo(client, png_header(w, h), name="room.png")
    assert r.status_code == 400 and "megapixels" in r.json()["detail"]
    assert not launcher.calls and limits.left_today() == 40


@pytest.mark.parametrize("fmt", ["BMP", "GIF", "TIFF"])
def test_formats_other_than_jpeg_png_webp_are_refused(live, fmt):
    client, launcher, _ = live()
    r = post_photo(client, jpeg_bytes(fmt=fmt), name="room.img")
    assert r.status_code == 400 and "JPEG, PNG or WebP" in r.json()["detail"]
    assert not launcher.calls


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_png_and_webp_are_accepted(live, fmt):
    client, launcher, _ = live()
    assert post_photo(client, jpeg_bytes(fmt=fmt), name="room.img").status_code == 200
    assert len(launcher.calls) == 1


def test_a_used_up_visitor_is_refused_before_the_upload_is_decoded(live, monkeypatch):
    client, _, _ = live(per_client_per_day=1, peer=("8.8.8.8", 50000))
    assert post_photo(client).status_code == 200
    monkeypatch.setattr(server, "_normalize_photo", lambda raw: pytest.fail("decoded a photo that could not run"))
    assert post_photo(client).status_code == 429


def test_decoding_runs_in_a_worker_thread(live, monkeypatch):
    seen = []
    real = server.run_in_threadpool

    async def spy(fn, *args):
        seen.append(fn.__name__)
        return await real(fn, *args)

    monkeypatch.setattr(server, "run_in_threadpool", spy)
    client, _, _ = live()
    assert post_photo(client).status_code == 200
    assert seen == ["_normalize_photo"]


# ------------------------------------------------------------------ ids, caching and headers

def test_live_run_ids_are_long_and_dated_in_utc(live):
    client, _, _ = live()
    before = dt.datetime.now(dt.timezone.utc)
    rid = post_photo(client).json()["id"]
    after = dt.datetime.now(dt.timezone.utc)
    assert re.fullmatch(r"u\d{8}[0-9a-f]{24}", rid) and RUN_ID.fullmatch(rid)
    assert rid[1:9] in {before.strftime("%m%d%H%M"), after.strftime("%m%d%H%M")}


def test_names_with_a_trailing_newline_do_not_match():
    assert not RUN_ID.fullmatch("living\n") and not FILE_NAME.fullmatch("after.jpg\n")


def test_live_run_files_are_private_and_demo_files_public(tmp_path, replay_client):
    rid = "u11021530" + "ab" * 12
    (tmp_path / "runs" / rid).mkdir(parents=True)
    (tmp_path / "runs" / rid / "before.jpg").write_bytes(jpeg_bytes())
    r = TestClient(create_app(tmp_path / "runs", CATALOG_INDEX, None, Limits())).get(f"/runs/{rid}/before.jpg")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    r = replay_client.get("/runs/living/after.jpg")
    assert r.headers["cache-control"] == "public, max-age=86400"
    assert "cross-origin-resource-policy" not in r.headers


def test_hardening_headers_and_the_csp_switch(replay_client, monkeypatch):
    h = replay_client.get("/api/config").headers
    assert h["x-frame-options"] == "DENY"
    assert h["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert h["cross-origin-opener-policy"] == "same-origin"
    assert h["strict-transport-security"] == "max-age=31536000"
    assert "default-src 'self'" in h["content-security-policy"]
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    monkeypatch.setattr(server, "CSP", "")
    assert "content-security-policy" not in replay_client.get("/api/config").headers


def copy_demo_runs(runs):
    for rid in DEMO_IDS:
        (runs / rid).mkdir(parents=True)
        shutil.copy(DEMO_RUNS / rid / "run.json", runs / rid / "run.json")
    shutil.copy(DEMO_RUNS / "demos.json", runs / "demos.json")


def edit_living_brief(runs, text):
    rp = runs / "living" / "run.json"
    run = json.loads(rp.read_text(encoding="utf-8"))
    run["brief"] = text
    rp.write_text(json.dumps(run), encoding="utf-8")


def test_demos_are_cached_until_demos_json_changes_or_the_ttl_ends(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    copy_demo_runs(runs)
    client = TestClient(create_app(runs, CATALOG_INDEX, None, Limits(), demos_file=runs / "demos.json"))
    first = client.get("/api/demos").json()
    edit_living_brief(runs, "Changed on disk.")
    assert client.get("/api/demos").json() == first  # served from memory
    stamp = (runs / "demos.json").stat().st_mtime + 5
    os.utime(runs / "demos.json", (stamp, stamp))
    assert client.get("/api/demos").json()[0]["brief"] == "Changed on disk."
    edit_living_brief(runs, "Changed again.")
    monkeypatch.setattr(server, "DEMOS_TTL_S", 0)
    assert client.get("/api/demos").json()[0]["brief"] == "Changed again."


def test_demos_are_not_cached_with_a_refresh_callback(tmp_path):
    runs = tmp_path / "runs"
    copy_demo_runs(runs)
    calls = []
    client = TestClient(create_app(runs, CATALOG_INDEX, None, Limits(), demos_file=runs / "demos.json",
                                   refresh=lambda: calls.append(1)))
    client.get("/api/demos")
    edit_living_brief(runs, "Changed on disk.")
    assert client.get("/api/demos").json()[0]["brief"] == "Changed on disk." and len(calls) == 2


# ------------------------------------------------------------------ usage counter

def test_unreadable_counter_pauses_live_runs(live):
    client, launcher, limits = live()
    assert post_photo(client).status_code == 200
    limits.state_file.write_text('{"day": "20')  # torn, but the backup still holds the counts
    assert post_photo(client).status_code == 200
    st = json.loads(limits.state_file.read_text())  # written whole again from the backup
    assert st["total"] == 2 and st["spent_usd"] == 0.8
    limits.state_file.write_text("")
    limits.state_file.with_name("_quota.json.bak").write_text("{")
    r = post_photo(client)
    assert r.status_code == 503 and "paused" in r.json()["detail"]
    assert len(launcher.calls) == 2 and limits.left_today() == 0
    c = client.get("/api/config").json()
    assert c["live"] is False and "paused" in c["reason"] and c["runs_left_today"] is None


def test_counter_writes_are_atomic_with_a_backup(live, monkeypatch):
    client, _, limits = live()
    assert post_photo(client).status_code == 200
    qf = limits.state_file
    assert json.loads(qf.read_text()) == json.loads(qf.with_name("_quota.json.bak").read_text())
    before = qf.read_text()

    def disk_full(src, dst):
        raise OSError("disk full")

    with monkeypatch.context() as m:
        m.setattr(os, "replace", disk_full)
        with pytest.raises(OSError):
            limits.settle(0.1)
    assert qf.read_text() == before and json.loads(before)["spent_usd"] == 0.4
    assert not list(qf.parent.glob("*.tmp"))


def test_without_a_state_file_the_counter_lives_in_memory(tmp_path):
    client = TestClient(create_app(tmp_path / "runs", CATALOG_INDEX, None,
                                   Limits(live=True, per_client_per_day=1), FakeLauncher()),
                        client=("8.8.8.8", 50000))
    assert post_photo(client).status_code == 200
    assert post_photo(client).status_code == 429
    assert not list(tmp_path.rglob("*.json*"))


def test_on_change_runs_after_each_save(tmp_path):
    calls = []
    limits = Limits(live=True, state_file=tmp_path / "_quota.json", on_change=lambda: calls.append(1))
    client = TestClient(create_app(tmp_path / "runs", CATALOG_INDEX, None, limits, FakeLauncher()))
    assert post_photo(client).status_code == 200 and len(calls) == 1
    limits.settle(0.2)
    assert len(calls) == 2


# ------------------------------------------------------------------ runs that stopped

def write_run(runs, rid, status="running", age_s=0.0):
    p = runs / rid / "run.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"id": rid, "status": status, "trace": "Traceback (most recent call last)",
                             "events": [{"t": 4.2, "stage": "scan", "title": "Reading the room", "detail": ""}],
                             "room": {"before": "before.jpg", "ai_generated": False}}))
    if age_s:
        old = time.time() - age_s
        os.utime(p, (old, old))
    return p


def test_a_running_run_with_no_worker_is_marked_interrupted(tmp_path):
    runs = tmp_path / "runs"
    active: set[str] = set()
    client = TestClient(create_app(runs, CATALOG_INDEX, None, Limits(), is_active=lambda rid: rid in active))
    old = write_run(runs, "u11021200" + "a" * 24, age_s=600)
    fresh = write_run(runs, "u11021201" + "b" * 24, age_s=30)
    owned = write_run(runs, "u11021202" + "c" * 24, age_s=600)
    active.add(owned.parent.name)
    for p in (fresh, owned):
        assert client.get(f"/api/runs/{p.parent.name}").json()["status"] == "running"
    body = client.get(f"/api/runs/{old.parent.name}").json()
    assert body["status"] == "error" and body["stopped_at"] == "interrupted" and "trace" not in body
    assert body["error"].endswith(".") and "Traceback" not in body["error"]
    assert body["events"][-1]["stage"] == "error" and body["events"][-1]["t"] == 4.2
    saved = json.loads(old.read_text())
    assert saved["status"] == "error" and saved["stopped_at"] == "interrupted"


def test_without_is_active_a_running_run_is_left_alone(tmp_path):
    runs = tmp_path / "runs"
    p = write_run(runs, "u11021200" + "d" * 24, age_s=3600)
    client = TestClient(create_app(runs, CATALOG_INDEX, None, Limits()))
    assert client.get(f"/api/runs/{p.parent.name}").json()["status"] == "running"


def test_startup_sweep_stops_only_old_running_runs(tmp_path):
    runs = tmp_path / "runs"
    old = write_run(runs, "u1", age_s=20 * 60)
    recent = write_run(runs, "u2", age_s=5 * 60)
    done = write_run(runs, "u3", status="done", age_s=20 * 60)
    (runs / "_photos").mkdir()
    assert sweep_interrupted(runs, 15 * 60) == ["u1"]
    assert json.loads(old.read_text())["stopped_at"] == "interrupted"
    assert json.loads(recent.read_text())["status"] == "running"
    assert json.loads(done.read_text())["status"] == "done"


# ------------------------------------------------------------------ security review (11-02)

def test_hourly_cap(live):
    client, launcher, _ = live(per_hour=2, per_client_per_day=10, peer=("8.8.8.8", 50000))
    assert [post_photo(client).status_code for _ in range(2)] == [200, 200]
    r = post_photo(client)
    assert r.status_code == 429 and "this hour" in r.json()["detail"]
    assert len(launcher.calls) == 2


def test_the_hourly_count_starts_again_each_hour(live, monkeypatch):
    client, _, _ = live(per_hour=1, per_client_per_day=10, peer=("8.8.8.8", 50000))
    monkeypatch.setattr(server, "_utc_hour", lambda: "2026-11-02T10")
    assert [post_photo(client).status_code for _ in range(2)] == [200, 429]
    monkeypatch.setattr(server, "_utc_hour", lambda: "2026-11-02T11")
    assert post_photo(client).status_code == 200


def test_a_failed_launch_gives_back_its_hourly_slot(live):
    client, _, limits = live(launcher=FakeLauncher(fail=RuntimeError("busy")), per_hour=1, peer=("8.8.8.8", 50000))
    with pytest.raises(RuntimeError):
        post_photo(client)
    st = json.loads(limits.state_file.read_text())
    assert st["hour_total"] == 0 and not any(st["clients"].values())


def test_ipv6_networks_also_count_per_48(tmp_path):
    limits = Limits(live=True, state_file=tmp_path / "_quota.json", per_client_per_day=1)
    app = create_app(tmp_path / "runs", CATALOG_INDEX, None, limits, FakeLauncher())
    codes = [post_photo(TestClient(app, client=(f"2001:4860:4860:{i:x}::1", 1))).status_code for i in range(3)]
    assert codes == [200, 200, 429]  # three /64s in one /48, whose cap is twice the visitor cap
    assert "network" in post_photo(TestClient(app, client=("2001:4860:4860:9::1", 1))).json()["detail"]
    assert post_photo(TestClient(app, client=("2001:4861::1", 1))).status_code == 200  # another /48


@pytest.mark.parametrize("peer", ["::ffff:8.8.8.8", "2002:808:808::1", "2001:0:4136:e378:8000:63bf:f7f7:f7f7"])
def test_ipv4_inside_ipv6_counts_as_that_ipv4_address(peer):
    assert str(server._global_ip(peer)) == "8.8.8.8"  # mapped, 6to4 and Teredo


@pytest.mark.parametrize("origin,code", [("http://testserver", 200), ("http://testserver:80", 200),
                                         ("https://evil.example", 403), ("null", 403),
                                         ("http://testserver:8080", 403), ("http://[bad", 403)])
def test_another_origin_is_refused_even_without_sec_fetch_site(live, origin, code):
    client, launcher, _ = live()
    assert post_photo(client, headers={"origin": origin}).status_code == code
    assert len(launcher.calls) == (1 if code == 200 else 0)


def test_uploads_past_the_slots_are_refused_at_once(live, monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_SLOTS", 0)  # every slot taken by uploads still arriving
    client, launcher, limits = live()
    r = post_photo(client)
    assert r.status_code == 503 and "other photos" in r.json()["detail"]
    assert not launcher.calls and limits.left_today() == 40


def test_a_body_that_takes_too_long_is_refused(live, monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_READ_S", 0.05)
    monkeypatch.setattr(server.Request, "form", lambda self, **kw: asyncio.sleep(2))  # a sender that trickles
    client, launcher, limits = live()
    r = post_photo(client)
    assert r.status_code == 408 and not launcher.calls and limits.left_today() == 40


def test_live_run_files_stay_private_on_range_requests_and_in_any_case(tmp_path):
    rid = "u11021530" + "ab" * 12
    (tmp_path / "runs" / rid).mkdir(parents=True)
    (tmp_path / "runs" / rid / "before.jpg").write_bytes(jpeg_bytes())
    c = TestClient(create_app(tmp_path / "runs", CATALOG_INDEX, None, Limits()))
    r = c.get(f"/runs/{rid}/before.jpg", headers={"range": "bytes=0-10"})
    assert r.status_code == 206 and r.headers["cache-control"] == "private, no-store"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    r = c.get(f"/runs/U{rid[1:]}/before.jpg")  # served on a case-blind disk, 404 elsewhere
    assert r.headers["cache-control"] == "private, no-store"


def test_the_jpeg_comment_of_an_upload_is_dropped():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1024, 768), (90, 80, 70)).save(buf, format="JPEG", comment=b"home address here")
    out = server._normalize_photo(buf.getvalue())
    assert b"home address" not in out and "comment" not in Image.open(io.BytesIO(out)).info


def test_allowed_hosts_refuses_other_host_names():
    app = create_app(DEMO_RUNS, CATALOG_INDEX, None, Limits(), demos_file=DEMO_RUNS / "demos.json",
                     allowed_hosts=["127.0.0.1", "localhost"])
    assert TestClient(app, base_url="http://127.0.0.1:8787").get("/api/config").status_code == 200
    assert TestClient(app, base_url="http://rebound.example").get("/api/config").status_code == 400
