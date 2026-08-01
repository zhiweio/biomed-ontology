"""许可分层策略（设计决策 D10）。

tier 不是标签，是四个执行点上的强制约束：
  1. RDF named graph 隔离
  2. 查询重写时的可见性过滤
  3. 导出闸门
  4. 训练语料准入

策略集中在此模块，避免各处散落硬编码的 tier 判断 ——
许可规则变更时只有一个地方要改。
"""

from __future__ import annotations

from dataclasses import dataclass

from biomed_ontology._generated.hmd_concept import LicenseTierEnum

__all__ = [
    "POLICIES",
    "LicenseTierEnum",
    "LicenseViolation",
    "TierPolicy",
    "assert_exportable",
    "is_exportable",
    "is_trainable",
    "max_visible_tier",
    "named_graph_uri",
    "policy_for",
]

NAMED_GRAPH_BASE = "https://w3id.org/asliva/biomed-ontology/graph"

_TIER_ORDER: dict[LicenseTierEnum, int] = {
    LicenseTierEnum.TIER_0: 0,
    LicenseTierEnum.TIER_1: 1,
    LicenseTierEnum.TIER_2: 2,
    LicenseTierEnum.TIER_3: 3,
}


@dataclass(frozen=True)
class TierPolicy:
    """单个 tier 的准入规则。"""

    tier: LicenseTierEnum
    exportable: bool
    """能否出现在对外导出物中（报告、数据包、给外部系统的返回体）。"""

    trainable: bool
    """能否进入模型微调 / 向量训练语料。"""

    requires_attribution: bool
    """分发时是否必须标注来源。"""

    share_alike: bool
    """衍生物是否被要求同源共享 —— 这会传染到整个衍生数据集。"""

    requires_entitlement: bool
    """调用方是否必须持有对应订阅凭据才能看到内容。"""

    description: str


POLICIES: dict[LicenseTierEnum, TierPolicy] = {
    LicenseTierEnum.TIER_0: TierPolicy(
        tier=LicenseTierEnum.TIER_0,
        exportable=True,
        trainable=True,
        requires_attribution=False,
        share_alike=False,
        requires_entitlement=False,
        description="完全开放：MONDO / HGNC / UNII / Wikidata",
    ),
    LicenseTierEnum.TIER_1: TierPolicy(
        tier=LicenseTierEnum.TIER_1,
        exportable=True,
        trainable=False,
        requires_attribution=True,
        share_alike=True,
        requires_entitlement=False,
        description="署名或同源共享：ChEMBL CC-BY-SA、DrugCentral",
    ),
    LicenseTierEnum.TIER_2: TierPolicy(
        tier=LicenseTierEnum.TIER_2,
        exportable=False,
        trainable=False,
        requires_attribution=True,
        share_alike=False,
        requires_entitlement=True,
        description="需订阅、内部可用：UMLS 受限源、DrugBank",
    ),
    LicenseTierEnum.TIER_3: TierPolicy(
        tier=LicenseTierEnum.TIER_3,
        exportable=False,
        trainable=False,
        requires_attribution=True,
        share_alike=False,
        requires_entitlement=True,
        description="严格受限：MedDRA、智慧芽 / 医药魔方原始记录",
    ),
}


class LicenseViolation(RuntimeError):
    """许可越界。触发即为 P0 合规事件，不做降级处理。"""


def policy_for(tier: LicenseTierEnum) -> TierPolicy:
    return POLICIES[tier]


def named_graph_uri(source_id: str, tier: LicenseTierEnum) -> str:
    """每个源一个命名图，tier 编进图 URI。

    把 tier 放进图 URI 而不是只做属性，是为了让 SPARQL 侧能用 FROM NAMED
    直接做集合级过滤，而不必对每条三元组做属性判断。
    """
    return f"{NAMED_GRAPH_BASE}/{tier.value.lower()}/{source_id.lower()}"


def max_visible_tier(entitlements: frozenset[str]) -> LicenseTierEnum:
    """根据调用方持有的凭据算出可见的最高 tier。

    凭据用源 ID 表示（如 {"UMLS", "MEDDRA"}）。
    无凭据者只能看 TIER_1 及以下 —— TIER_1 内部可读，只是分发受限。
    """
    if not entitlements:
        return LicenseTierEnum.TIER_1
    return LicenseTierEnum.TIER_3


def is_exportable(tier: LicenseTierEnum) -> bool:
    return POLICIES[tier].exportable


def is_trainable(tier: LicenseTierEnum) -> bool:
    return POLICIES[tier].trainable


def tier_rank(tier: LicenseTierEnum) -> int:
    return _TIER_ORDER[tier]


def assert_exportable(tier: LicenseTierEnum, *, what: str) -> None:
    """导出闸门。任何离开系统边界的内容都必须过这道检查。"""
    if not is_exportable(tier):
        raise LicenseViolation(
            f"{what} 属于 {tier.value}（{POLICIES[tier].description}），禁止导出"
        )
