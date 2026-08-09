"""Tree Chunk 引擎：父子路径与 Evidence Object 字段。"""

from __future__ import annotations

from biomed_ontology._generated.hmd_fact import DocTypeEnum
from biomed_ontology.corpus import Document, DocumentSection, ImageBlock, TableBlock
from biomed_ontology.corpus.tree import build_document_tree, iter_evidence_nodes, tree_to_chunks


def test_build_tree_sentence_parent_chain() -> None:
    doc = Document(
        doc_id="DOC:tree1",
        source_id="pubmed",
        title="EGFR inhibitors",
        doc_type=DocTypeEnum.JOURNAL_ARTICLE,
        sections=[
            DocumentSection(
                name="Results",
                text="ABC-001 inhibits EGFR. It shows activity in NSCLC models.",
                page=2,
            )
        ],
    )
    tree = build_document_tree(doc)
    assert tree.node_kind == "document"
    sentences = [n for n in tree.walk() if n.node_kind == "sentence"]
    assert len(sentences) >= 2
    s0 = sentences[0]
    assert s0.parent_id
    assert "Results" in s0.section_path
    parents = {n.node_id: n for n in tree.walk()}
    para = parents[s0.parent_id]
    assert para.node_kind == "paragraph"
    assert para.parent_id
    section = parents[para.parent_id]
    assert section.node_kind == "section"


def test_tree_to_chunks_evidence_fields() -> None:
    doc = Document(
        doc_id="DOC:tree2",
        source_id="pubmed",
        title="Demo",
        doc_type=DocTypeEnum.JOURNAL_ARTICLE,
        sections=[DocumentSection(name="Abstract", text="Savolitinib targets MET.", page=1)],
        tables=[
            TableBlock(
                table_id="T1",
                caption="IC50 table",
                header=["drug", "ic50"],
                rows=[["savolitinib", "1.2"]],
                page=3,
            )
        ],
        images=[
            ImageBlock(image_id="F1", caption="Pathway figure", page=4),
        ],
    )
    chunks = tree_to_chunks(build_document_tree(doc))
    kinds = {c.node_kind for c in chunks}
    assert "sentence" in kinds
    assert "table" in kinds or "caption" in kinds
    assert "figure" in kinds or "caption" in kinds
    for c in chunks:
        assert c.chunk_id
        assert c.section_path
        assert c.parent_id or c.node_kind in {"document"}  # leaves have parents


def test_iter_evidence_nodes_excludes_document_root() -> None:
    doc = Document(
        doc_id="DOC:tree3",
        source_id="pubmed",
        title="X",
        doc_type=DocTypeEnum.JOURNAL_ARTICLE,
        sections=[DocumentSection(name="Intro", text="Hello world.", page=1)],
    )
    nodes = iter_evidence_nodes(build_document_tree(doc))
    assert all(n.node_kind != "document" for n in nodes)
    assert all(n.content.strip() for n in nodes)
