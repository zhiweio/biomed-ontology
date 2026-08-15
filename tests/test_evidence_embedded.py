"""foundation_evidence 不得把占位向量标成已嵌入。"""

from __future__ import annotations

from biomed_ontology.lake import evidence_index


def test_placeholder_vectors_are_marked_not_embedded() -> None:
    assert evidence_index._EMBEDDED is False
    assert evidence_index._DIM == 32


def test_chunk_id_is_joinable() -> None:
    from biomed_ontology.lake.claim_bridge import evidence_id_for_chunk
    from biomed_ontology.lake.evidence_join import join_chunks_to_evidence

    chunk_id = "CHK:DOC:1:0"
    joined = join_chunks_to_evidence(
        [{"chunk_id": chunk_id, "text": "quote"}],
        [{"chunk_id": chunk_id, "evidence_id": evidence_id_for_chunk(chunk_id)}],
    )
    assert joined[0]["joined"] is True
    assert joined[0]["embedded"] is False
    assert joined[0]["chunk_id"] == chunk_id
