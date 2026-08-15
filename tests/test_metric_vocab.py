"""指标受控词表。"""

from __future__ import annotations

from biomed_ontology.ontology.metrics import load_metric_vocab


def test_metric_vocab_canonicalizes_orr() -> None:
    vocab = load_metric_vocab()
    assert vocab.version
    term = vocab.canonicalize("客观缓解率")
    assert term is not None
    assert term.metric == "ORR"
    assert term.unit == "%"
    assert vocab.canonicalize("unknown-header") is None


def test_metric_vocab_codes_are_in_linkml_enum() -> None:
    from biomed_ontology.ontology.metrics import metric_codes_from_vocab, schema_metric_codes

    missing = metric_codes_from_vocab() - schema_metric_codes()
    assert not missing
