"""VectorIndex Protocol：默认 Ngram，可注入替代后端。"""

from __future__ import annotations

from biomed_ontology.normalize import Normalizer
from biomed_ontology.normalize.matchers import CandidateHit, NgramVectorIndex
from biomed_ontology.pipeline import build_literature_base


class _EmptyVectors:
    def search(self, text, *, top_k=5, entity_types=None, min_score=0.60):
        return []


def test_normalizer_accepts_injected_vector_index():
    kb = build_literature_base(with_corpus=False, with_graph=False)
    n = Normalizer(
        concepts=kb.concepts,
        synonyms=kb.synonyms,
        vectors=_EmptyVectors(),
    )
    assert not isinstance(n.vectors, NgramVectorIndex)
    assert n.vectors.search("savolitinib") == []


def test_default_still_ngram():
    kb = build_literature_base(with_corpus=False, with_graph=False)
    n = Normalizer(concepts=kb.concepts, synonyms=kb.synonyms)
    assert isinstance(n.vectors, NgramVectorIndex)
    hits = n.vectors.search("savolitinib", top_k=3)
    assert all(isinstance(h, CandidateHit) for h in hits)
