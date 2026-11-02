"""Thin clients for the Meta Model API pieces Realroom uses.

- Muse Spark 1.3: room reading, planning, judging (chat completions + JSON schema output)
- SAM 3.1: concept and box-prompted masks (Responses API stream + official meta_sam_parser)
- Muse Image 1.0: generation and single-shot edits (images endpoints)

Every paid call is appended to a JSONL ledger with latency and an estimated cost, so the
probe report can show real numbers. The API key is read from ../../.env and never printed.

Every way a call can fail (an HTTP status, a dropped connection, a timeout, an SDK or stream
parser error, an answer that is not JSON, bytes that are not an image) surfaces as ApiError with
a short message, so callers handle one exception type. Retries use jittered exponential backoff
and honor Retry-After, as the API's rate-limit notes ask. A call that timed out may already have
been billed, so it is retried at most once.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import email.utils
import io
import json
import math
import os
import pathlib
import random
import re
import threading
import time
from typing import Any, Callable, Iterable

import cv2
import numpy as np
import requests
from PIL import Image

BASE_URL = "https://api.meta.ai/v1"
PROJECT = pathlib.Path(__file__).resolve().parents[2]


def _find_env() -> pathlib.Path | None:
    """REALROOM_ENV, else .env in the repository root, else .env in the folder that holds the clone.
    No other folder is searched, so an unrelated .env higher up is never read."""
    if os.environ.get("REALROOM_ENV"):
        return pathlib.Path(os.environ["REALROOM_ENV"])
    for folder in (PROJECT, PROJECT.parent):
        if (folder / ".env").exists():
            return folder / ".env"
    return None


ENV_PATH = _find_env()

SPARK = "muse-spark-1.3"
IMAGE = "muse-image-1.0"
SAM = "sam-3.1"

# Published prices (pricing-rate-limits.md, 2026-10-26 notes). Estimates only.
USD_SPARK_IN = 1.25 / 1e6
USD_SPARK_OUT = 4.25 / 1e6
USD_IMAGE = 0.01
USD_SAM_IMAGE = 2.50 / 1000

# Network limits. A read timeout is the longest wait for the answer to start (or, for the SAM
# stream, between two chunks); the connect timeout covers reaching the host.
CONNECT_S = 10
READ_S = {"spark": 180, "sam": 120, "image": 300}
ATTEMPTS = 4  # the first try plus three retries for 429, 5xx and connections that were never made
# A call that timed out or lost its connection after the request went out may still be billed (images
# are billed as they are made), so it is retried once at most.
TIMEOUT_RETRIES = 1
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
BACKOFF_S, BACKOFF_CAP_S = 1.0, 16.0  # waits of 0.5 to 1 s, then 1 to 2 s, 2 to 4 s, with jitter
RETRY_AFTER_MAX_S = 60.0  # a longer Retry-After ends the call instead of holding a run slot
MAX_EDIT_INPUTS = 10  # /images/edits takes 1 to 10 input images
BODY_CHARS = 200  # the most of a response body an ApiError message carries

# Limits on what a model answer can make this server do. The recorded runs stay far inside them (11-02:
# Spark answers of at most 6,843 tokens, reasoning included, and text fields of at most 243 characters;
# SAM answers of at most 11 instances), so they only bind when a brief or a photo pushes a model off course.
SPARK_MAX_TOKENS = 16_384  # max_completion_tokens: visible output plus reasoning, so each answer has a price cap
SPARK_TEXT_MAX = 600       # characters kept of any text field in a Spark answer
SAM_MAX_INSTANCES = 16     # instances kept of one SAM answer, the most confident first
IMAGE_MAX_BYTES = 40 * 1024 * 1024  # an image the model API hands back as a URL is read up to this size


def _load_env() -> None:
    """Take MODEL_API_KEY from the .env file and nothing else: other lines (a proxy setting, another
    service's key) never reach this process's environment."""
    if os.environ.get("MODEL_API_KEY") or ENV_PATH is None or not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "MODEL_API_KEY":
            os.environ["MODEL_API_KEY"] = value.strip().strip('"').strip("'")
            return


_load_env()


def _key() -> str:
    key = os.environ.get("MODEL_API_KEY", "")
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set (environment, REALROOM_ENV or a .env file)")
    return key


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}


def _short(text: Any, n: int = BODY_CHARS) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 3] + "..."


class ApiError(RuntimeError):
    """A Meta Model API call that gave no usable answer. `status` is the HTTP status, or 0 when
    there was none (network, timeout, SDK, stream parser, bad bytes). The message stays short."""

    def __init__(self, status: int, body: str = ""):
        self.status = int(status or 0)
        self.body = _short(body)
        head = f"HTTP {self.status}" if self.status else "no usable answer"
        super().__init__(f"{head}: {self.body}" if self.body else head)


class Ledger:
    """Append-only log of model calls (kind, tag, seconds, tokens, estimated USD; 0 for the local Perception Encoder)."""

    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def add(self, **record: Any) -> None:
        record["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:  # a line cut short by a crash
                continue
        return out

    def total_usd(self) -> float:
        return round(sum(r.get("usd", 0.0) for r in self.rows()), 4)


# ---------------------------------------------------------------- media helpers

def sniff_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    raise ValueError("unknown image bytes")


def data_url(data: bytes) -> str:
    return f"data:{sniff_mime(data)};base64,{base64.b64encode(data).decode('ascii')}"


# ---------------------------------------------------------------- retries

def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _retry_after(headers: Any) -> float | None:
    """Seconds the server asked for: Retry-After as seconds or an HTTP date, or retry-after-ms."""
    if not headers:
        return None
    ms = headers.get("retry-after-ms")
    if ms is not None:
        try:
            return max(0.0, float(ms) / 1000)
        except (TypeError, ValueError):
            pass
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        when = email.utils.parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None
    if when is None or when.tzinfo is None:
        return None
    return max(0.0, when.timestamp() - time.time())


def _delay(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff, half fixed and half random, and never shorter than Retry-After."""
    d = min(BACKOFF_CAP_S, BACKOFF_S * 2 ** attempt)
    wait = d / 2 + random.uniform(0, d / 2)
    if retry_after is not None:
        wait = max(wait, retry_after + random.uniform(0, 0.25))
    return wait


def _read_timed_out(exc: Exception) -> bool:
    """A read timeout, including one that requests reports as a ConnectionError while the body streams."""
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return True
    import urllib3

    return any(isinstance(a, urllib3.exceptions.ReadTimeoutError) for a in getattr(exc, "args", ()))


def _never_sent(exc: Exception) -> bool:
    """True when no connection was made (connect timeout, refused, name lookup failed): nothing reached
    the API, so nothing can have been billed and the call may be retried freely."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    import urllib3

    for a in getattr(exc, "args", ()):
        reason = getattr(a, "reason", a)
        if isinstance(reason, (urllib3.exceptions.NewConnectionError, urllib3.exceptions.ConnectTimeoutError)):
            return True
    return False


def _post(path: str, body: dict, read_s: float) -> tuple[dict, float]:
    """POST a JSON body with the retry rules above. Returns (answer, seconds) or raises ApiError."""
    t0 = time.time()
    timeouts = 0
    resp = None
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            resp = requests.post(BASE_URL + path, headers=_headers(), json=body, timeout=(CONNECT_S, read_s))
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as exc:
            # a connection that was never made is retried like a 503; a read timeout or a connection
            # dropped after the request went out may already be billed, so it is retried once at most
            if _never_sent(exc):
                if last:
                    raise ApiError(0, f"connection failed ({type(exc).__name__})") from None
            else:
                timeouts += 1
                if last or timeouts > TIMEOUT_RETRIES:
                    if _read_timed_out(exc):
                        raise ApiError(0, f"no answer within {read_s:g} s") from None
                    raise ApiError(0, f"connection dropped ({type(exc).__name__})") from None
            _sleep(_delay(attempt))
            continue
        except requests.exceptions.RequestException as exc:
            raise ApiError(0, f"request failed ({type(exc).__name__})") from None
        if resp.status_code in RETRY_STATUS and not last:
            wait = _retry_after(resp.headers)
            if wait is not None and wait > RETRY_AFTER_MAX_S:
                raise ApiError(resp.status_code, f"asked to retry after {wait:.0f} s")
            _sleep(_delay(attempt, wait))
            continue
        break
    if resp is None:
        raise ApiError(0, "no answer")
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text)
    try:
        data = resp.json()
    except ValueError:
        raise ApiError(resp.status_code, "the answer was not JSON") from None
    if not isinstance(data, dict):
        raise ApiError(resp.status_code, "the answer was not a JSON object")
    return data, time.time() - t0


# ---------------------------------------------------------------- Muse Spark

# Readers for fields of a model answer. The schemas ask for these types, but structured output is not
# guaranteed to be strict, so a number can come back as "12" and a boolean as "false". Values of the
# right type pass through unchanged, which keeps a well-formed answer exactly as it was.

def as_number(v: Any) -> int | float | None:
    """A finite number, or a numeric string such as "12", "12.5" or "12 cm"; None otherwise."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*(?:cm)?\s*", v)
        if not m:
            return None
        s = m.group(1)
        return float(s) if "." in s else int(s)
    return None


