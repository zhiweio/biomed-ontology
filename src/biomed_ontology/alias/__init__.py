"""别名归一化与 scope 驱动的检索扩展（设计决策 D2）。

alias_norm 是词典精确匹配的唯一键。归一化必须是确定性、幂等、可离线复算的 ——
索引侧和查询侧用的是同一个函数，任何不一致都会直接变成召回丢失。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from biomed_ontology._generated.hmd_concept import SynonymScopeEnum

__all__ = [
    "SCOPE_INDEXED",
    "SCOPE_WEIGHTS",
    "ExpansionTerm",
    "expansion_weight",
    "generate_code_variants",
    "is_indexed",
    "normalize_alias",
]

# 希腊字母在生物医药文本里既写符号也写英文（PI3Kδ / PI3K-delta / PI3Kdelta），
# 不统一转写就会漏召回。
_GREEK = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "ε": "epsilon",
    "ζ": "zeta",
    "η": "eta",
    "θ": "theta",
    "ι": "iota",
    "κ": "kappa",
    "λ": "lambda",
    "μ": "mu",
    "ν": "nu",
    "ξ": "xi",
    "ο": "omicron",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "τ": "tau",
    "υ": "upsilon",
    "φ": "phi",
    "χ": "chi",
    "ψ": "psi",
    "ω": "omega",
    "Α": "alpha",
    "Β": "beta",
    "Γ": "gamma",
    "Δ": "delta",
    "Ε": "epsilon",
    "Ζ": "zeta",
    "Η": "eta",
    "Θ": "theta",
    "Ι": "iota",
    "Κ": "kappa",
    "Λ": "lambda",
    "Μ": "mu",
    "Ν": "nu",
    "Ξ": "xi",
    "Ο": "omicron",
    "Π": "pi",
    "Ρ": "rho",
    "Σ": "sigma",
    "Τ": "tau",
    "Υ": "upsilon",
    "Φ": "phi",
    "Χ": "chi",
    "Ψ": "psi",
    "Ω": "omega",
}

_GREEK_RE = re.compile("|".join(map(re.escape, _GREEK)))
_SEPARATORS_RE = re.compile(r"[\s\u3000\-\u2010-\u2015_/\\.,;:()\[\]{}'\"·、，。（）【】]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

# HMPL-504 / HMPL504 / HMPL 504 必须归一到同一个键。
# 研发代号是内部检索里最高频的入口，也是最容易因写法差异丢召回的地方。
_CODE_RE = re.compile(r"^([A-Za-z]{2,6})[\s\-_]?(\d{2,6})([A-Za-z]?)$")


def normalize_alias(text: str) -> str:
    """计算词典匹配键。

    步骤顺序不可调换：NFKC 先把全角转半角，希腊字母转写要在去分隔符之前
    （否则 PI3K-δ 会先粘成 PI3Kδ 再转写，结果与 PI3K-delta 不一致）。
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text).strip()
    s = _GREEK_RE.sub(lambda m: _GREEK[m.group()], s)
    s = s.casefold()
    s = _SEPARATORS_RE.sub("", s)
    return s


def contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


# ---------------------------------------------------------------- scope 语义

SCOPE_WEIGHTS: dict[SynonymScopeEnum, float] = {
    SynonymScopeEnum.EXACT: 1.0,
    SynonymScopeEnum.NARROW: 0.8,
    SynonymScopeEnum.BROAD: 0.0,
    SynonymScopeEnum.RELATED: 0.0,
}

SCOPE_INDEXED: dict[SynonymScopeEnum, bool] = {
    SynonymScopeEnum.EXACT: True,
    SynonymScopeEnum.NARROW: True,
    SynonymScopeEnum.BROAD: False,
    SynonymScopeEnum.RELATED: False,
}


def expansion_weight(scope: SynonymScopeEnum) -> float:
    return SCOPE_WEIGHTS[scope]


def is_indexed(scope: SynonymScopeEnum) -> bool:
    """broad 与 related 不入索引。

    把 related 当等价扩展会让「MET 抑制剂」召回所有激酶抑制剂文献 ——
    精确率的损失远大于召回率的收益。
    """
    return SCOPE_INDEXED[scope]


@dataclass(frozen=True)
class ExpansionTerm:
    """一个查询扩展词及其权重，直接对应 OpenSearch 的 boost。"""

    term: str
    weight: float
    concept_id: str
    alias_id: str
    scope: SynonymScopeEnum


# ---------------------------------------------------------------- 变体生成


def generate_code_variants(alias: str) -> set[str]:
    """为研发代号生成写法变体。

    只对匹配代号模式的输入生效 —— 对普通词做变体扩展会引入噪声。
    """
    m = _CODE_RE.match(alias.strip())
    if not m:
        return set()
    letters, digits, suffix = m.groups()
    variants = {
        f"{letters}{digits}{suffix}",
        f"{letters}-{digits}{suffix}",
        f"{letters} {digits}{suffix}",
    }
    return {v for v in variants if v.casefold() != alias.strip().casefold()}
