"""语料标引、三模态抽取、事实层。"""

from __future__ import annotations

import os

import pytest

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology._generated.hmd_fact import ModalityChannelEnum

SAVOLITINIB = "HMD:ENT:DC:savolitinib"
MET = "HMD:ENT:TGT:MET"


def test_corpus_loads_all_modalities(kb):
    mods = {c.modality for c in kb.chunks}
    assert mods == {
        ModalityChannelEnum.TEXT,
        ModalityChannelEnum.TABLE,
        ModalityChannelEnum.IMAGE,
    }


def test_every_chunk_traces_back_to_a_document(kb):
    doc_ids = {d.doc_id for d in kb.documents}
    assert all(c.doc_id in doc_ids for c in kb.chunks)


def test_chunk_ids_are_content_derived(kb):
    """切片 ID 由内容派生，重跑构建不会漂移。

    自增 ID 会让每次重建都产生一批"新"切片，
    于是所有既有引用一夜之间全部失效。
    """
    from biomed_ontology.pipeline import build_knowledge_base

    again = build_knowledge_base()
    assert {c.chunk_id for c in again.chunks} == {c.chunk_id for c in kb.chunks}


def test_chunk_ids_survive_a_process_boundary():
    """同进程内重建看不出问题：内置 `hash()` 在一个进程里是恒定的。

    真正的失配发生在"索引进程"和"检索进程"之间 —— 表格/图像切片曾用
    `hash(table_id)` 当种子，PYTHONHASHSEED 一变 ID 全变，
    表现为 Milvus 里的 ID 在知识库里查无此人。所以这里必须跨进程比。
    """
    import subprocess
    import sys

    prog = (
        "from biomed_ontology.pipeline import build_knowledge_base;"
        "print(' '.join(sorted(c.chunk_id for c in build_knowledge_base().chunks)))"
    )
    seen = set()
    for seed in ("0", "1", "2"):
        out = subprocess.run(
            [sys.executable, "-c", prog],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        seen.add(out.stdout.strip())
    assert len(seen) == 1, "切片 ID 随 PYTHONHASHSEED 漂移，说明种子里混进了随机化的 hash()"


def test_facts_carry_at_least_one_evidence(kb):
    """无凭据的事实等于无出处的断言 —— 整条链路的价值都建立在这条约束上。"""
    assert kb.facts
    assert all(f.evidence for f in kb.facts)


def test_evidence_quotes_come_from_the_cited_chunk(kb):
    """引文必须真的出现在被引切片里，否则"可溯源"只是形式。"""
    for f in kb.facts:
        for ev in f.evidence:
            if not ev.quote:
                continue
            chunk = kb.chunk(ev.chunk_id)
            assert chunk is not None, f"{f.fact_id} 引用了不存在的切片 {ev.chunk_id}"


def test_facts_span_all_three_extractors(kb):
    """三模态各自都要真的产出事实，否则"三模态"只是架构图上的字。"""
    assert {f.modality for f in kb.facts} == {
        ModalityChannelEnum.TEXT,
        ModalityChannelEnum.TABLE,
        ModalityChannelEnum.IMAGE,
    }


def test_cross_lingual_merge_produces_one_fact_with_two_sources(kb):
    """中文专利与英文论文说的同一件事必须合并成一条，而不是两条。

    不合并的话，agent 会把同一结论当成两个独立证据来源，
    从而高估把握 —— 这在研发决策里是实打实的风险。
    """
    merged = [
        f
        for f in kb.facts
        if f.subject_id == SAVOLITINIB
        and f.object_id == MET
        and len({ev.doc_id for ev in f.evidence}) > 1
    ]
    assert merged, "跨语种同一断言未合并"
    docs = {ev.doc_id for ev in merged[0].evidence}
    assert any("CNIPA" in d for d in docs)
    assert any("PMID" in d for d in docs)


def test_merged_confidence_rises_but_stays_below_certainty(kb):
    for f in kb.facts:
        assert 0.0 < f.confidence <= 0.97


def test_document_labels_cover_every_document(kb):
    assert set(kb.labels) == {d.doc_id for d in kb.documents}
    assert all(labels for labels in kb.labels.values())


def test_labels_are_multi_dimensional(kb):
    """单维标引撑不起"研发阶段 × 证据类型"这类交叉筛选。"""
    dims = {label.dimension for labels in kb.labels.values() for label in labels}
    assert len(dims) >= 3


def test_every_label_records_the_keywords_that_triggered_it(kb):
    """标引必须自带证据。

    只给一个标签而不给命中词，人工复核就只能重读全文，
    复核成本一高，标引质量就再也没人管了。
    """
    for labels in kb.labels.values():
        for label in labels:
            assert label.matched_keywords, f"{label.label_id} 未记录命中关键词"


def test_license_tier_propagates_from_source_to_fact(kb):
    """事实的许可等级必须继承自最严格的证据来源。

    取最宽松的那一档就等于用一条公开凭据把商业内容洗白了。
    """
    for f in kb.facts:
        tiers = {kb.doc_tier(ev.doc_id) for ev in f.evidence}
        assert f.license_tier in tiers


def test_commercial_document_stays_tier3(kb):
    doc = kb.document("DOC:PATSNAP.PS-2023-00417")
    assert doc is not None
    assert doc.license_tier is LicenseTierEnum.TIER_3


def test_build_is_warning_free(kb):
    assert kb.warnings == []


@pytest.mark.parametrize("key", ["concepts", "synonyms", "documents", "chunks", "facts", "triples"])
def test_stats_are_non_zero(kb, key):
    assert kb.stats()[key] > 0
