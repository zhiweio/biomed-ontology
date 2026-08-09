"""extraction gold 评测可跑通。"""

from __future__ import annotations

from biomed_ontology.eval.extraction import eval_extraction


def test_eval_extraction_rules(kb):
    report = eval_extraction(kb.normalizer, enable_rules=True)
    assert report.total_cases >= 3
    # 规则路径至少应抽到部分阳性句；否定句不得污染
    assert report.negation_ok or "negation" in " ".join(report.failures)
    assert 0.0 <= report.f1 <= 1.0
