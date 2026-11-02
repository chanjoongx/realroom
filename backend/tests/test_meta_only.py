"""Hackathon rule: every model Realroom calls at runtime is a Meta model, reached only at api.meta.ai."""
from __future__ import annotations

import ast
import pathlib
import re

from conftest import REPO

PKG = REPO / "backend" / "realroom"
META_MODELS = {"muse-spark-1.3", "muse-image-1.0", "sam-3.1"}


def test_model_constants_are_meta():
    from realroom import catalog, meta

    assert meta.BASE_URL == "https://api.meta.ai/v1"
    assert {meta.SPARK, meta.IMAGE, meta.SAM} == META_MODELS
    assert catalog.EMB_MODEL_ID == "pe-core-b16-224"


def _string_literals(path: pathlib.Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def test_no_other_model_or_endpoint_in_the_runtime_code():
    other = re.compile(r"\b(gpt-|o\d-|claude|gemini|mistral|qwen|deepseek|grok|stable-diffusion|sdxl|flux|dall-e|siglip)", re.I)
    for f in sorted(PKG.glob("*.py")):
        for s in _string_literals(f):
            assert not other.search(s), f"{f.name}: {s[:80]!r}"
            for url in re.findall(r"https?://[^\s\"'<>]+", s):
                assert url.startswith("https://api.meta.ai/"), f"{f.name} calls {url}"


def test_openai_client_points_at_meta():
    """The OpenAI Python SDK is only the HTTP client Meta's docs use; it must never default to another host."""
    src = (PKG / "meta.py").read_text(encoding="utf-8")
    calls = re.findall(r"(?:Async)?OpenAI\(([^)]*)\)", src)
    assert calls, "meta.py builds an OpenAI-compatible client for SAM"
    assert all("base_url=BASE_URL" in c for c in calls)
