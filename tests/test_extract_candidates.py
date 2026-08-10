"""MentionPair 候选与规则/LLM 抽取接线。"""

from __future__ import annotations

import json

from biomed_ontology._generated.hmd_concept import LanguageEnum, LicenseTierEnum
from biomed_ontology._generated.hmd_fact import DocTypeEnum, ModalityChannelEnum
from biomed_ontology.corpus import Chunk, Document
from biomed_ontology.corpus.candidates import (
    Mention,
    build_mention_pairs,
    split_sentences,
)
from biomed_ontology.corpus.extract import RuleTextRelationExtractor, TriModalPipeline
from biomed_ontology.corpus.extractors.llm_text import LlmTextRelationExtractor
from biomed_ontology.llm.chat import ChatResult
from biomed_ontology.observability import TraceContext


def test_split_sentences_zh_en():
    sents = split_sentences("A inhibits B. 沃利替尼靶向MET。")
    assert len(sents) >= 2


def test_build_mention_pairs_type_matrix():
    text = "Savolitinib inhibits MET."
    mentions = [
        Mention("Savolitinib", 0, 11, "drug", "HMD:ENT:DC:savolitinib", 0.9),
        Mention("MET", 21, 24, "target", "HMD:ENT:TGT:MET", 0.9),
    ]
    pairs = build_mention_pairs(text, mentions)
    assert pairs
    assert any((p.subject.entity_id or "").endswith("savolitinib") for p in pairs)


def test_rule_extractor_inhibits(kb, ctx):
    doc = Document(
        doc_id="DOC:T1",
        source_id="PUBMED",
        title="t",
        doc_type=DocTypeEnum.JOURNAL_ARTICLE,
        language=LanguageEnum.en,
        license_tier=LicenseTierEnum.TIER_0,
    )
    chunk = Chunk(
        chunk_id="CHK:T1",
        doc_id=doc.doc_id,
        text="Savolitinib selectively inhibits MET kinase activity.",
        section="body",
        char_start=0,
        char_end=50,
        modality=ModalityChannelEnum.TEXT,
    )
    facts = RuleTextRelationExtractor().extract(chunk, doc, kb.normalizer, ctx)
    assert facts
    assert any(f.predicate.value == "inhibits" for f in facts)


class _FakeChat:
    name = "fake"

    def complete(self, messages, *, response_format=None):
        return ChatResult(
            text=json.dumps(
                {
                    "relations": [
                        {
                            "subject": "Savolitinib",
                            "object": "MET",
                            "predicate": "inhibits",
                            "negated": False,
                            "uncertain": False,
                            "confidence": 0.8,
                            "quote": "Savolitinib selectively inhibits MET",
                        }
                    ]
                }
            )
        )


def test_llm_extractor_with_fake_chat(kb, ctx):
    doc = Document(
        doc_id="DOC:T2",
        source_id="PUBMED",
        title="t",
        doc_type=DocTypeEnum.JOURNAL_ARTICLE,
        language=LanguageEnum.en,
        license_tier=LicenseTierEnum.TIER_0,
    )
    chunk = Chunk(
        chunk_id="CHK:T2",
        doc_id=doc.doc_id,
        text="Savolitinib selectively inhibits MET kinase activity.",
        section="body",
        char_start=0,
        char_end=50,
        modality=ModalityChannelEnum.TEXT,
        entity_ids=["HMD:ENT:DC:savolitinib", "HMD:ENT:TGT:MET"],
    )
    ext = LlmTextRelationExtractor(chat=_FakeChat(), enabled=True)
    facts = ext.extract(chunk, doc, kb.normalizer, ctx)
    assert facts
    assert facts[0].extractor_id == "text-llm-v1"
    assert facts[0].predicate.value == "inhibits"


def test_trimodal_merges_rule_and_table(kb):
    pipe = TriModalPipeline(
        extractors=[RuleTextRelationExtractor()],
    )
    ctx = TraceContext(trace_id="t", ontology_release_id=kb.release_id)
    doc = Document(
        doc_id="DOC:T3",
        source_id="PUBMED",
        title="t",
        doc_type=DocTypeEnum.JOURNAL_ARTICLE,
        language=LanguageEnum.en,
        license_tier=LicenseTierEnum.TIER_0,
    )
    chunk = Chunk(
        chunk_id="CHK:T3",
        doc_id=doc.doc_id,
        text="Savolitinib for the treatment of NSCLC.",
        section="body",
        char_start=0,
        char_end=40,
        modality=ModalityChannelEnum.TEXT,
    )
    facts = pipe.run([doc], [chunk], normalizer=kb.normalizer, ctx=ctx)
    assert any(f.predicate.value == "treats" for f in facts)
