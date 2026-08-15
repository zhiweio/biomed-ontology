"""OpenMetadata：Trino/Iceberg 治理、文档 Asset、跨系统血缘。"""

from __future__ import annotations

import json
from typing import Any

from biomed_ontology.config import settings
from biomed_ontology.foundation.catalog import OpenMetadataClient
from biomed_ontology.foundation.models import AssetHit

__all__ = [
    "ensure_trino_service",
    "publish_cross_lineage",
    "publish_run_lineage",
    "runtime_lineage_meta",
    "trigger_trino_metadata_ingest",
    "upsert_document_asset",
]

TRINO_SERVICE = "HMDTrinoLake"


def upsert_document_asset(
    *,
    doc_id: str,
    source_id: str,
    object_uri: str | None,
    title: str | None = None,
) -> str:
    """登记文档级 Asset（Glossary term + 元数据）；不写 NER 实体。"""
    client = OpenMetadataClient.from_settings()
    fqn = f"document.{source_id}.{doc_id.replace(':', '_')}"
    asset = AssetHit(
        asset_fqn=fqn,
        name=title or doc_id,
        entity_ids=[],
        description=(
            f"Enterprise document asset\n"
            f"source={source_id}\n"
            f"object_uri={object_uri or ''}\n"
            f"type=Research Report / Supplier Document"
        ),
        asset_type="document",
        url=object_uri,
    )
    client.upsert_assets([asset])
    return fqn


def ensure_trino_service(client: OpenMetadataClient | None = None) -> dict[str, Any]:
    """幂等创建 Trino DatabaseService（供官方 connector / UI 摄入）。"""
    om = client or OpenMetadataClient.from_settings()
    om.ensure_auth()
    assert om.base_url
    host = settings.trino_host
    port = settings.trino_port
    body = {
        "name": TRINO_SERVICE,
        "serviceType": "Trino",
        "description": "HMD Iceberg lake via Trino (official OM connector path)",
        "connection": {
            "config": {
                "type": "Trino",
                "scheme": "trino",
                "hostPort": f"{host}:{port}",
                "catalog": settings.trino_catalog,
                "databaseSchema": settings.trino_schema,
            }
        },
    }
    url = f"{om.base_url.rstrip('/')}/api/v1/services/databaseServices"
    import httpx

    with httpx.Client(timeout=30.0) as http:
        # 已存在则 GET
        got = http.get(
            f"{url}/name/{TRINO_SERVICE}",
            headers=om._headers(),
        )
        if got.status_code == 200:
            return got.json()
        resp = http.post(url, headers=om._headers(), json=body)
        if resp.status_code in {409, 400}:
            got2 = http.get(f"{url}/name/{TRINO_SERVICE}", headers=om._headers())
            if got2.status_code == 200:
                return got2.json()
        resp.raise_for_status()
        return resp.json()


def trigger_trino_metadata_ingest() -> dict[str, Any]:
    """尽量创建/触发 metadata ingestion；失败时返回可操作提示。"""
    om = OpenMetadataClient.from_settings()
    try:
        svc = ensure_trino_service(om)
        return {
            "ok": True,
            "service": TRINO_SERVICE,
            "service_id": svc.get("id"),
            "note": (
                "Trino DatabaseService ready. "
                "Run OpenMetadata metadata ingestion for this service "
                "(UI or ingestion pipeline) to catalog iceberg.hmd.* tables."
            ),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def runtime_lineage_meta() -> dict[str, Any]:
    """Prefect run / deployment / ontology release；无 Server 时字段为空。"""
    from biomed_ontology.pipeline import DEFAULT_RELEASE

    meta: dict[str, Any] = {
        "prefect_run_id": None,
        "deployment": None,
        "ontology_release_id": DEFAULT_RELEASE,
    }
    try:
        from prefect.runtime import deployment, flow_run

        meta["prefect_run_id"] = getattr(flow_run, "id", None)
        meta["deployment"] = getattr(deployment, "name", None)
    except Exception:
        pass
    return meta


def publish_cross_lineage(
    *,
    doc_id: str,
    asset_fqn: str | None,
    pipeline: str = "hmd.lake.dual_write",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """补充 Document → Iceberg 表 → Milvus/GraphDB 的跨系统血缘边（REST）。"""
    om = OpenMetadataClient.from_settings()
    om.ensure_auth()
    if not om.base_url:
        return {"ok": False, "error": "openmetadata disabled"}
    details = {"pipeline": pipeline, "doc_id": doc_id, **runtime_lineage_meta()}
    if extra:
        details.update(extra)
    # OM Lineage API 形状随版本变化；尽力而为，失败不阻断 ingest
    edges = [
        {
            "from": asset_fqn or f"document:{doc_id}",
            "to": f"{settings.trino_catalog}.{settings.trino_schema}.documents",
        },
        {
            "from": f"{settings.trino_catalog}.{settings.trino_schema}.documents",
            "to": f"{settings.trino_catalog}.{settings.trino_schema}.evidence_chunks",
        },
        {
            "from": f"{settings.trino_catalog}.{settings.trino_schema}.evidence_chunks",
            "to": "milvus.foundation_evidence",
        },
        {
            "from": f"{settings.trino_catalog}.{settings.trino_schema}.evidence_chunks",
            "to": f"{settings.trino_catalog}.{settings.trino_schema}.knowledge_claims",
        },
        {
            "from": f"{settings.trino_catalog}.{settings.trino_schema}.knowledge_claims",
            "to": "graphdb.hmd.provenance",
        },
    ]
    import httpx

    recorded = 0
    errors: list[str] = []
    with httpx.Client(timeout=30.0) as http:
        for edge in edges:
            payload = {
                "edge": {
                    "fromEntity": {"type": "table", "fullyQualifiedName": edge["from"]},
                    "toEntity": {"type": "table", "fullyQualifiedName": edge["to"]},
                    "lineageDetails": {"description": json.dumps(details, ensure_ascii=False)},
                }
            }
            try:
                r = http.put(
                    f"{om.base_url.rstrip('/')}/api/v1/lineage",
                    headers=om._headers(),
                    json=payload,
                )
                if r.status_code < 300:
                    recorded += 1
                else:
                    errors.append(f"{edge['from']}→{edge['to']}: {r.status_code}")
            except Exception as exc:
                errors.append(str(exc))
    return {"ok": recorded > 0, "recorded": recorded, "errors": errors, "details": details}


def publish_run_lineage(
    *,
    pipeline: str,
    from_fqn: str,
    to_fqn: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """sync / Zingg / apply 的资产血缘（glossary / matches / claims YAML，不是伪造图边）。"""
    return publish_cross_lineage(
        doc_id=pipeline,
        asset_fqn=from_fqn,
        pipeline=pipeline,
        extra={"from": from_fqn, "to": to_fqn, **(extra or {})},
    )
