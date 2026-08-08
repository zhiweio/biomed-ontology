"""三层身份：Enterprise Entity / External Concept / Evidence。

① Enterprise Ontology ID 是对外语义锚点（Graph / Milvus / API 主键）。
② BIOS / ChEBI / HGNC / DrugBank 等只作为 External Concept，经 exactMatch 挂接。
③ Evidence ID 锚定出处（pubmed / patent / eln / lims / ev），不是企业实体主键。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "KIND_SEGMENT",
    "EnterpriseId",
    "EnterpriseKind",
    "EvidenceId",
    "ExternalId",
    "is_enterprise_id",
    "is_evidence_id",
    "is_external_id",
    "mint_enterprise_id",
    "normalize_alias_key",
    "normalize_evidence_id",
]

_ENT_RE = re.compile(r"^HMD:ENT:(DC|PRG|TGT|IND|EXP|PUB|ASY|CMP|BMK):[A-Za-z0-9_-]+$")
_EXT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*:\S+$")
# pubmed: / patent: / eln: / lims: / ev:…（Evidence Index 条目与文献/资产出处）
_EVIDENCE_RE = re.compile(
    r"^(?:pubmed|patent|eln|lims|ev):[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    re.IGNORECASE,
)
_PMID_RE = re.compile(r"^(?:PMID|pmid)[:\s]?(\d+)$")
_EXP_RE = re.compile(r"^(?:ELN:)?(EXP-[A-Za-z0-9_-]+)$", re.IGNORECASE)
_ASY_RE = re.compile(r"^(?:LIMS:)?(ASY-[A-Za-z0-9_-]+)$", re.IGNORECASE)
_PATENT_RE = re.compile(r"^(?:patent:)?((?:US|WO|EP|CN)\d[\w/-]*)$", re.IGNORECASE)


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
        if is_evidence_id(self.value):
            raise ValueError("Evidence ID 不得当作 ExternalId")

    @property
    def prefix(self) -> str:
        return self.value.split(":", 1)[0]

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class EvidenceId:
    """证据 / 出处 ID（pubmed / patent / eln / lims / ev）。"""

    value: str

    def __post_init__(self) -> None:
        normalized = normalize_evidence_id(self.value)
        if normalized is None or not _EVIDENCE_RE.match(normalized):
            raise ValueError(f"非法证据 ID：{self.value!r}")
        object.__setattr__(self, "value", normalized)

    @property
    def scheme(self) -> str:
        return self.value.split(":", 1)[0].lower()

    @property
    def local(self) -> str:
        return self.value.split(":", 1)[1]

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
    return (
        bool(_EXT_RE.match(value))
        and not value.startswith("HMD:ENT:")
        and not is_evidence_id(value)
    )


def is_evidence_id(value: str) -> bool:
    return normalize_evidence_id(value) is not None


def normalize_evidence_id(value: str) -> str | None:
    """将常见出处写法规范为 Evidence ID；无法识别则返回 None。"""
    text = value.strip()
    if not text:
        return None
    if _EVIDENCE_RE.match(text):
        scheme, rest = text.split(":", 1)
        return f"{scheme.lower()}:{rest}"

    m = _PMID_RE.match(text)
    if m:
        return f"pubmed:{m.group(1)}"

    m = _EXP_RE.match(text)
    if m:
        return f"eln:{m.group(1).upper() if m.group(1).upper().startswith('EXP-') else m.group(1)}"

    m = _ASY_RE.match(text)
    if m:
        return f"lims:{m.group(1).upper() if m.group(1).upper().startswith('ASY-') else m.group(1)}"

    m = _PATENT_RE.match(text)
    if m:
        return f"patent:{m.group(1).upper()}"

    return None


def normalize_alias_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())