def as_int(v: Any) -> int | None:
    """A whole number given as an int, a float such as 3.0, or a string such as "3" or "#3"."""
    if isinstance(v, str):
        v = v.strip().lstrip("#")
    n = as_number(v)
    if isinstance(n, float):
        return int(n) if n.is_integer() else None
    return n


def as_bool(v: Any) -> bool | None:
    """True or False, also from "true", "yes", "false", "no", 1 or 0; None when it is neither."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes"):
            return True
        if s in ("false", "no"):
            return False
    return None


def as_text(v: Any) -> str:
    """A string; a number or boolean becomes its text, anything else an empty string."""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float, bool)):
        return str(v)
    return ""


def normalize_picks(data: dict) -> dict:
    """The product_picks answer (parsed in pick.py): pick numbers that came back as strings or floats
    become ints, alternates that are not numbers are dropped, entries that are not objects are
    dropped. A well-formed answer comes back equal to what went in."""
    out = dict(data)
    picks = data.get("picks")
    fixed = []
    for p in picks if isinstance(picks, list) else []:
        if not isinstance(p, dict):
            continue
        q = dict(p)
        n = as_int(p.get("pick"))
        if n is not None and n is not p.get("pick"):
            q["pick"] = n
        alts = p.get("alternates")
        if isinstance(alts, list):
            clean = [m for m in (as_int(a) for a in alts) if m is not None]
            if clean != alts:
                q["alternates"] = clean
        elif "alternates" in p:
            q["alternates"] = []
        if "slot" in q and not isinstance(q["slot"], str):
            q["slot"] = as_text(q["slot"])
        if "reason" in q and not isinstance(q["reason"], str):
            q["reason"] = as_text(q["reason"])
        fixed.append(q)
    out["picks"] = fixed
    if "set_comment" in out and not isinstance(out["set_comment"], str):
        out["set_comment"] = as_text(out["set_comment"])
    return out


# answers whose parser lives in a module this layer cannot change get their light fixes here
OUTPUT_FIXES: dict[str, Callable[[dict], dict]] = {"product_picks": normalize_picks}


def _parse_choice(data: dict) -> tuple[dict | None, str]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "returned no choices"
    choice = choices[0]
    if choice.get("finish_reason") == "length":
        return None, "answer was cut off"
    text = (choice.get("message") or {}).get("content")
    if not isinstance(text, str):
        return None, "answer had no text"
    try:
        out = json.loads(text)
    except ValueError:
        return None, "answer was not valid JSON"
    if not isinstance(out, dict):
        return None, "answer was not a JSON object"
    return out, ""


def _cap_text(x: Any) -> Any:
    """The answer with every string cut to SPARK_TEXT_MAX characters, at any depth."""
    if isinstance(x, str):
        return x[:SPARK_TEXT_MAX]
    if isinstance(x, dict):
        return {k: _cap_text(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_cap_text(v) for v in x]
    return x


def spark_json(
    ledger: Ledger,
    tag: str,
    parts: Iterable[str | bytes],
    schema: dict,
    schema_name: str,
    system: str | None = None,
    model: str = SPARK,
    extra: dict | None = None,
) -> dict:
    """One structured-output call. `parts` mixes text (str) and images (bytes) in order. An answer
    that was cut off or is not a JSON object is asked for once more with the same request, then the
    call raises ApiError. Each attempt is logged, since each one is billed. The request sets no
    temperature, so the second answer is sampled anew and can come back shorter. max_completion_tokens
    (SPARK_MAX_TOKENS) caps what one answer can cost; none of the 412 recorded Spark calls (11-02) came
    near it. Every text field of the answer is cut to SPARK_TEXT_MAX characters."""
    content = []
    for part in parts:
        if isinstance(part, (bytes, bytearray)):
            content.append({"type": "image_url", "image_url": {"url": data_url(bytes(part))}})
        else:
            content.append({"type": "text", "text": part})
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    body = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema}},
        "max_completion_tokens": SPARK_MAX_TOKENS,
    }
    if extra:
        body.update(extra)
    problem = "gave no answer"
    for attempt in range(2):
        data, elapsed = _post("/chat/completions", body, READ_S["spark"])
        usage = data.get("usage") or {}
        tin, tout = usage.get("prompt_tokens", 0) or 0, usage.get("completion_tokens", 0) or 0
        row: dict[str, Any] = dict(kind="spark", tag=tag, model=model, sec=round(elapsed, 2), in_tok=tin,
                                   out_tok=tout, usd=round(tin * USD_SPARK_IN + tout * USD_SPARK_OUT, 5))
        if attempt:
            row["retry"] = attempt
        ledger.add(**row)
        out, problem = _parse_choice(data)
        if out is not None:
            out = _cap_text(out)
            fix = OUTPUT_FIXES.get(schema_name)
            return fix(out) if fix else out
    raise ApiError(0, f"Muse Spark {problem}")


# ---------------------------------------------------------------- Muse Image

def _decode_images(items: list) -> tuple[list[bytes], int]:
    """The returned images that really are images (the header is checked before PIL sees the bytes, then
    they must decode in full), and how many items were unusable."""
    out, bad = [], 0
    for item in items:
        raw = None
        try:
            if isinstance(item, dict) and item.get("b64_json"):
                raw = base64.b64decode(item["b64_json"])
            elif isinstance(item, dict) and isinstance(item.get("url"), str):
                raw = _fetch_image(item["url"])
        except (binascii.Error, ValueError, TypeError, requests.exceptions.RequestException):
            raw = None
        if raw and not _decodes(raw):
            raw = None
        if raw:
            out.append(raw)
        else:
            bad += 1
    return out, bad


def _fetch_image(url: str) -> bytes | None:
    """An image the API returned as a link: https only, read in chunks up to IMAGE_MAX_BYTES."""
    if not url.lower().startswith("https://"):
        return None
    with requests.get(url, timeout=(CONNECT_S, 120), stream=True) as r:
        if r.status_code != 200 or not r.url.lower().startswith("https://"):
            return None
        buf = bytearray()
        for chunk in r.iter_content(64 * 1024):
            buf += chunk
            if len(buf) > IMAGE_MAX_BYTES:
                return None
        return bytes(buf)


def _decodes(raw: bytes) -> bool:
    """The bytes start like a PNG, JPEG, WebP or GIF (checked first) and then decode in full."""
    try:
        sniff_mime(raw)
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
        return True
    except Exception:  # noqa: BLE001  (PIL raises several types for broken or truncated data)
        return False


def image_generate(
    ledger: Ledger,
    tag: str,
    prompt: str,
    size: str = "1536x1152",
    n: int = 1,
    reasoning: str = "high",
    output_format: str = "png",
    tools: dict | None = None,
) -> list[bytes]:
    body = {"model": IMAGE, "prompt": prompt, "n": n, "size": size,
            "output_format": output_format, "reasoning_strength": reasoning}
    if tools is not None:
        body["tool_enablement"] = tools
    data, elapsed = _post("/images/generations", body, READ_S["image"])
    items = data.get("data") or []
    images, bad = _decode_images(items)
    row: dict[str, Any] = dict(kind="image_gen", tag=tag, sec=round(elapsed, 2), n=len(images), size=size,
                               reasoning=reasoning, usd=round(USD_IMAGE * len(items), 4), usage=data.get("usage"),
                               revised=[d.get("revised_prompt") for d in items if isinstance(d, dict)])
    if bad:
        row["unusable"] = bad
    ledger.add(**row)
    if items and not images:
        raise ApiError(0, "the image model returned bytes that are not an image")
    return images


def image_edit(
    ledger: Ledger,
    tag: str,
    prompt: str,
    images: list[bytes],
    n: int = 1,
    size: str | None = None,
    reasoning: str = "high",
    output_format: str = "png",
    tools: dict | None = None,
) -> list[bytes]:
    """Single-shot edit/compose on /v1/images/edits (JSON body, 1 to 10 inputs, n candidates)."""
    if not 1 <= len(images) <= MAX_EDIT_INPUTS:
        raise ApiError(0, f"{len(images)} input images, the edit endpoint takes 1 to {MAX_EDIT_INPUTS}")
    body = {"model": IMAGE, "prompt": prompt, "images": [{"image_url": data_url(b)} for b in images],
            "n": n, "output_format": output_format, "reasoning_strength": reasoning}
    if size:
        body["size"] = size
    if tools is not None:
        body["tool_enablement"] = tools
    data, elapsed = _post("/images/edits", body, READ_S["image"])
    items = data.get("data") or []
    out, bad = _decode_images(items)
    row: dict[str, Any] = dict(kind="image_edit", tag=tag, sec=round(elapsed, 2), n=len(out), inputs=len(images),
                               reasoning=reasoning, usd=round(USD_IMAGE * len(items), 4), usage=data.get("usage"))
    if bad:
        row["unusable"] = bad
    ledger.add(**row)
    if items and not out:
        raise ApiError(0, "the image model returned bytes that are not an image")
    return out


# ---------------------------------------------------------------- SAM 3.1

def box_prompt(boxes: dict[str, tuple[int, int, int, int]], width: int, height: int) -> str:
    """`<0f>` object prompt with one box per object id, in source pixels (x2, y2 inclusive)."""
    parts = []
    for oid, (x1, y1, x2, y2) in boxes.items():
        x1, x2 = max(0, min(width - 1, int(x1))), max(0, min(width - 1, int(x2)))
        y1, y2 = max(0, min(height - 1, int(y1))), max(0, min(height - 1, int(y2)))
        parts.append(f"{oid}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={width};h={height}|>")
    return "<0f>" + ",".join(parts)


async def _sam_async(image: bytes, text: str, key: str):
    import httpx
    from meta_sam_parser import image_segmentation_format, parse_responses_stream
    from openai import AsyncOpenAI

    timeout = httpx.Timeout(READ_S["sam"], connect=CONNECT_S)
    # retries happen in _sam_result with the shared rules; the context manager closes the client on
    # every path, errors included
    async with AsyncOpenAI(base_url=BASE_URL, api_key=key, timeout=timeout, max_retries=0) as client:
        stream = await client.responses.create(
            model=SAM,
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": text},
                {"type": "input_image", "image_url": data_url(image)},
            ]}],
            stream=True,
            metadata={"include_confidence": "true", "mask_encoding": "one_bit"},
        )
        parsed = parse_responses_stream(stream, image_segmentation_format())
        return await parsed.final_result()


def _status_text(exc: Exception) -> str:
    return _short(getattr(exc, "message", None) or str(exc))


def _sam_result(image: bytes, text: str):
    """One SAM 3.1 request with the shared retry rules. Every failure becomes ApiError."""
    import openai

    key = _key()  # a missing key is a setup problem, not an API failure
    timeouts = 0
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            return asyncio.run(_sam_async(image, text, key))
        except ApiError:
            raise
        except openai.APITimeoutError:
            timeouts += 1
            if last or timeouts > TIMEOUT_RETRIES:
                raise ApiError(0, f"SAM 3.1 gave no answer within {READ_S['sam']:g} s") from None
            _sleep(_delay(attempt))
        except openai.APIConnectionError as exc:
            if last:
                raise ApiError(0, f"could not reach SAM 3.1 ({type(exc).__name__})") from None
            _sleep(_delay(attempt))
        except openai.APIStatusError as exc:
            status = int(getattr(exc, "status_code", 0) or 0)
            if status in RETRY_STATUS and not last:
                wait = _retry_after(getattr(getattr(exc, "response", None), "headers", None))
                if wait is not None and wait > RETRY_AFTER_MAX_S:
                    raise ApiError(status, f"asked to retry after {wait:.0f} s") from None
                _sleep(_delay(attempt, wait))
                continue
            raise ApiError(status, _status_text(exc)) from None
        except Exception as exc:  # noqa: BLE001  (SDK and stream parser errors with no HTTP status)
            raise ApiError(0, f"SAM 3.1 answer could not be read ({type(exc).__name__})") from None
    raise ApiError(0, "SAM 3.1 gave no answer")


def _mask_record(rec: Any, width: int, height: int, decode: Callable) -> dict | None:
    raster = np.frombuffer(decode(rec.mask), dtype=np.uint8)
    raster = raster.reshape(rec.mask.height, rec.mask.width)
    b = rec.bounds
    left, top = int(np.floor(b.left)), int(np.floor(b.top))
    right, bottom = int(np.ceil(b.right)), int(np.ceil(b.bottom))
    left, top = max(0, left), max(0, top)
    right, bottom = min(width, right), min(height, bottom)
    if right <= left or bottom <= top:
        return None
    local = cv2.resize(raster, (right - left, bottom - top), interpolation=cv2.INTER_NEAREST)
    full = np.zeros((height, width), dtype=bool)
    full[top:bottom, left:right] = local > 0
    return {"mask": full, "conf": rec.confidence, "box": (left, top, right, bottom), "area": int(full.sum())}


def sam_masks(ledger: Ledger, tag: str, image: bytes, text: str, width: int, height: int) -> dict[str, dict]:
    """Run SAM 3.1 (concept phrase or box prompt) and return full-frame boolean masks by object id.
    A failed request raises ApiError; a mask record that cannot be decoded is skipped and counted."""
    from meta_sam_parser import decode_mask_to_raster

    t0 = time.time()
    result = _sam_result(image, text)
    elapsed = time.time() - t0
    masks: dict[str, dict] = {}
    unreadable = 0
    records = [r for r in getattr(result, "records", None) or [] if getattr(r, "kind", None) == "mask"]
    dropped = max(0, len(records) - SAM_MAX_INSTANCES)
    if dropped:  # keep the most confident, in the order SAM gave them; each kept one is a full-frame mask
        conf = [as_number(getattr(r, "confidence", None)) or 0.0 for r in records]
        keep = set(sorted(range(len(records)), key=lambda i: -conf[i])[:SAM_MAX_INSTANCES])
        records = [r for i, r in enumerate(records) if i in keep]
    for rec in records:
        try:
            m = _mask_record(rec, width, height, decode_mask_to_raster)
        except Exception:  # noqa: BLE001  (one unreadable record must not lose the others)
            unreadable += 1
            continue
        if m is not None:
            masks[str(rec.object_id)] = m
    outcome = getattr(result, "outcome", None)
    row: dict[str, Any] = dict(kind="sam", tag=tag, sec=round(elapsed, 2), n_masks=len(masks), usd=USD_SAM_IMAGE,
                               outcome=getattr(outcome, "status", str(outcome)),
                               diagnostics=[str(d) for d in (getattr(result, "diagnostics", None) or [])][:5])
    if unreadable:
        row["unreadable"] = unreadable
    if dropped:
        row["dropped"] = dropped
    ledger.add(**row)
    return masks
