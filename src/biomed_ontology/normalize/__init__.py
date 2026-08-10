"""归一化服务（L3）—— 四级级联 + 同步埋点。

埋点与业务逻辑同一次写完，不是后置补。理由很实际：
级联的中间态（每一级的候选集与落选原因）在函数返回后就消失了，
后补埋点只能拿到最终结果，而排障要问的恰恰是"为什么没选那个"。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from biomed_ontology._generated.hmd_concept import (
    EntityTypeEnum,
    LicenseTierEnum,
    MappingJustificationEnum,
    SynonymScopeEnum,
)
from biomed_ontology._generated.hmd_fact import NormalizationStageEnum
from biomed_ontology.alias import SCOPE_WEIGHTS, contains_cjk, normalize_alias
from biomed_ontology.normalize.matchers import (
    CandidateHit,
    ContextDisambiguator,
    DictionaryIndex,
    NgramVectorIndex,
    RuleMatcher,
    VectorIndex,
    detect_spans,
    has_entity_shape,
    maximal_spans,
    zh_segment_bounded,
)
from biomed_ontology.observability import Candidate, TraceContext

__all__ = [
    "ExpansionOut",
    "MatchedConcept",
    "NormalizationResult",
    "Normalizer",
]

# 候选分差小于此值即认为"分不开"，触发下一级。
# 阈值设在 0.08：低于它时排名靠随机性（别名长度、n-gram 分布）主导，不该被当作确定结论。
AMBIGUITY_MARGIN = 0.08


@dataclass
class MatchedConcept:
    concept_id: str
    matched_text: str
    stage: NormalizationStageEnum
    confidence: float
    justification: MappingJustificationEnum
    char_start: int = 0
    char_end: int = 0
    alias_id: str | None = None
    scope: SynonymScopeEnum | None = None
    entity_type: EntityTypeEnum | None = None
    preferred_label_en: str | None = None
    preferred_label_zh: str | None = None
    is_ambiguous: bool = False
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    rationale: str | None = None


@dataclass
class NormalizationResult:
    matched: list[MatchedConcept]
    unmapped_spans: list[str] = field(default_factory=list)
    llm_invoked: bool = False

    @property
    def concept_ids(self) -> list[str]:
        return [m.concept_id for m in self.matched]


@dataclass
class ExpansionOut:
    term: str
    weight: float
    concept_id: str
    alias_id: str | None
    scope: SynonymScopeEnum
    depth: int = 0
    lang: str = "en"


class Normalizer:
    """把文本变成唯一 CURIE。"""

    def __init__(
        self,
        *,
        concepts,
        synonyms,
        ambiguity_index: dict | None = None,
        release_id: str = "0.1.0",
        vectors: VectorIndex | None = None,
    ) -> None:
        self.release_id = release_id
        self.dictionary = DictionaryIndex.from_build(concepts, synonyms)
        self.rules = RuleMatcher(self.dictionary)
        self.vectors: VectorIndex = vectors or NgramVectorIndex.from_index(self.dictionary)
        self._concepts = {c.concept_id: c for c in concepts}
        self._children: dict[str, list[str]] = {}
        key_to_id = {c.seed_key: c.concept_id for c in concepts}
        for c in concepts:
            for p in c.parents:
                pid = key_to_id.get(p, p)
                self._children.setdefault(pid, []).append(c.concept_id)
        self._ambiguous_norms = {s.alias_norm for s in synonyms if s.is_ambiguous}
        self.disambiguator = (
            ContextDisambiguator(ambiguity_index, key_to_id) if ambiguity_index else None
        )

    # -------------------------------------------------- 主流程

    def normalize(
        self,
        text: str,
        *,
        ctx: TraceContext,
        entity_types: set[EntityTypeEnum] | None = None,
        context: str | None = None,
        top_k: int = 5,
        min_confidence: float = 0.0,
        allow_llm: bool = True,
        detect: bool = False,
    ) -> NormalizationResult:
        with ctx.span("normalize", **{"hmd.input_len": len(text)}) as sp:
            if detect:
                result = self._normalize_document(
                    text,
                    ctx=ctx,
                    entity_types=entity_types,
                    allow_llm=allow_llm,
                    context=context,
                )
            else:
                m = self._normalize_mention(
                    text,
                    ctx=ctx,
                    entity_types=entity_types,
                    context=context or text,
                    top_k=top_k,
                    allow_llm=allow_llm,
                )
                matched = [m] if m and m.confidence >= min_confidence else []
                unmapped = [] if matched else ([text] if has_entity_shape(text) else [])
                result = NormalizationResult(
                    matched, unmapped, llm_invoked=bool(m and m.stage is NormalizationStageEnum.LLM)
                )
            sp.set(
                **{
                    "ontology.concept_ids": result.concept_ids,
                    "hmd.unmapped_count": len(result.unmapped_spans),
                    "hmd.llm_invoked": result.llm_invoked,
                }
            )
            return result

    def _normalize_document(
        self,
        text: str,
        *,
        ctx: TraceContext,
        entity_types: set[EntityTypeEnum] | None,
        allow_llm: bool,
        context: str | None = None,
    ) -> NormalizationResult:
        """整篇文本抽实体。长片段优先消费，已覆盖区间不再重复匹配。"""
        matched: list[MatchedConcept] = []
        unmapped: list[str] = []
        taken: list[tuple[int, int]] = []
        llm = False
        # 调用方给的 context 是消歧最强的线索（通常是章节主题或查询意图），
        # 只拿正文当上下文会让它被整段稀释掉，短文本上尤其致命。
        disamb_context = f"{context}\n{text}" if context else text
        for span_text, start, end in detect_spans(text):
            if any(s < end and start < e for s, e in taken):
                continue
            hits = self.dictionary.match(span_text, entity_types)
            if not hits:
                continue
            m = self._resolve(
                span_text,
                hits,
                stage=NormalizationStageEnum.DICTIONARY,
                justification=MappingJustificationEnum.LexicalMatching,
                ctx=ctx,
                context=disamb_context,
                allow_llm=allow_llm,
            )
            if m:
                m.char_start, m.char_end = start, end
                matched.append(m)
                taken.append((start, end))
                llm = llm or m.stage is NormalizationStageEnum.LLM
        candidates = [
            (span_text, start, end)
            for span_text, start, end in detect_spans(text, max_gram=2)
            if not any(s < end and start < e for s, e in taken)
            and has_entity_shape(span_text)
            and (not contains_cjk(span_text) or zh_segment_bounded(text, start, end))
        ]
        for span_text, _s, _e in maximal_spans(candidates):
            if span_text not in unmapped:
                unmapped.append(span_text)
        matched.sort(key=lambda m: m.char_start)
        return NormalizationResult(matched, unmapped[:20], llm_invoked=llm)

    def _normalize_mention(
        self,
        text: str,
        *,
        ctx: TraceContext,
        entity_types: set[EntityTypeEnum] | None,
        context: str,
        top_k: int,
        allow_llm: bool,
    ) -> MatchedConcept | None:
        stages = (
            (
                NormalizationStageEnum.DICTIONARY,
                MappingJustificationEnum.LexicalMatching,
                lambda: self.dictionary.match(text, entity_types),
            ),
            (
                NormalizationStageEnum.RULE,
                MappingJustificationEnum.CompositeMatching,
                lambda: self.rules.match(text, entity_types),
            ),
            (
                NormalizationStageEnum.VECTOR,
                MappingJustificationEnum.SemanticSimilarityThresholdMatching,
                lambda: self.vectors.search(text, top_k=top_k, entity_types=entity_types),
            ),
        )
        for stage, justification, run in stages:
            t0 = time.perf_counter()
            with ctx.span(f"normalize.{stage.value.lower()}", **{"hmd.stage": stage.value}) as sp:
                hits = run()
                sp.set(**{"hmd.candidate_count": len(hits)})
            if not hits:
                ctx.record_decision(
                    stage=stage,
                    justification=justification,
                    chosen=None,
                    candidates=[],
                    state_before=text,
                    state_after=None,
                    confidence=0.0,
                    elapsed_ms=(time.perf_counter() - t0) * 1000,
                )
                continue
            return self._resolve(
                text,
                hits,
                stage=stage,
                justification=justification,
                ctx=ctx,
                context=context,
                allow_llm=allow_llm,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

        ctx.record_decision(
            stage=NormalizationStageEnum.ABSTAIN,
            justification=MappingJustificationEnum.UnspecifiedMatching,
            chosen=None,
            state_before=text,
            confidence=0.0,
        )
        return None

    def _resolve(
        self,
        text: str,
        hits: list[CandidateHit],
        *,
        stage: NormalizationStageEnum,
        justification: MappingJustificationEnum,
        ctx: TraceContext,
        context: str,
        allow_llm: bool,
        elapsed_ms: float = 0.0,
    ) -> MatchedConcept | None:
        cands = [Candidate(h.concept_id, h.score, h.channel, stage=stage.value) for h in hits]
        top = hits[0]
        margin = top.score - hits[1].score if len(hits) > 1 else 1.0
        # 已登记的歧义词无条件走消歧，即使本库只收录了其中一个义项。
        # 否则“MET 在本库只有靶点义”会被当成确定结论记录，而它实际上是覆盖不足导致的假确定。
        registered_ambiguous = normalize_alias(text) in self._ambiguous_norms
        needs_disambiguation = registered_ambiguous or (len(hits) > 1 and margin < AMBIGUITY_MARGIN)

        if needs_disambiguation and allow_llm and self.disambiguator is not None:
            with ctx.span("normalize.llm", **{"hmd.stage": NormalizationStageEnum.LLM.value}):
                outcome = self.disambiguator.disambiguate(
                    text, context, [(h.concept_id, self._label(h.concept_id)) for h in hits]
                )
            if outcome.chosen:
                ctx.record_decision(
                    stage=NormalizationStageEnum.LLM,
                    justification=MappingJustificationEnum.LLMDisambiguation,
                    chosen=outcome.chosen,
                    candidates=cands,
                    state_before=text,
                    state_after=outcome.chosen,
                    confidence=outcome.confidence,
                    model_id=outcome.model_id,
                    elapsed_ms=elapsed_ms,
                )
                # 消歧选中的义项可能不在本次候选集里（候选集受实体类型过滤限制），
                # 那种情况下宁可回退到词典 top1，也不能返回一个未被检索层召回的概念。
                if any(h.concept_id == outcome.chosen for h in hits):
                    return self._to_match(
                        text,
                        outcome.chosen,
                        stage=NormalizationStageEnum.LLM,
                        justification=MappingJustificationEnum.LLMDisambiguation,
                        confidence=outcome.confidence,
                        hits=hits,
                        rationale=outcome.rationale,
                    )
            elif registered_ambiguous:
                # 消歧判定该 mention 指向本体外义项 —— 放弃比猜一个更有价值，
                # 因为放弃会产出 unmapped 信号，而猜错只会产出一条无人发现的错误结论。
                ctx.record_decision(
                    stage=NormalizationStageEnum.ABSTAIN,
                    justification=MappingJustificationEnum.LLMDisambiguation,
                    chosen=None,
                    candidates=cands,
                    state_before=text,
                    confidence=outcome.confidence,
                    model_id=outcome.model_id,
                    elapsed_ms=elapsed_ms,
                )
                return None

        ctx.record_decision(
            stage=stage,
            justification=justification,
            chosen=top.concept_id,
            candidates=cands,
            state_before=text,
            state_after=top.concept_id,
            confidence=top.score,
            elapsed_ms=elapsed_ms,
        )
        return self._to_match(
            text,
            top.concept_id,
            stage=stage,
            justification=justification,
            confidence=top.score,
            hits=hits,
            alias_id=top.alias_id,
            scope=top.scope,
        )

    def _to_match(
        self,
        text: str,
        concept_id: str,
        *,
        stage: NormalizationStageEnum,
        justification: MappingJustificationEnum,
        confidence: float,
        hits: list[CandidateHit],
        alias_id: str | None = None,
        scope: SynonymScopeEnum | None = None,
        rationale: str | None = None,
    ) -> MatchedConcept:
        c = self._concepts.get(concept_id)

        return MatchedConcept(
            concept_id=concept_id,
            matched_text=text,
            stage=stage,
            confidence=round(confidence, 4),
            justification=justification,
            alias_id=alias_id,
            scope=scope,
            entity_type=c.entity_type if c else None,
            preferred_label_en=c.preferred_label_en if c else None,
            preferred_label_zh=c.preferred_label_zh if c else None,
            is_ambiguous=normalize_alias(text) in self._ambiguous_norms,
            alternatives=[(h.concept_id, h.score) for h in hits[1:4]],
            rationale=rationale,
        )

    def _label(self, concept_id: str) -> str:
        c = self._concepts.get(concept_id)
        return c.preferred_label_en if c else concept_id

    # -------------------------------------------------- 扩展

    def expand(
        self,
        concept_id: str,
        *,
        ctx: TraceContext | None = None,
        max_depth: int = 2,
        include_descendants: bool = True,
        min_weight: float = 0.1,
        languages: set[str] | None = None,
    ) -> list[ExpansionOut]:
        """概念 → 加权扩展词集（设计决策 D2）。

        权重 = scope 权重 × 本体距离衰减。
        子孙每深一层衰减 0.85：查"肺癌"该召回"肺腺癌"，但不该把三级子类抬到与本体同权。
        """
        out: list[ExpansionOut] = []
        seen: set[tuple[str, str]] = set()

        def add(cid: str, depth: int) -> None:
            decay = 0.85**depth
            for e in self.dictionary.aliases_of(cid):
                w = SCOPE_WEIGHTS[e.scope] * decay
                if w < min_weight:
                    continue
                if languages and e.lang.value not in languages:
                    continue
                key = (e.alias_raw.casefold(), cid)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    ExpansionOut(
                        e.alias_raw, round(w, 4), cid, e.alias_id, e.scope, depth, e.lang.value
                    )
                )

        add(concept_id, 0)
        if include_descendants:
            frontier, depth = [concept_id], 0
            while frontier and depth < max_depth:
                depth += 1
                nxt = []
                for cid in frontier:
                    for child in self._children.get(cid, []):
                        add(child, depth)
                        nxt.append(child)
                frontier = nxt
        out.sort(key=lambda t: (-t.weight, t.term))
        if ctx is not None:
            ctx.record_decision(
                stage="EXPAND",
                justification=MappingJustificationEnum.OntologyDescendantExpansion,
                chosen=concept_id,
                candidates=[Candidate(t.concept_id, t.weight, "expansion") for t in out[:10]],
                confidence=1.0,
                state_after=f"expansion_size={len(out)}",
            )
        return out

    def descendants(self, concept_id: str, max_depth: int = 3) -> list[str]:
        out, frontier, depth = [], [concept_id], 0
        while frontier and depth < max_depth:
            depth += 1
            nxt = [c for cid in frontier for c in self._children.get(cid, [])]
            out.extend(nxt)
            frontier = nxt
        return out

    def concept(self, concept_id: str):
        return self._concepts.get(concept_id)

    def concept_tier(self, concept_id: str) -> LicenseTierEnum:
        c = self._concepts.get(concept_id)
        return c.license_tier if c else LicenseTierEnum.TIER_0
