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
    """目标 named graph（默认 graph:biomedical）。"""


class Loader(Protocol):
    def __call__(self, *, license_ack: str, **kwargs: object) -> dict: ...


SOURCE_REGISTRY: dict[str, BiomedicalSource] = {
    "bios_v3": BiomedicalSource(
        source_id="bios_v3",
        description="BIOS_v3 public biomedical KG — normal mount into GraphDB",
        license="CC-BY-NC-ND-4.0",
        graph_uri="http://asliva.com/graph/biomedical",
    ),
    # 后续：umls_subset_x = BiomedicalSource(...)
}

_LOADERS: dict[str, Loader] = {}


def register_source(source: BiomedicalSource, loader: Loader) -> None:
    SOURCE_REGISTRY[source.source_id] = source
    _LOADERS[source.source_id] = loader


def load_biomedical_source(
    source_id: str,
    *,
    license_ack: str,
    **kwargs: object,
) -> dict:
    """统一入口。BIOS 委托 foundation.bios；UMLS 等未注册则明确报错。"""
    if source_id not in SOURCE_REGISTRY:
        raise KeyError(
            f"未知 biomedical source: {source_id}. "
            f"已注册: {sorted(SOURCE_REGISTRY)}（UMLS 子集后续按需 register_source）"
        )
    if source_id == "bios_v3":
        return {"source_id": source_id, "result": "use hmd foundation bios-load", **kwargs}
    loader = _LOADERS.get(source_id)
    if loader is None:
        raise NotImplementedError(f"source {source_id} 已登记但 loader 未实现（预留扩展点）")
    return loader(license_ack=license_ack, **kwargs)
