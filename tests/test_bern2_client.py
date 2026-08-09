"""BERN2 客户端：连接复用、有界并发、短文本跳过远程。"""

from __future__ import annotations

import threading
from typing import Any

import httpx
import pytest
import respx

from biomed_ontology.foundation.bern2 import Bern2Client, EnterpriseDictionary


@respx.mock
def test_annotate_reuses_one_http_client():
    route = respx.post("http://bern2.test/plain").mock(
        return_value=httpx.Response(200, json={"annotations": []})
    )
    with Bern2Client(base_url="http://bern2.test", concurrency=1) as client:
        client.annotate("EGFR mutation in NSCLC is common.")
        client.annotate("MET amplification was observed.")
    assert route.call_count == 2
    # 同一 Client 复用：两次请求都发出即可；关闭后 _client 清空
    assert client._client is None


@respx.mock
def test_annotate_many_dedupes_identical_texts():
    route = respx.post("http://bern2.test/plain").mock(
        return_value=httpx.Response(
            200,
            json={
                "annotations": [
                    {
                        "mention": "EGFR",
                        "obj": "gene",
                        "id": ["NCBIGene:1956"],
                        "span": {"begin": 0, "end": 4},
                        "prob": 0.9,
                    }
                ]
            },
        )
    )
    texts = ["EGFR is a kinase.", "EGFR is a kinase.", "other text about MET."]
    with Bern2Client(base_url="http://bern2.test", concurrency=2) as client:
        out = client.annotate_many(texts)
    assert len(out) == 3
    assert out[0][0].mention == "EGFR"
    assert out[1][0].mention == "EGFR"
    # 去重后远程只打 2 次（两条唯一正文）
    assert route.call_count == 2


@respx.mock
def test_annotate_many_respects_low_concurrency():
    """并发上限生效：瞬时 in-flight 不超过配置。"""
    active = 0
    max_active = 0
    lock = threading.Lock()

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            return httpx.Response(200, json={"annotations": []})
        finally:
            with lock:
                active -= 1

    respx.post("http://bern2.test/plain").mock(side_effect=_handler)
    texts = [f"sentence number {i} about cancer therapy." for i in range(8)]
    with Bern2Client(base_url="http://bern2.test", concurrency=2) as client:
        client.annotate_many(texts)
    assert max_active <= 2


@respx.mock
def test_short_text_skips_remote():
    route = respx.post("http://bern2.test/plain").mock(
        return_value=httpx.Response(200, json={"annotations": []})
    )
    with Bern2Client(base_url="http://bern2.test", min_chars=20) as client:
        out = client.annotate("EGFR")  # 4 chars
    assert out == []
    assert route.call_count == 0


def test_dictionary_only_without_endpoint():
    dictionary = EnterpriseDictionary(
        entries=[
            {
                "mention": "savolitinib",
                "type": "chemical",
                "enterprise_id": "HMD:ENT:SAVO",
                "aliases": ["savolitinib"],
            }
        ]
    )
    client = Bern2Client(base_url=None, dictionary=dictionary)
    hits = client.annotate("Patients received savolitinib daily.")
    assert any(h.source == "enterprise_dictionary" for h in hits)


@respx.mock
def test_annotate_bern2_step_uses_annotate_many(monkeypatch: pytest.MonkeyPatch):
    from biomed_ontology.lake.steps import IngestContext, annotate_bern2

    calls: dict[str, Any] = {"n": 0}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            calls["kwargs"] = kwargs

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def annotate_many(self, texts: list[str]) -> list[list[Any]]:
            calls["n"] = len(texts)
            return [[] for _ in texts]

    class _World:
        resolver = type("R", (), {"resolve_text": staticmethod(lambda t: None)})()

    monkeypatch.setattr(
        "biomed_ontology.lake.steps.require_bern2", lambda url=None: "http://bern2.test"
    )
    monkeypatch.setattr(
        "biomed_ontology.foundation.bern2.Bern2Client",
        _FakeClient,
    )
    monkeypatch.setattr(
        "biomed_ontology.foundation.world.load_world_model",
        lambda **k: _World(),
    )
    monkeypatch.setattr(
        "biomed_ontology.foundation.bern2.load_enterprise_dictionary",
        lambda p: EnterpriseDictionary(),
    )

    ctx = IngestContext(source_id="PMC", doc_id="DOC:X")
    ctx.chunks = [
        type("C", (), {"text": "a" * 20, "concept_ids": [], "entity_ids": []})(),
        type("C", (), {"text": "b" * 20, "concept_ids": [], "entity_ids": []})(),
    ]
    annotate_bern2(ctx, bern2_url="http://bern2.test")
    assert calls["n"] == 2
    assert ctx.resolver is not None
    assert calls["kwargs"]["concurrency"] >= 1
