"""双层身份：Enterprise Entity ID vs External Concept ID。

Enterprise Ontology ID 是对外语义锚点。
BIOS / ChEBI / HGNC / DrugBank 等只作为 External Concept，经 exactMatch 挂接。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "KIND_SEGMENT",
    "EnterpriseId",
    "EnterpriseKind",
    "ExternalId",
    "is_enterprise_id",
    "is_external_id",
    "mint_enterprise_id",
    "normalize_alias_key",
]

_ENT_RE = re.compile(r"^HMD:ENT:(DC|PRG|TGT|IND|EXP|PUB|ASY|CMP|BMK):[A-Za-z0-9_-]+$")
_EXT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*:\S+$")


class EnterpriseKind(str, Enum):
    DrugCandidate = "DrugCandidate"
    Program = "Program"
    Target = "Target"
    Indication = "Indication"
    Experiment = "Experiment"
    Publication = "Publication"
    Assay = "Assay"
    Compound = "Compound"
    Biomarker = "Biomarker"


KIND_SEGMENT: dict[EnterpriseKind, str] = {
    EnterpriseKind.DrugCandidate: "DC",
    EnterpriseKind.Program: "PRG",
    EnterpriseKind.Target: "TGT",
    EnterpriseKind.Indication: "IND",
    EnterpriseKind.Experiment: "EXP",
    EnterpriseKind.Publication: "PUB",
    EnterpriseKind.Assay: "ASY",
    EnterpriseKind.Compound: "CMP",
    EnterpriseKind.Biomarker: "BMK",
}

_SEGMENT_KIND = {v: k for k, v in KIND_SEGMENT.items()}


@dataclass(frozen=True)
class EnterpriseId:
    value: str

    def __post_init__(self) -> None:
        if not _ENT_RE.match(self.value):
            raise ValueError(f"非法企业实体 ID：{self.value!r}")

    @property
    def kind(self) -> EnterpriseKind:
        seg = self.value.split(":")[2]
        return _SEGMENT_KIND[seg]

    @property
    def local(self) -> str:
        return self.value.rsplit(":", 1)[-1]

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ExternalId:
    """外部标准概念 ID（含 BIOS 概念 URI 的 CURIE 形态）。"""

    value: str

    def __post_init__(self) -> None:
        if not _EXT_RE.match(self.value):
            raise ValueError(f"非法外部概念 ID：{self.value!r}")
        if self.value.startswith("HMD:ENT:"):
            raise ValueError("Enterprise ID 不得当作 ExternalId")

    @property
    def prefix(self) -> str:
        return self.value.split(":", 1)[0]

    def __str__(self) -> str:
        return self.value


def mint_enterprise_id(kind: EnterpriseKind, local: str) -> EnterpriseId:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", local.strip()).strip("_")
    if not slug:
        raise ValueError("enterprise local id 不能为空")
    return EnterpriseId(f"HMD:ENT:{KIND_SEGMENT[kind]}:{slug}")


def is_enterprise_id(value: str) -> bool:
    return bool(_ENT_RE.match(value))


def is_external_id(value: str) -> bool:
    return bool(_EXT_RE.match(value)) and not value.startswith("HMD:ENT:")


def normalize_alias_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())
