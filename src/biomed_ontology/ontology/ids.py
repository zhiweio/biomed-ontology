"""内部 CURIE 分配（设计决策 D1）。

主键必须是内部 ID，不能是外部本体 ID —— MONDO / ChEMBL 的 obsolete 与 merge 是常态，
拿外部 ID 当主键会让下游索引、已标注语料和历史报告一起失效。

三条不变量，由 ledger 强制保证：
  1. ID 单调递增，永不复用（哪怕概念被废弃）
  2. 重复构建产出相同 ID（离线可重放的前提）
  3. 概念合并不删 ID，用 replaced_by 指向继任者
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Self

from biomed_ontology._generated.hmd_concept import EntityTypeEnum

__all__ = [
    "ID_SEGMENTS",
    "Allocation",
    "IdLedger",
    "MintAction",
    "MintResult",
    "SequenceLedger",
]

# 段名与 schema 中 EntityTypeEnum 的 id_segment 注解一致。
# 改这里必须同步改 schema，否则已发布的 ID 会失去语义。
ID_SEGMENTS: dict[EntityTypeEnum, str] = {
    EntityTypeEnum.TARGET: "TGT",
    EntityTypeEnum.SUBSTANCE: "SUB",
    EntityTypeEnum.DISEASE: "DIS",
    EntityTypeEnum.MECHANISM: "MOA",
    EntityTypeEnum.MODALITY: "MOD",
    EntityTypeEnum.TRIAL: "TRL",
    EntityTypeEnum.BIOMARKER: "BMK",
    EntityTypeEnum.ADVERSE_EVENT: "AE",
}

_ID_WIDTH = 7


class MintAction(str, Enum):
    CREATED = "created"
    """全新概念。"""

    REUSED = "reused"
    """命中已有分配，ID 不变。重复构建时应当全是这一类。"""

    EXTENDED = "extended"
    """已有概念吸收了新成员（外部源新增了等价 ID）。"""

    MERGED = "merged"
    """两个已有概念被判定为同一个。存活者保留 ID，其余标 replaced_by。"""


@dataclass(frozen=True)
class MintResult:
    concept_id: str
    action: MintAction
    obsoleted: tuple[str, ...] = ()
    """本次合并中被废弃的 concept_id。"""

    notes: str | None = None


@dataclass
class Allocation:
    """一条 ID 分配记录。"""

    concept_id: str
    entity_type: EntityTypeEnum
    members: set[str]
    created_in_release: str
    modified_in_release: str
    is_obsolete: bool = False
    replaced_by: str | None = None
    consider: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "concept_id": self.concept_id,
            "entity_type": self.entity_type.value,
            "members": sorted(self.members),
            "created_in_release": self.created_in_release,
            "modified_in_release": self.modified_in_release,
            "is_obsolete": self.is_obsolete,
            "replaced_by": self.replaced_by,
            "consider": sorted(self.consider),
        }

    @classmethod
    def from_json(cls, d: dict) -> Self:
        return cls(
            concept_id=d["concept_id"],
            entity_type=EntityTypeEnum(d["entity_type"]),
            members=set(d["members"]),
            created_in_release=d["created_in_release"],
            modified_in_release=d["modified_in_release"],
            is_obsolete=d.get("is_obsolete", False),
            replaced_by=d.get("replaced_by"),
            consider=list(d.get("consider", [])),
        )


class IdLedger:
    """ID 分配账本。

    账本是构建产物中唯一必须持久化且纳入版本控制的状态 ——
    丢了它就无法保证重建后 ID 不变。
    """

    def __init__(self, path: Path, *, release: str) -> None:
        self.path = path
        self.release = release
        self._lock = threading.Lock()
        self._allocations: dict[str, Allocation] = {}
        self._member_index: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        if path.exists():
            self._load()

    # ------------------------------------------------------------------ 持久化

    def _load(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._counters = dict(raw.get("counters", {}))
        for d in raw.get("allocations", []):
            alloc = Allocation.from_json(d)
            self._allocations[alloc.concept_id] = alloc
            for m in alloc.members:
                self._member_index[m] = alloc.concept_id

    def save(self) -> None:
        payload = {
            "release": self.release,
            "counters": dict(sorted(self._counters.items())),
            "allocations": [self._allocations[cid].to_json() for cid in sorted(self._allocations)],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    # ------------------------------------------------------------------ 查询

    def __len__(self) -> int:
        return len(self._allocations)

    def get(self, concept_id: str) -> Allocation | None:
        return self._allocations.get(concept_id)

    def lookup(self, xref: str) -> str | None:
        """外部 ID → 内部 concept_id。"""
        return self._member_index.get(xref)

    def resolve(self, concept_id: str) -> str:
        """跟随 replaced_by 链，返回当前有效的 concept_id。

        历史报告里的旧 ID 要能解析到今天的概念，否则溯源会断。
        """
        seen: set[str] = set()
        current = concept_id
        while True:
            alloc = self._allocations.get(current)
            if alloc is None or not alloc.is_obsolete or alloc.replaced_by is None:
                return current
            if current in seen:
                raise ValueError(f"replaced_by 成环: {current}")
            seen.add(current)
            current = alloc.replaced_by

    def active(self) -> list[Allocation]:
        return [a for a in self._allocations.values() if not a.is_obsolete]

    # ------------------------------------------------------------------ 分配

    def _next_id(self, entity_type: EntityTypeEnum) -> str:
        segment = ID_SEGMENTS[entity_type]
        nxt = self._counters.get(segment, 0) + 1
        self._counters[segment] = nxt
        return f"HMD:{segment}:{nxt:0{_ID_WIDTH}d}"

    def mint(self, entity_type: EntityTypeEnum, members: set[str]) -> MintResult:
        """为一个等价团分配或复用 ID。

        判定逻辑按命中的已有概念数分三种情况：
          0 个 → 新建
          1 个 → 复用（成员有增补则记为 extended）
          N 个 → 该团把原本独立的 N 个概念连通了，执行合并
        """
        if not members:
            raise ValueError("等价团不能为空")

        with self._lock:
            hits: list[str] = []
            for m in sorted(members):
                cid = self._member_index.get(m)
                if cid is not None:
                    cid = self.resolve(cid)
                    if cid not in hits:
                        hits.append(cid)

            if not hits:
                return self._create(entity_type, members)
            if len(hits) == 1:
                return self._extend(hits[0], members)
            return self._merge(hits, members)

    def _create(self, entity_type: EntityTypeEnum, members: set[str]) -> MintResult:
        concept_id = self._next_id(entity_type)
        self._allocations[concept_id] = Allocation(
            concept_id=concept_id,
            entity_type=entity_type,
            members=set(members),
            created_in_release=self.release,
            modified_in_release=self.release,
        )
        for m in members:
            self._member_index[m] = concept_id
        return MintResult(concept_id=concept_id, action=MintAction.CREATED)

    def _extend(self, concept_id: str, members: set[str]) -> MintResult:
        alloc = self._allocations[concept_id]
        new_members = members - alloc.members
        if not new_members:
            return MintResult(concept_id=concept_id, action=MintAction.REUSED)
        alloc.members |= new_members
        alloc.modified_in_release = self.release
        for m in new_members:
            self._member_index[m] = concept_id
        return MintResult(
            concept_id=concept_id,
            action=MintAction.EXTENDED,
            notes=f"新增成员 {sorted(new_members)}",
        )

    def _merge(self, hits: list[str], members: set[str]) -> MintResult:
        """合并时保留最早分配的 ID —— 它在下游被引用得最久，改动代价最大。"""
        survivor = min(hits)
        losers = [h for h in hits if h != survivor]
        alloc = self._allocations[survivor]

        for loser_id in losers:
            loser = self._allocations[loser_id]
            alloc.members |= loser.members
            loser.is_obsolete = True
            loser.replaced_by = survivor
            loser.members = set()
            loser.modified_in_release = self.release

        alloc.members |= members
        alloc.modified_in_release = self.release
        for m in alloc.members:
            self._member_index[m] = survivor

        return MintResult(
            concept_id=survivor,
            action=MintAction.MERGED,
            obsoleted=tuple(losers),
            notes=f"合并 {losers} 入 {survivor}",
        )

    def obsolete(
        self, concept_id: str, *, replaced_by: str | None = None, consider: list[str] | None = None
    ) -> None:
        """废弃概念。ID 保留在账本中，永不回收。"""
        alloc = self._allocations[concept_id]
        alloc.is_obsolete = True
        alloc.replaced_by = replaced_by
        alloc.consider = consider or []
        alloc.modified_in_release = self.release
        for m in alloc.members:
            if replaced_by is not None:
                self._member_index[m] = replaced_by
            else:
                self._member_index.pop(m, None)
        if replaced_by is not None:
            self._allocations[replaced_by].members |= alloc.members
        alloc.members = set()


class SequenceLedger:
    """按稳定键分配顺序 ID，用于别名与映射条目。

    别名 ID 必须稳定，因为可观测埋点回传的就是它 ——
    如果重建后 ID 漂移，历史 trace 就再也定位不到具体那一行别名。
    """

    def __init__(self, path: Path, *, prefix: str, width: int = 9) -> None:
        self.path = path
        self.prefix = prefix
        self.width = width
        self._assigned: dict[str, str] = {}
        self._counter = 0
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._counter = raw["counter"]
            self._assigned = raw["assigned"]

    def __len__(self) -> int:
        return len(self._assigned)

    def assign(self, key: str) -> str:
        existing = self._assigned.get(key)
        if existing is not None:
            return existing
        self._counter += 1
        value = f"{self.prefix}:{self._counter:0{self.width}d}"
        self._assigned[key] = value
        return value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "prefix": self.prefix,
            "counter": self._counter,
            "assigned": dict(sorted(self._assigned.items())),
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
