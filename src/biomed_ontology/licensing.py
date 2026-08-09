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
    "COMPONENTS",
    "POLICIES",
    "ComponentObligation",
    "LicenseTierEnum",
    "LicenseViolation",
    "TierPolicy",
    "assert_component_cleared",
    "assert_exportable",
    "is_exportable",
    "is_trainable",
    "max_visible_tier",
    "named_graph_uri",
    "policy_for",
    "uncleared_components",
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


# ---------------------------------------------------------------- 第三方组件
#
# 上面管的是**数据**的许可，这里管的是**软件**的许可。两者不能混进同一张表：
# 数据源有实体类型、权威范围、命名图，解析器一个都没有。
#
# 之所以把软件许可也写成可执行的闸门而非只写进 NOTICE：法务义务如果只存在于
# 文档里，就只有写它的人知道；写成启动时抛异常，才能保证换人接手时也绕不过去。


@dataclass(frozen=True)
class ComponentObligation:
    component_id: str
    license_id: str
    obligation: str
    review: str
    """not_required / pending / cleared —— pending 表示义务已识别但法务尚未结论。"""


COMPONENTS: dict[str, ComponentObligation] = {
    "pymupdf4llm": ComponentObligation(
        component_id="pymupdf4llm",
        license_id="AGPL-3.0 / 商业双授权（底层 PyMuPDF）",
        obligation=(
            "PyMuPDF4LLM 依赖 PyMuPDF：内部工具用途通常无碍；"
            "对外提供服务需 Artifex 商业许可，或改走 Docling / 其他非 AGPL 路径。"
        ),
        review="pending",
    ),
    "docling": ComponentObligation(
        component_id="docling",
        license_id="MIT",
        obligation="保留版权与许可声明；模型权重若另有条款须单独登记。",
        review="pending",
    ),
    "mineru": ComponentObligation(
        component_id="mineru",
        license_id="MinerU Open Source License (Apache-2.0 + 附加条款)",
        obligation=(
            "月活 > 1 亿或月总收入 > 2000 万美元（与关联方合并计算）须另行取得商业许可；"
            "对第三方提供在线服务须显著标明使用了 MinerU。阿斯利华的收入规模可能触及门槛。"
        ),
        review="pending",
    ),
    "knowhere": ComponentObligation(
        component_id="knowhere",
        license_id="Apache-2.0",
        obligation="保留许可与版权声明，并标注已作出的修改（见 NOTICE）。",
        review="cleared",
    ),
    # 权重是 MIT，看起来最宽松的一条 —— 但模型卡另有一句独立于许可证的声明：
    # "Any deployed use case --- commercial or otherwise --- is currently out of scope"。
    # 许可证给的是版权层面的许可，这句话是发布方对**用途**的限定，两者不互相覆盖。
    # 只把 MIT 记进来会让这个组件在依赖清单上显得干干净净，而真正的风险在别处。
    "biomedclip": ComponentObligation(
        component_id="biomedclip",
        license_id="MIT（权重）+ 模型卡用途限定",
        obligation=(
            "模型卡声明「任何部署用途（无论商用与否）当前均超出适用范围」，"
            "仅供研究使用；且明确未在临床场景验证，不得用于诊断或治疗决策。"
            "研究性 PoC 与内部评测不受影响，对外提供服务前须法务与医学事务共同结论。"
        ),
        review="pending",
    ),
}


def assert_component_cleared(component_id: str, *, accept_uncleared: bool = False) -> None:
    """启用第三方组件前的法务闸门。

    `accept_uncleared` 只应由配置（HMD_ACCEPT_UNCLEARED_COMPONENTS，PoC 默认 true）驱动，
    且会在启动告警里留痕 —— 允许本地试用，但不允许无声地带进生产。
    """
    ob = COMPONENTS.get(component_id)
    if ob is None or ob.review == "cleared" or ob.review == "not_required":
        return
    if accept_uncleared:
        return
    raise LicenseViolation(
        f"组件 {component_id} 的许可义务尚未经法务结论（{ob.license_id}）：{ob.obligation} "
        f"完成核实后把 COMPONENTS['{component_id}'].review 改为 'cleared'；"
        f"仅本地试用可设 HMD_ACCEPT_UNCLEARED_COMPONENTS=true。"
    )


def uncleared_components() -> list[ComponentObligation]:
    return sorted(
        (c for c in COMPONENTS.values() if c.review == "pending"),
        key=lambda c: c.component_id,
    )
