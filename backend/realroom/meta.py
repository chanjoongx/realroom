"""Thin clients for the Meta Model API pieces Realroom uses.

- Muse Spark 1.3: room reading, planning, judging (chat completions + JSON schema output)
- SAM 3.1: box-prompted masks (Responses API stream + official meta_sam_parser)
- Muse Image 1.0: generation, single-shot edits (images endpoints), interleaved composition
  with per-image captions and multi-turn refinement (Responses API)

Every paid call is appended to a JSONL ledger with latency and an estimated cost, so the
probe report can show real numbers. The API key is read from ../../.env and never printed.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import pathlib
import threading
import time
from typing import Any, Iterable

import cv2
import numpy as np
import requests

BASE_URL = "https://api.meta.ai/v1"
PROJECT = pathlib.Path(__file__).resolve().parents[2]


def _find_env() -> pathlib.Path | None:
    """REALROOM_ENV, else the nearest .env above this package (keys never live in the repo)."""
    if os.environ.get("REALROOM_ENV"):
        return pathlib.Path(os.environ["REALROOM_ENV"])
    for parent in pathlib.Path(__file__).resolve().parents[1:5]:
        if (parent / ".env").exists():
            return parent / ".env"
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


def _load_env() -> None:
    if os.environ.get("MODEL_API_KEY") or ENV_PATH is None or not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()


def _key() -> str:
    key = os.environ.get("MODEL_API_KEY", "")
    if not key:
        raise RuntimeError("MODEL_API_KEY is not set (environment, REALROOM_ENV or a .env file)")
    return key


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:600]}")
        self.status = status
        self.body = body


class Ledger:
    """Append-only log of paid calls (kind, tag, seconds, tokens, estimated USD)."""

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
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

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


def _post(path: str, body: dict, timeout: int = 900) -> tuple[dict, float]:
    t0 = time.time()
    for attempt in range(4):
        resp = requests.post(BASE_URL + path, headers=_headers(), json=body, timeout=timeout)
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(4 * (attempt + 1))
            continue
        break
    elapsed = time.time() - t0
    if resp.status_code != 200:
        raise ApiError(resp.status_code, resp.text)
    return resp.json(), elapsed


# ---------------------------------------------------------------- Muse Spark

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
    """One structured-output call. `parts` mixes text (str) and images (bytes) in order."""
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
    }
    if extra:
        body.update(extra)
    data, elapsed = _post("/chat/completions", body)
    usage = data.get("usage") or {}
    tin, tout = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    ledger.add(kind="spark", tag=tag, model=model, sec=round(elapsed, 2), in_tok=tin, out_tok=tout,
               usd=round(tin * USD_SPARK_IN + tout * USD_SPARK_OUT, 5))
    text = data["choices"][0]["message"]["content"]
    return json.loads(text)


# ---------------------------------------------------------------- Muse Image

def _decode_images(items: list[dict]) -> list[bytes]:
    out = []
    for item in items:
        if item.get("b64_json"):
            out.append(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            out.append(requests.get(item["url"], timeout=120).content)
    return out


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
    data, elapsed = _post("/images/generations", body)
    images = _decode_images(data.get("data", []))
    ledger.add(kind="image_gen", tag=tag, sec=round(elapsed, 2), n=len(images), size=size,
               reasoning=reasoning, usd=round(USD_IMAGE * len(images), 4), usage=data.get("usage"),
               revised=[d.get("revised_prompt") for d in data.get("data", [])])
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
    body = {"model": IMAGE, "prompt": prompt, "images": [{"image_url": data_url(b)} for b in images],
            "n": n, "output_format": output_format, "reasoning_strength": reasoning}
    if size:
        body["size"] = size
    if tools is not None:
        body["tool_enablement"] = tools
    data, elapsed = _post("/images/edits", body)
    out = _decode_images(data.get("data", []))
    ledger.add(kind="image_edit", tag=tag, sec=round(elapsed, 2), n=len(out), inputs=len(images),
               reasoning=reasoning, usd=round(USD_IMAGE * len(out), 4), usage=data.get("usage"))
    return out


def image_compose(
    ledger: Ledger,
    tag: str,
    parts: Iterable[str | bytes],
    size: str | None = None,
    reasoning: str = "high",
    output_format: str = "png",
    tools: dict | None = None,
    previous_response_id: str | None = None,
) -> dict:
    """Responses API turn with interleaved captions and images. Returns images, id, summary."""
    content = []
    for part in parts:
        if isinstance(part, (bytes, bytearray)):
            content.append({"type": "input_image", "image_url": data_url(bytes(part))})
        else:
            content.append({"type": "input_text", "text": part})
    tool: dict[str, Any] = {"type": "image_generation", "reasoning_strength": reasoning,
                            "output_format": output_format}
    if size:
        tool["size"] = size
    if tools:
        tool.update(tools)
    body: dict[str, Any] = {"model": IMAGE, "input": [{"role": "user", "content": content}], "tools": [tool]}
    if previous_response_id:
        body["previous_response_id"] = previous_response_id
    data, elapsed = _post("/responses", body)
    images, summary = [], []
    for item in data.get("output", []):
        if item.get("type") == "image_generation_call" and item.get("result"):
            images.append(base64.b64decode(item["result"]))
        elif item.get("type") == "reasoning":
            summary += [s.get("text", "") for s in item.get("summary", []) if isinstance(s, dict)]
    ledger.add(kind="image_compose", tag=tag, sec=round(elapsed, 2), n=len(images),
               reasoning=reasoning, chained=bool(previous_response_id),
               usd=round(USD_IMAGE * len(images), 4), usage=data.get("usage"), summary=summary)
    return {"images": images, "id": data.get("id"), "summary": summary}


# ---------------------------------------------------------------- SAM 3.1

def box_prompt(boxes: dict[str, tuple[int, int, int, int]], width: int, height: int) -> str:
    """`<0f>` object prompt with one box per object id, in source pixels (x2, y2 inclusive)."""
    parts = []
    for oid, (x1, y1, x2, y2) in boxes.items():
        x1, x2 = max(0, min(width - 1, int(x1))), max(0, min(width - 1, int(x2)))
        y1, y2 = max(0, min(height - 1, int(y1))), max(0, min(height - 1, int(y2)))
        parts.append(f"{oid}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={width};h={height}|>")
    return "<0f>" + ",".join(parts)


async def _sam_async(image: bytes, text: str):
    from openai import AsyncOpenAI
    from meta_sam_parser import image_segmentation_format, parse_responses_stream

    client = AsyncOpenAI(base_url=BASE_URL, api_key=_key(), timeout=600)
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
    result = await parsed.final_result()
    await client.close()
    return result


def sam_masks(ledger: Ledger, tag: str, image: bytes, text: str, width: int, height: int) -> dict[str, dict]:
    """Run SAM 3.1 (concept phrase or box prompt) and return full-frame boolean masks by object id."""
    from meta_sam_parser import decode_mask_to_raster

    t0 = time.time()
    result = asyncio.run(_sam_async(image, text))
    elapsed = time.time() - t0
    masks: dict[str, dict] = {}
    for rec in result.records:
        if getattr(rec, "kind", None) != "mask":
            continue
        raster = np.frombuffer(decode_mask_to_raster(rec.mask), dtype=np.uint8)
        raster = raster.reshape(rec.mask.height, rec.mask.width)
        b = rec.bounds
        left, top = int(np.floor(b.left)), int(np.floor(b.top))
        right, bottom = int(np.ceil(b.right)), int(np.ceil(b.bottom))
        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        if right <= left or bottom <= top:
            continue
        local = cv2.resize(raster, (right - left, bottom - top), interpolation=cv2.INTER_NEAREST)
        full = np.zeros((height, width), dtype=bool)
        full[top:bottom, left:right] = local > 0
        masks[str(rec.object_id)] = {"mask": full, "conf": rec.confidence,
                                     "box": (left, top, right, bottom), "area": int(full.sum())}
    ledger.add(kind="sam", tag=tag, sec=round(elapsed, 2), n_masks=len(masks), usd=USD_SAM_IMAGE,
               outcome=getattr(result.outcome, "status", str(result.outcome)),
               diagnostics=[str(d) for d in result.diagnostics][:5])
    return masks
