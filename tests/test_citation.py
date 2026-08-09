"""P15 Citationware：碎片 → 原文的还原必须可信、且不成为许可后门。"""

from __future__ import annotations

import pytest

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.pipeline import build_literature_base
from biomed_ontology.tools import ToolApi, dispatch
from biomed_ontology.tools.citation import restore_context as raw_restore
from tests.support.search_fakes import make_searcher

LICENSED = frozenset({"MOCK_LICENSED"})


@pytest.fixture(scope="module")
def api() -> ToolApi:
    kb = build_literature_base(with_graph=False)
    searcher = make_searcher(kb)
    return ToolApi.from_backends(kb=kb, backend=searcher.backend, searcher=searcher)


@pytest.fixture(scope="module")
def open_chunk(api: ToolApi):
    return api.search_documents("surufatinib", top_k=5)["results"][0]["chunk_id"]


def test_restore_returns_the_whole_section_not_just_the_fragment(api, open_chunk):
    out = api.restore_context(open_chunk)
    fragment = next(c for c in api.kb.chunks if c.chunk_id == open_chunk)
    assert fragment.text in out["full_text"]
    assert open_chunk in out["restored_chunk_ids"]


def test_restore_gives_a_page_to_turn_back_to(api, open_chunk):
    """还原的意义就是让人翻回原文。页码丢了，这个工具就白做了。"""
    out = api.restore_context(open_chunk)
    assert out["page_start"] >= 1
    assert out["page_end"] >= out["page_start"]


def test_breadcrumb_names_the_document_and_the_section(api, open_chunk):
    out = api.restore_context(open_chunk)
    doc = api.kb.document(out["doc_id"])
    assert doc.title in out["breadcrumb"]
    assert out["section_path"] in out["breadcrumb"]


def test_restore_is_not_a_license_bypass(api):
    """碎片 id 换全文 —— 这是还原最容易变成的后门。"""
    licensed = [
        c
        for c in api.kb.chunks
        if api.kb.document(c.doc_id).license_tier is not LicenseTierEnum.TIER_0
    ]
    assert licensed, "语料里没有受限文档，这条断言就形同虚设"
    target = licensed[0]

    denied = api.restore_context(target.chunk_id)
    assert any("LICENSE_DENIED" in w for w in denied["warnings"])
    assert not denied.get("full_text")

    # 同一个碎片，带上权益就能还原 —— 证明拒绝来自许可判定，不是别的偶然原因。
    out = api.restore_context(target.chunk_id, entitlements=LICENSED)
    assert out["warnings"] == []
    assert out["full_text"]


def test_restore_reuses_the_search_license_predicate(api):
    """还原若自己实现一份判断，迟早和检索对不上。这里锁死它调用的是同一个谓词。"""
    denied = []

    def permits(rank, source_id):
        denied.append((rank, source_id))
        return False

    chunk = api.kb.chunks[0]
    with pytest.raises(PermissionError):
        raw_restore(api.kb, chunk.chunk_id, permits=permits)
    assert denied, "谓词根本没被调用，说明还原绕开了许可判定"


def test_unknown_chunk_is_a_typed_error_not_a_crash(api):
    out = api.restore_context("CHK:txt.deadbeef")
    assert any("NOT_FOUND" in w for w in out["warnings"])


def test_truncation_is_reported_never_silent(api, open_chunk):
    """静默截断会让"还原完整原文"变成一句假话。"""
    out = api.restore_context(open_chunk, max_chars=40)
    assert out["truncated"] is True
    assert len(out["full_text"]) <= 40
    full = api.restore_context(open_chunk, max_chars=100_000)
    assert full["truncated"] is False


@pytest.mark.parametrize("scope", ["SECTION", "SIBLINGS", "DOCUMENT"])
def test_scope_widens_monotonically(api, open_chunk, scope):
    out = api.restore_context(open_chunk, restore_scope=scope, max_chars=100_000)
    section = api.restore_context(open_chunk, restore_scope="SECTION", max_chars=100_000)
    assert set(section["restored_chunk_ids"]) <= set(out["restored_chunk_ids"])


def test_restored_chunks_come_back_in_reading_order(api, open_chunk):
    """字典序拼出来的章节读起来是乱的，等于没还原。"""
    out = api.restore_context(open_chunk, restore_scope="DOCUMENT", max_chars=100_000)
    by_id = {c.chunk_id: c for c in api.kb.chunks}
    keys = [
        (by_id[cid].page or 0, getattr(by_id[cid], "char_start", 0) or 0)
        for cid in out["restored_chunk_ids"]
    ]
    assert keys == sorted(keys)


def test_restore_is_reachable_through_dispatch(api, open_chunk):
    """工具要在契约、REST、MCP 三处同时可见，dispatch 是它们共同的入口。"""
    out = dispatch(api, "restore_context", {"chunk_id": open_chunk})
    assert out["full_text"]
    scoped = dispatch(api, "restore_context", {"chunk_id": open_chunk, "restore_scope": "DOCUMENT"})
    assert set(out["restored_chunk_ids"]) <= set(scoped["restored_chunk_ids"])


def test_restore_carries_the_full_envelope(api, open_chunk):
    out = api.restore_context(open_chunk, trace_id="TRC:restore-1")
    for key in ("trace_id", "ontology_release_id", "license_tier_max", "elapsed_ms"):
        assert key in out
    assert out["trace_id"] == "TRC:restore-1"


# ------------------------------------------------------------------ 证据树


def test_evidence_tree_groups_fragments_by_document(api):
    res = api.search_documents("surufatinib", top_k=10)
    tree = res["evidence_tree"]
    assert sum(node["chunk_count"] for node in tree) == res["total"]
    assert len({node["doc_id"] for node in tree}) == len(tree)


def test_evidence_tree_dissolves_the_illusion_of_independent_evidence(api):
    """同一段落的多个碎片在扁平列表里看着像多条独立证据。树把它们收回一个节点。"""
    res = api.search_documents("肿瘤", top_k=10, entitlements=LICENSED)
    tree = res["evidence_tree"]
    flat = res["total"]
    assert len(tree) <= flat
    for node in tree:
        for section in node["sections"]:
            assert section["chunks"]


def test_evidence_tree_is_ordered_by_best_score(api):
    tree = api.search_documents("surufatinib", top_k=10)["evidence_tree"]
    scores = [node["best_score"] for node in tree]
    assert scores == sorted(scores, reverse=True)


def test_evidence_tree_never_leaks_a_filtered_document(api):
    """树是从已过滤的命中构建的。它若能出现受限文档，说明构建时绕开了过滤。"""
    tree = api.search_documents("肿瘤", top_k=10)["evidence_tree"]
    assert all(node["license_tier"] == LicenseTierEnum.TIER_0.value for node in tree)


def test_evidence_tree_pages_bracket_its_chunks(api):
    tree = api.search_documents("surufatinib", top_k=10)["evidence_tree"]
    for node in tree:
        for section in node["sections"]:
            pages = [c["page"] for c in section["chunks"] if c["page"]]
            if pages:
                assert section["page_start"] <= min(pages)
                assert section["page_end"] >= max(pages)
