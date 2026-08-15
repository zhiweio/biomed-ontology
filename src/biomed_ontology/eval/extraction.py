"""Knowledge Extraction 评测：predicate F1 / grounding / 否定抑制。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from biomed_ontology._generated.hmd_concept import LanguageEnum, LicenseTierEnum
from biomed_ontology._generated.hmd_fact import DocTypeEnum, ModalityChannelEnum
from biomed_ontology.corpus import Chunk, Document
from biomed_ontology.corpus.extract import TriModalPipeline, default_extractors
from biomed_ontology.observability import TraceContext

__all__ = ["ExtractionCaseResult", "ExtractionEval", "eval_extraction", "load_extraction_gold"]

DEFAULT_GOLD = Path(__file__).resolve().parents[3] / "data" / "gold" / "extraction.yaml"


@dataclass
class ExtractionCaseResult:
    case_id: str
    expected: set[tuple[str, str, str]]
    predicted: set[tuple[str, str, str]]
    tp: int
    fp: int
    fn: int

    @property
    def ok(self) -> bool:
        return self.fp == 0 and self.fn == 0


@dataclass
class ExtractionEval:
    precision: float
    recall: float
    f1: float
    grounding_rate: float
    negation_ok: bool
    total_cases: int
    cases: list[ExtractionCaseResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.f1 >= 0.5 and self.negation_ok


def load_extraction_gold(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or DEFAULT_GOLD
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return list(raw.get("cases") or [])


def eval_extraction(
    normalizer: Any,
    *,
    gold_path: Path | None = None,
    pipeline: TriModalPipeline | None = None,
    enable_rules: bool = True,
) -> ExtractionEval:
    """对 gold 句跑抽取；默认开规则旁路以便无 LLM 时仍可评测。"""
    cases = load_extraction_gold(gold_path)
    pipe = pipeline or TriModalPipeline(
        extractors=default_extractors(enable_llm=False, enable_rules=enable_rules)
    )
    ctx = TraceContext(trace_id="eval-extract", ontology_release_id="eval")

    tp = fp = fn = 0
    grounded = total_pred = 0
    results: list[ExtractionCaseResult] = []
    failures: list[str] = []
    negation_ok = True

    for i, case in enumerate(cases):
        # 规则-only 评测可跳过仅适用于 LLM 的用例
        if enable_rules and pipeline is None and case.get("rules") is False:
            continue
        text = str(case.get("text") or "")
        cid = str(case.get("id") or f"case-{i}")
        raw_type = str(case.get("doc_type") or "JOURNAL_ARTICLE")
        try:
            doc_type = DocTypeEnum(raw_type)
        except ValueError:
            doc_type = DocTypeEnum.JOURNAL_ARTICLE
        doc = Document(
            doc_id=f"DOC:EVAL:{cid}",
            source_id="PUBMED",
            title=cid,
            doc_type=doc_type,
            language=LanguageEnum.en,
            license_tier=LicenseTierEnum.TIER_0,
            sections=[],
        )
        chunk = Chunk(
            chunk_id=f"CHK:EVAL:{cid}",
            doc_id=doc.doc_id,
            text=text,
            section="body",
            char_start=0,
            char_end=len(text),
            modality=ModalityChannelEnum.TEXT,
        )
        facts = pipe.run([doc], [chunk], normalizer=normalizer, ctx=ctx)
        pred = {
            (
                f.subject_id,
                f.predicate.value if hasattr(f.predicate, "value") else str(f.predicate),
                f.object_id or "",
            )
            for f in facts
            if f.object_id
        }
        for f in facts:
            total_pred += 1
            if f.subject_id.startswith("HMD:") and (
                not f.object_id or f.object_id.startswith("HMD:")
            ):
                grounded += 1

        expect_rows = case.get("expect") or []
        expect = {(str(e["subject"]), str(e["predicate"]), str(e["object"])) for e in expect_rows}
        c_tp = len(pred & expect)
        c_fp = len(pred - expect)
        c_fn = len(expect - pred)
        tp += c_tp
        fp += c_fp
        fn += c_fn
        results.append(
            ExtractionCaseResult(
                case_id=cid, expected=expect, predicted=pred, tp=c_tp, fp=c_fp, fn=c_fn
            )
        )
        if not expect_rows and pred:
            negation_ok = False
            failures.append(f"{cid}: expected empty, got {sorted(pred)}")
        elif c_fp or c_fn:
            failures.append(f"{cid}: fp={sorted(pred - expect)} fn={sorted(expect - pred)}")

    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    ground_rate = grounded / total_pred if total_pred else 1.0
    return ExtractionEval(
        precision=round(prec, 4),
        recall=round(rec, 4),
        f1=round(f1, 4),
        grounding_rate=round(ground_rate, 4),
        negation_ok=negation_ok,
        total_cases=len(cases),
        cases=results,
        failures=failures,
    )
