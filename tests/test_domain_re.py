"""分域 RE prompt：文献 / CSR / 说明书 / 专利。"""

from __future__ import annotations

from biomed_ontology._generated.hmd_fact import DocTypeEnum
from biomed_ontology.corpus import Document
from biomed_ontology.corpus.extractors.llm_text import _SYSTEM, system_prompt_for


def _doc(doc_type: DocTypeEnum) -> Document:
    return Document(
        doc_id="DOC:x",
        source_id="PUBMED",
        title="t",
        doc_type=doc_type,
    )


def test_system_prompt_switches_by_doctype() -> None:
    paper = system_prompt_for(_doc(DocTypeEnum.JOURNAL_ARTICLE))
    csr = system_prompt_for(_doc(DocTypeEnum.CLINICAL_STUDY_REPORT))
    label = system_prompt_for(_doc(DocTypeEnum.LABEL))
    patent = system_prompt_for(_doc(DocTypeEnum.PATENT))
    assert "Domain=literature" in paper
    assert "Domain=CSR" in csr
    assert "has_adverse_event" in csr
    assert "Domain=label" in label
    assert "Domain=patent" in patent
    assert paper != csr


def test_unknown_doctype_falls_back_to_base() -> None:
    assert system_prompt_for(None) == _SYSTEM
    assert "genes" in _SYSTEM
    assert "adverse events" in _SYSTEM
