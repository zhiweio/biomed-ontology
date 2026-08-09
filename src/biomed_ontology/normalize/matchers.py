"""归一化级联的四级实现（L3）。

级联顺序按 [代价, 可信度] 排：词典最快最准，LLM 最慢最不确定。
每一级都记录决策与候选，因此可以事后回答"这次为什么走到了第 4 级" ——
LLM 触发率是级联健康度的核心指标，它上升通常意味着词典该补了，而不是模型该换了。
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from biomed_ontology._generated.hmd_concept import (
    EntityTypeEnum,
    LanguageEnum,
    SynonymScopeEnum,
)
from biomed_ontology.alias import contains_cjk, generate_code_variants, normalize_alias

__all__ = [
    "CandidateHit",
    "ContextDisambiguator",
    "DictionaryIndex",
    "DisambiguationOutcome",
    "LlmDisambiguator",
    "NgramVectorIndex",
    "RuleMatcher",
    "detect_spans",
]


@dataclass(frozen=True)
class CandidateHit:
    concept_id: str
    score: float
    channel: str
    alias_id: str | None = None
    alias_raw: str | None = None
    scope: SynonymScopeEnum | None = None
    entity_type: EntityTypeEnum | None = None


@dataclass
class DictEntry:
    concept_id: str
    alias_id: str
    alias_raw: str
    scope: SynonymScopeEnum
    lang: LanguageEnum
    entity_type: EntityTypeEnum
    is_ambiguous: bool


# ---------------------------------------------------------------- L1 词典


class DictionaryIndex:
    """alias_norm → 概念的精确索引。

    一个 norm 可能对应多个概念（歧义），索引保留全部而不是取第一个 ——
    在索引层做静默去重会让歧义在最上游就丢失，下游再也无从发现。
    """

    def __init__(self) -> None:
        self._by_norm: dict[str, list[DictEntry]] = defaultdict(list)
        self._by_concept: dict[str, list[DictEntry]] = defaultdict(list)

    def add(self, entry: DictEntry) -> None:
        norm = normalize_alias(entry.alias_raw)
        if not norm:
            return
        self._by_norm[norm].append(entry)
        self._by_concept[entry.concept_id].append(entry)

    @classmethod
    def from_build(cls, concepts, synonyms) -> DictionaryIndex:
        idx = cls()
        etype = {c.concept_id: c.entity_type for c in concepts}
        for s in synonyms:
            idx.add(
                DictEntry(
                    concept_id=s.concept_id,
                    alias_id=s.alias_id,
                    alias_raw=s.alias_raw,
                    scope=s.scope,
                    lang=s.lang,
                    entity_type=etype.get(s.concept_id, EntityTypeEnum.SUBSTANCE),
                    is_ambiguous=s.is_ambiguous,
                )
            )
        return idx

    def lookup(self, text: str) -> list[DictEntry]:
        return list(self._by_norm.get(normalize_alias(text), []))

    def aliases_of(self, concept_id: str) -> list[DictEntry]:
        return list(self._by_concept.get(concept_id, []))

    def all_norms(self) -> list[str]:
        return list(self._by_norm)

    def match(
        self, text: str, entity_types: set[EntityTypeEnum] | None = None
    ) -> list[CandidateHit]:
        hits = []
        for e in self.lookup(text):
            if entity_types and e.entity_type not in entity_types:
                continue
            # BROAD 别名不参与归一化：查 "PI3K" 不应判定为 PIK3CD（设计决策 D2）。
            if e.scope is SynonymScopeEnum.BROAD:
                continue
            hits.append(
                CandidateHit(
                    concept_id=e.concept_id,
                    score=1.0 if e.scope is SynonymScopeEnum.EXACT else 0.8,
                    channel="dictionary",
                    alias_id=e.alias_id,
                    alias_raw=e.alias_raw,
                    scope=e.scope,
                    entity_type=e.entity_type,
                )
            )
        return _dedupe_best(hits)


# ---------------------------------------------------------------- L2 规则


class RuleMatcher:
    """规则模糊匹配。只做能解释的变换，不做通用编辑距离。

    通用模糊匹配在生物医药领域危险：AZD6094 与 AZD6244 编辑距离为 2，
    却是完全不同的药。所以规则限定在代号分隔符、大小写、希腊字母、常见拼写变体。
    """

    def __init__(self, index: DictionaryIndex) -> None:
        self.index = index

    def match(
        self, text: str, entity_types: set[EntityTypeEnum] | None = None
    ) -> list[CandidateHit]:
        hits: list[CandidateHit] = []
        for variant in self._variants(text):
            for h in self.index.match(variant, entity_types):
                hits.append(
                    CandidateHit(
                        concept_id=h.concept_id,
                        score=h.score * 0.95,
                        channel="rule",
                        alias_id=h.alias_id,
                        alias_raw=h.alias_raw,
                        scope=h.scope,
                        entity_type=h.entity_type,
                    )
                )
        return _dedupe_best(hits)

    def _variants(self, text: str) -> list[str]:
        out = set(generate_code_variants(text))
        stripped = re.sub(r"[®™©]", "", text).strip()
        if stripped != text:
            out.add(stripped)
        # 复方药名的成分拆分：TAS-102 = trifluridine/tipiracil
        for part in re.split(r"[/+]", text):
            part = part.strip()
            if len(part) >= 4 and part != text:
                out.add(part)
        out.discard(text)
        return sorted(out)


# ---------------------------------------------------------------- L3 向量


class NgramVectorIndex:
    """字符 n-gram TF-IDF 向量召回。

    刻意不用预训练模型：PoC 要求全链路断网可重放，
    模型权重会引入版本漂移，让"同一 release 重放结果一致"这条验收项失效。
    生产替换为 SapBERT/BGE-M3 时只需实现同样的 `search` 签名。
    """

    def __init__(self, n: int = 3) -> None:
        self.n = n
        self._vectors: dict[str, Counter[str]] = {}
        self._concept_of: dict[str, str] = {}
        self._df: Counter[str] = Counter()
        self._entity_of: dict[str, EntityTypeEnum] = {}

    def _grams(self, text: str) -> Counter[str]:
        s = f"  {normalize_alias(text)}  "
        if len(s) <= self.n:
            return Counter([s])
        return Counter(s[i : i + self.n] for i in range(len(s) - self.n + 1))

    @classmethod
    def from_index(cls, index: DictionaryIndex, n: int = 3) -> NgramVectorIndex:
        vi = cls(n)
        for concept_id, entries in index._by_concept.items():
            for e in entries:
                if e.scope is SynonymScopeEnum.BROAD:
                    continue
                key = f"{concept_id}|{e.alias_id}"
                grams = vi._grams(e.alias_raw)
                vi._vectors[key] = grams
                vi._concept_of[key] = concept_id
                vi._entity_of[key] = e.entity_type
                vi._df.update(set(grams))
        return vi

    def _weighted(self, grams: Counter[str]) -> dict[str, float]:
        total = max(1, len(self._vectors))
        return {g: c * math.log(1 + total / (1 + self._df.get(g, 0))) for g, c in grams.items()}

    def search(
        self,
        text: str,
        *,
        top_k: int = 5,
        entity_types: set[EntityTypeEnum] | None = None,
        min_score: float = 0.60,
    ) -> list[CandidateHit]:
        # 默认 0.60：挡住 -afenib/-inib 类 OOV 近邻误判（如 sorafenib→regorafenib
        # 仅 0.57），同时保留单字符 typo（sovolitinib→savolitinib ≈ 0.62）。
        q = self._weighted(self._grams(text))
        qn = math.sqrt(sum(v * v for v in q.values())) or 1.0
        scored: list[tuple[float, str]] = []
        for key, grams in self._vectors.items():
            if entity_types and self._entity_of[key] not in entity_types:
                continue
            d = self._weighted(grams)
            dn = math.sqrt(sum(v * v for v in d.values())) or 1.0
            dot = sum(q[g] * d[g] for g in q.keys() & d.keys())
            sim = dot / (qn * dn)
            if sim >= min_score:
                scored.append((sim, key))
        scored.sort(key=lambda t: (-t[0], t[1]))
        hits = [
            CandidateHit(
                concept_id=self._concept_of[key],
                score=round(sim, 4),
                channel="vector",
                alias_id=key.split("|", 1)[1],
                entity_type=self._entity_of[key],
            )
            for sim, key in scored
        ]
        return _dedupe_best(hits)[:top_k]


# ---------------------------------------------------------------- L4 消歧


@dataclass
class DisambiguationOutcome:
    chosen: str | None
    confidence: float
    rationale: str
    model_id: str | None = None
    scores: dict[str, float] = field(default_factory=dict)


class LlmDisambiguator(Protocol):
    """LLM 消歧的接入点。PoC 用规则实现，生产接 DeepSeek / 本地垂类模型。"""

    model_id: str

    def disambiguate(
        self, mention: str, context: str, candidates: list[tuple[str, str]]
    ) -> DisambiguationOutcome: ...


# 无线索命中时的置信度上限。取值刻意压到 evolution.LOW_CONFIDENCE_THRESHOLD 之下，
# 这样"歧义词 + 零证据"必然被挖成 low_confidence_normalization 信号，
# 变成一条"该补线索词了"的待办，而不是一次没人知道的静默押注。
PRIOR_ONLY_CONFIDENCE_CAP = 0.55


class ContextDisambiguator:
    """基于人工维护的 context_cues 做消歧。

    先用线索词而不是直接上 LLM，理由是成本与可解释性：
    线索词由领域专家维护、命中即可解释、零延迟，
    真正需要 LLM 的是线索词都不命中的长尾 —— 那才是模型的价值区间。
    """

    model_id = "context-cues-v1"

    def __init__(self, ambiguity_index: dict[str, object], key_to_concept: dict[str, str]) -> None:
        self._index = ambiguity_index
        self._key_to_concept = key_to_concept

    def senses(self, mention: str) -> list[tuple[str | None, float, list[str]]]:
        """返回全部义项。本体库未收录的义项以 concept_id=None 保留，

        不能直接丢弃 —— 丢弃后归一化会把“8 MET of exercise”自信地判成 MET 靶点，
        而诚实的答案是“这不是我们收录的任何一个义项”。
        """
        entry = self._index.get(normalize_alias(mention))
        if entry is None:
            return []
        return [
            (self._key_to_concept.get(s.concept_key), s.prior, list(s.context_cues))
            for s in entry.senses  # type: ignore[attr-defined]
        ]

    def disambiguate(
        self, mention: str, context: str, candidates: list[tuple[str, str]]
    ) -> DisambiguationOutcome:
        senses = self.senses(mention)
        if not senses:
            return DisambiguationOutcome(None, 0.0, "无登记义项", self.model_id)
        ctx_norm = context.casefold()
        scores: dict[str, float] = {}
        evidence: dict[str, list[str]] = {}
        for i, (cid, prior, cues) in enumerate(senses):
            key = cid or f"__unmapped_{i}"
            hit = [c for c in cues if c.casefold() in ctx_norm]
            scores[key] = prior * _likelihood_ratio(hit)
            evidence[key] = hit
        total = sum(scores.values()) or 1.0
        normed = {k: v / total for k, v in scores.items()}
        best = max(normed, key=lambda k: (normed[k], k))
        if best.startswith("__unmapped_"):
            return DisambiguationOutcome(
                None,
                round(normed[best], 4),
                "上下文指向本体库未收录的义项，拒绝归一化",
                self.model_id,
                normed,
            )
        cues_hit = evidence[best]
        if not any(evidence.values()):
            # 一条线索都没命中：算出来的是先验，不是证据。
            # 直接返回归一化先验会把"这个词通常指 X"说成"这次它指 X"——
            # 两者是不同的断言，而下游只会读到后者。0.85 又高于低置信阈值，
            # 于是这条无证据的选择连信号都产生不了，永远没人复核。
            # 封顶不改变义项排序，只是拒绝在没有证据时给出高置信度。
            return DisambiguationOutcome(
                best,
                min(round(normed[best], 4), PRIOR_ONLY_CONFIDENCE_CAP),
                "上下文无线索，按先验概率选取（无证据，已封顶置信度）",
                self.model_id,
                normed,
            )
        return DisambiguationOutcome(
            best, round(normed[best], 4), f"上下文命中线索 {cues_hit}", self.model_id, normed
        )


# ---------------------------------------------------------------- span 检测

# 线索证据按似然比累乘而非线性加权：MET 靶点与"代谢当量"的先验差 17 倍，
# 线性增益无论如何都翻不过来，结果就是上下文证据形同虚设。
# 多词线索给更高的比值 —— "metabolic equivalent" 比 "exercise" 特异得多，
# 用词数当特异性代理，避免让领域同事再维护一套逐词权重。
_LR_SINGLE_WORD = 3.0
_LR_MULTI_WORD = 8.0
_MAX_EFFECTIVE_CUES = 3


def _likelihood_ratio(hits: list[str]) -> float:
    """命中线索的累积似然比。取最强的若干条，避免单条误命中主导结论。"""
    ratios = sorted(
        (_LR_MULTI_WORD if " " in c.strip() else _LR_SINGLE_WORD for c in hits), reverse=True
    )
    lr = 1.0
    for r in ratios[:_MAX_EFFECTIVE_CUES]:
        lr *= r
    return lr


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*\d*|[A-Za-z]+|\d+[A-Za-z]+")


def detect_spans(text: str, max_gram: int = 4) -> list[tuple[str, int, int]]:
    """从长文本切出候选实体片段。

    中英混排必须分开处理：英文按词滑窗，中文按字滑窗。
    统一按空格切会让中文整段变成一个 token，任何别名都匹配不上。
    """
    spans: list[tuple[str, int, int]] = []
    for m in _TOKEN_RE.finditer(text):
        spans.append((m.group(), m.start(), m.end()))
    words = [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    for n in range(2, max_gram + 1):
        for i in range(len(words) - n + 1):
            chunk = words[i : i + n]
            spans.append((text[chunk[0][1] : chunk[-1][2]], chunk[0][1], chunk[-1][2]))
    for m in re.finditer(r"[\u4e00-\u9fff]+", text):
        seg, base = m.group(), m.start()
        for size in range(2, min(len(seg), 8) + 1):
            for i in range(len(seg) - size + 1):
                spans.append((seg[i : i + size], base + i, base + i + size))
    seen: set[tuple[str, int, int]] = set()
    out = []
    for s in spans:
        if s[0].strip() and s not in seen:
            seen.add(s)
            out.append(s)
    # 长片段优先，让 "lung adenocarcinoma" 先于 "lung" 被消费。
    return sorted(out, key=lambda s: (-(s[2] - s[1]), s[1]))


def has_entity_shape(text: str) -> bool:
    """判断片段是否值得报为 unmapped 信号。

    宁可漏报也不能滥报：审校队列一旦被“evaluated in”这类普通短语淹没，
    人工就会直接放弃整个队列，真信号也跟着一起失效。
    因此只放行三类形态：研发代号、全大写缩写、Title Case 专名。
    """
    t = text.strip().strip(".,;:()[]")
    if not t:
        return False
    if contains_cjk(t):
        return _has_zh_entity_shape(t)
    if " " in t:
        words = t.split()
        if len(words) > 3:
            return False
        return all(w[:1].isupper() or _looks_like_code(w) for w in words) and any(
            len(w) >= 4 for w in words
        )
    if t.casefold() in _STOPWORDS:
        return False
    if _looks_like_code(t):
        return True
    if t.isupper() and 2 <= len(t) <= 8:
        return True
    return t[:1].isupper() and len(t) >= 6


# 中文没有大小写与词边界，滑窗必然切出 '一种新' '中的含义' 这类碎片。
# 唯一可用的形态信号是"片段里不含功能字、也不是通用领域词"。
_ZH_FUNCTION_CHARS = set(
    "的了是在与和及或为被把对中上下等该其有无不这那个之以并所很再亦已均可将如而则者们就还更最也于"
)
_ZH_GENERIC = {
    "一种",
    "新型",
    "含义",
    "抑制",
    "抑制剂",
    "激动剂",
    "治疗",
    "用于",
    "研究",
    "患者",
    "结果",
    "方法",
    "结论",
    "背景",
    "试验",
    "临床",
    "本发明",
    "实施例",
    "药物",
    "疾病",
    "靶点",
    "作用",
    "效果",
    "水平",
    "方案",
    "适应症",
    "评价",
    "联合",
    "联用",
    "高选择性",
    "国产",
}


def _has_zh_entity_shape(t: str) -> bool:
    # 长度下限取 3：中文药名/病名/靶点名几乎都 ≥ 3 字，
    # 而 2 字片段绝大多数是滑窗切出来的碎片（制剂、种新、联用）。
    if len(t) < 3 or len(t) > 12:
        return False
    if t in _ZH_GENERIC:
        return False
    if any(c in _ZH_FUNCTION_CHARS for c in t):
        return False
    # 通用词做前后缀时，剥掉后剩不下东西的也不是实体。
    core = t
    for g in _ZH_GENERIC:
        if core.startswith(g):
            core = core[len(g) :]
        if core.endswith(g):
            core = core[: -len(g)]
    return len(core) >= 2


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ZH_SPLIT_RE = re.compile(
    "|".join([*sorted(_ZH_GENERIC, key=len, reverse=True), f"[{''.join(_ZH_FUNCTION_CHARS)}]"])
)


def zh_segment_bounded(text: str, start: int, end: int) -> bool:
    """中文片段必须恰好是一个“去掉功能字与通用词后”的极大段。

    中文无词边界，滑窗子串本身不携带任何“这是一个词”的证据。
    取整段连续汉字太严（“国产药物泽布替尼亦在研”会整段被废），
    只看字数又太松；拿功能字和通用词做分隔符是两者之间唯一不需要分词器的代理信号。
    """
    lo, hi = start, end
    while lo > 0 and _CJK_RE.match(text[lo - 1]):
        lo -= 1
    while hi < len(text) and _CJK_RE.match(text[hi]):
        hi += 1
    run = text[lo:hi]
    target = text[start:end]
    offset, pos = 0, 0
    for piece in _ZH_SPLIT_RE.split(run):
        pos = run.index(piece, offset) if piece else pos
        if piece == target and lo + pos == start:
            return True
        offset = pos + max(len(piece), 1)
    return False


def maximal_spans(spans: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """只保留极大片段：被其他候选完全覆盖的子串不单独上报。

    滑窗天然会同时产出 '沃利替尼' 和 '沃利替'，两条都进队列
    等于让审校同一件事做两遍。
    """
    ordered = sorted(spans, key=lambda s: (-(s[2] - s[1]), s[1]))
    kept: list[tuple[str, int, int]] = []
    for text, start, end in ordered:
        if any(k[1] <= start and end <= k[2] for k in kept):
            continue
        kept.append((text, start, end))
    return sorted(kept, key=lambda s: s[1])


_CODE_SHAPE_RE = re.compile(r"^[A-Za-z]{2,6}[\s\-_]?\d{2,6}[A-Za-z]?$")


def _looks_like_code(text: str) -> bool:
    return bool(_CODE_SHAPE_RE.match(text.strip()))


_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "were",
    "was",
    "are",
    "has",
    "have",
    "had",
    "not",
    "but",
    "all",
    "can",
    "may",
    "our",
    "its",
    "into",
    "than",
    "then",
    "when",
    "which",
    "these",
    "those",
    "such",
    "also",
    "study",
    "patients",
    "results",
    "methods",
    "conclusion",
    "background",
}


def _dedupe_best(hits: list[CandidateHit]) -> list[CandidateHit]:
    best: dict[str, CandidateHit] = {}
    for h in hits:
        cur = best.get(h.concept_id)
        if cur is None or h.score > cur.score:
            best[h.concept_id] = h
    return sorted(best.values(), key=lambda h: (-h.score, h.concept_id))
