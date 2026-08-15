"""External biomedical sources 挂载扩展点（BIOS 常路径；UMLS 等后续按需）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "SOURCE_REGISTRY",
    "BiomedicalSource",
    "load_biomedical_source",
    "register_source",
]


@dataclass(frozen=True)
class BiomedicalSource:
    source_id: str
    description: str
    license: str
    graph_uri: str
    """目标 named graph（默认 graph/biomedical）。"""


class Loader(Protocol):
    def __call__(self, *, license_ack: str, **kwargs: object) -> dict: ...


SOURCE_REGISTRY: dict[str, BiomedicalSource] = {
    "bios_v3": BiomedicalSource(
        source_id="bios_v3",
        description="BIOS_v3 public biomedical KG — normal mount into GraphDB",
        license="CC-BY-NC-ND-4.0",
        graph_uri="http://asliva.example/graph/biomedical",
    ),
    "umls_subset": BiomedicalSource(
        source_id="umls_subset",
        description="UMLS subset by SAB — CUI as xref only, never enterprise PK",
        license="UMLS Metathesaurus License (per-SAB categories)",
        graph_uri="http://asliva.example/graph/biomedical",
    ),
    "hgnc": BiomedicalSource(
        source_id="hgnc",
        description="HGNC gene nomenclature — graph/biomedical + Target exact_match_xrefs",
        license="CC0",
        graph_uri="http://asliva.example/graph/biomedical",
    ),
}

_LOADERS: dict[str, Loader] = {}


def _load_umls_subset(*, license_ack: str, **kwargs: object) -> dict:
    ack = (license_ack or "").strip().lower()
    if ack not in {"poc", "evaluation", "licensed"}:
        raise PermissionError(
            "UMLS 子集需要 HMD_UMLS_LICENSE_ACK=poc|evaluation|licensed。"
            "CUI 只进 graph/biomedical 与 exact_match_xrefs，企业主键仍是 HMD:ENT:*。"
        )
    raise NotImplementedError(
        "umls_subset loader 尚未实现：接口已登记。"
        "全量 UMLS 须按 SAB 映射许可分层，不得整体当 TIER_2。"
    )


def register_source(source: BiomedicalSource, loader: Loader) -> None:
    SOURCE_REGISTRY[source.source_id] = source
    _LOADERS[source.source_id] = loader


def _load_hgnc(*, license_ack: str = "", **kwargs: object) -> dict:
    """官方下载可选；测试与 CI 只读 catalog / entities 已有 HGNC xref。不改 HMD:ENT:*。"""
    import yaml

    from biomed_ontology.foundation.paths import ENTITIES_PATH, ONTOLOGY_ROOT, REPO_ROOT

    cache = REPO_ROOT / "data" / "cache" / "hgnc" / "hgnc_complete_set.txt"
    downloaded = cache.is_file()
    xrefs: list[dict[str, str]] = []
    if ENTITIES_PATH.is_file():
        raw = yaml.safe_load(ENTITIES_PATH.read_text(encoding="utf-8")) or {}
        for ent in raw.get("entities") or []:
            eid = str(ent.get("enterprise_id") or "")
            for xref in ent.get("exact_match_xrefs") or []:
                if str(xref).startswith("HGNC:"):
                    xrefs.append(
                        {
                            "enterprise_id": eid,
                            "xref": str(xref),
                            "graph": "http://asliva.example/graph/biomedical",
                        }
                    )
    catalog = ONTOLOGY_ROOT / "catalog" / "targets.yaml"
    symbols: list[str] = []
    if catalog.is_file():
        raw = yaml.safe_load(catalog.read_text(encoding="utf-8")) or {}
        for concept in raw.get("concepts") or []:
            hint = (concept.get("xref_hints") or {}).get("HGNC") or {}
            if hint.get("value"):
                symbols.append(str(hint["value"]))
    return {
        "source_id": "hgnc",
        "graph_uri": SOURCE_REGISTRY["hgnc"].graph_uri,
        "concepts": len(symbols),
        "xrefs": xrefs,
        "downloaded": downloaded,
        "cache": str(cache) if downloaded else None,
        "enterprise_ids_unchanged": True,
        "license_ack": license_ack or "public",
        **{k: v for k, v in kwargs.items() if isinstance(v, str | int | bool)},
    }


register_source(SOURCE_REGISTRY["umls_subset"], _load_umls_subset)
register_source(SOURCE_REGISTRY["hgnc"], _load_hgnc)


def load_biomedical_source(
    source_id: str,
    *,
    license_ack: str,
    **kwargs: object,
) -> dict:
    """统一入口。BIOS 委托 foundation.bios；UMLS 等未实现则明确报错。"""
    if source_id not in SOURCE_REGISTRY:
        raise KeyError(f"未知 biomedical source: {source_id}. 已注册: {sorted(SOURCE_REGISTRY)}")
    if source_id == "bios_v3":
        return {"source_id": source_id, "result": "use hmd foundation bios-load", **kwargs}
    loader = _LOADERS.get(source_id)
    if loader is None:
        raise NotImplementedError(f"source {source_id} 已登记但 loader 未实现（预留扩展点）")
    return loader(license_ack=license_ack, **kwargs)
