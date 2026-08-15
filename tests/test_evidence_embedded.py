"""foundation_evidence 不得把占位向量标成已嵌入。"""

from __future__ import annotations

from biomed_ontology.lake import evidence_index


def test_placeholder_vectors_are_marked_not_embedded() -> None:
    assert evidence_index._EMBEDDED is False
    assert evidence_index._DIM == 32
