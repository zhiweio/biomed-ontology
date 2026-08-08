"""运行时契约校验、许可闸门与消费正确性校验。

三件事共用一个模块，因为它们都是"返回体离开底座前的最后一道关"：

1. 契约校验 —— 用 LinkML 生成的 JSON Schema 双向校验，违约计数进指标
2. 许可闸门 —— tier 超出调用方权限的内容必须在此拦下（设计决策 D10）
3. 消费正确性 —— agent 声称引用的 doc_id 是否真在返回集内（citation_fidelity）

第 3 条容易被忽略：底座返回正确不代表 agent 用得正确，
不校验就无法区分"底座召回错了"和"agent 引用错了"，两者的修复动作完全不同。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.licensing import tier_rank

__all__ = [
    "ContractValidator",
    "LicenseGate",
    "LicenseLeak",
    "ValidationResult",
    "citation_fidelity",
]

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schema" / "generated"


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


class ContractValidator:
    """按类名校验 tool 输入输出。

    jsonschema 未安装时降级为"结构存在性检查"而非跳过校验 ——
    静默跳过会让契约合规率虚高到 100%，比没有校验更危险。
    """

    def __init__(self, schema_dir: Path | None = None) -> None:
        self.schema_dir = schema_dir or SCHEMA_DIR
        self._backend_available = _jsonschema_available()

    @property
    def strict(self) -> bool:
        return self._backend_available

    def validate(
        self, class_name: str, payload: dict[str, Any], *, schema: str = "hmd_agentapi"
    ) -> ValidationResult:
        doc = self._load(schema)
        defs = doc.get("$defs", {})
        if class_name not in defs:
            return ValidationResult(False, [f"契约中不存在类 {class_name}"])
        if not self._backend_available:
            return self._structural_check(defs[class_name], payload, class_name)

        import jsonschema

        sub = {**doc, **defs[class_name]}
        sub.pop("$defs", None)
        sub["$defs"] = defs
        validator = jsonschema.Draft202012Validator(sub)
        errors = [
            f"{'/'.join(str(p) for p in e.absolute_path) or class_name}: {e.message}"
            for e in validator.iter_errors(payload)
        ]
        return ValidationResult(not errors, errors)

    def _structural_check(
        self, cls_schema: dict[str, Any], payload: dict[str, Any], class_name: str
    ) -> ValidationResult:
        errors = [
            f"{class_name}: 缺少必填字段 {r}"
            for r in cls_schema.get("required", [])
            if payload.get(r) is None
        ]
        return ValidationResult(not errors, errors)

    @lru_cache(maxsize=8)  # noqa: B019
    def _load(self, schema: str) -> dict[str, Any]:
        path = self.schema_dir / f"{schema}.schema.json"
        if not path.exists():
            raise FileNotFoundError(f"契约未生成：{path}，请先执行 task gen")
        return json.loads(path.read_text(encoding="utf-8"))


def _jsonschema_available() -> bool:
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------- 许可闸门


class LicenseLeak(RuntimeError):
    """检出许可泄漏。按告警规则这是 P0，因此用异常而不是返回码 —— 不允许被忽略。"""


@dataclass
class GateResult:
    kept: list[Any]
    filtered_count: int
    max_tier: LicenseTierEnum


class LicenseGate:
    """按调用方权限过滤返回体。

    过滤发生在返回体组装的最后一步而不是查询条件里，是刻意的：
    查询层过滤会让"因许可被挡掉了 N 条"这个事实彻底不可见，
    而这个计数本身是合规审计与采购 ROI 论证的依据。
    """

    def __init__(self, entitlements: frozenset[str] = frozenset()) -> None:
        self.entitlements = entitlements

    def visible_tier(self, source_id: str | None, tier: LicenseTierEnum) -> bool:
        if tier_rank(tier) <= tier_rank(LicenseTierEnum.TIER_1):
            return True
        return source_id is not None and source_id in self.entitlements

    def filter(self, items: list[Any], *, tier_of, source_of=lambda _: None) -> GateResult:
        kept, max_tier = [], LicenseTierEnum.TIER_0
        for item in items:
            tier = tier_of(item)
            if self.visible_tier(source_of(item), tier):
                kept.append(item)
                if tier_rank(tier) > tier_rank(max_tier):
                    max_tier = tier
        return GateResult(kept, len(items) - len(kept), max_tier)

    def assert_no_leak(self, items: list[Any], *, tier_of, source_of=lambda _: None) -> None:
        leaked = [item for item in items if not self.visible_tier(source_of(item), tier_of(item))]
        if leaked:
            raise LicenseLeak(f"返回体中检出 {len(leaked)} 条超出调用方权限的内容")


# ---------------------------------------------------------------- 消费正确性


def citation_fidelity(
    claimed: list[tuple[str, str | None]],
    returned_docs: dict[str, set[str]],
) -> float:
    """agent 引用忠实度。

    claimed: [(doc_id, 声称该文档支持的 concept_id 或 None)]
    returned_docs: doc_id → 该文档实际带的 concept_id 集合

    同时校验两件事：文档是否真在返回集内、文档是否真含声称的概念。
    只查前者会放过"引用了正确文档但归因到错误概念"这类最难发现的错误。
    """
    if not claimed:
        return 1.0
    ok = 0
    for doc_id, concept_id in claimed:
        if doc_id not in returned_docs:
            continue
        if concept_id is None or concept_id in returned_docs[doc_id]:
            ok += 1
    return ok / len(claimed)
