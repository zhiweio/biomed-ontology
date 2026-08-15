"""入仓 / sync 前探活。失败要大声，禁止静默开跑。"""

from __future__ import annotations

from typing import Any

__all__ = ["probe_foundation", "probe_ingest"]


def probe_milvus() -> None:
    from pymilvus import MilvusClient

    from biomed_ontology.config import settings

    client = MilvusClient(uri=settings.milvus_uri)
    _ = client.list_collections()


def probe_graphdb() -> None:
    import httpx

    from biomed_ontology.config import settings

    url = settings.graphdb_url.rstrip("/")
    r = httpx.get(f"{url}/rest/repositories", timeout=10.0)
    if not (200 <= r.status_code < 300):
        raise RuntimeError(f"graphdb HTTP {r.status_code}")


def probe_iceberg() -> None:
    from biomed_ontology.lake.catalog import open_catalog

    open_catalog()


def probe_ingest(*, bern2_url: str | None = None) -> dict[str, Any]:
    """BERN2 + Iceberg + Milvus + GraphDB。任一失败则 raise。"""
    from biomed_ontology.lake.steps import require_bern2

    url = require_bern2(bern2_url)
    probe_iceberg()
    probe_milvus()
    probe_graphdb()
    return {"bern2": url, "iceberg": True, "milvus": True, "graphdb": True}


def probe_foundation() -> dict[str, Any]:
    """GraphDB + Milvus（OM 慢启动不阻断）。"""
    probe_milvus()
    probe_graphdb()
    return {"milvus": True, "graphdb": True}
