"""双写流水线纯函数步骤（Prefect task 薄包装调用此处）。"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from biomed_ontology.config import settings
from biomed_ontology.corpus import Document, load_corpus
from biomed_ontology.corpus.tree import build_document_tree, tree_to_chunks
from biomed_ontology.lake.claim_bridge import facts_to_claims
from biomed_ontology.lake.evidence_index import upsert_evidence_objects
from biomed_ontology.lake.minio_store import DocumentObjectStore
from biomed_ontology.lake.tables import (
    append_documents,
    append_evidence_chunks,
    append_knowledge_claims,
)

__all__ = [
    "IngestContext",
    "annotate_bern2",
    "parse_and_tree",
    "put_document",
    "register_om_document",
    "require_bern2",
    "write_claims",
    "write_evidence",
]


@dataclass
class IngestContext:
    source_id: str
    doc_id: str
    object_uri: str | None = None
    checksum: str | None = None
    document: Document | None = None
    chunks: list[Any] = field(default_factory=list)
    claims: list[Any] = field(default_factory=list)
    skipped_claims: int = 0
    evidence_n: int = 0
    claim_n: int = 0
    asset_fqn: str | None = None
    errors: list[str] = field(default_factory=list)
    # annotate_bern2 装配后供 write_claims 复用，避免二次 load_world_model
    resolver: Any | None = None
    parse_degraded: list[str] = field(default_factory=list)
    qa: Any | None = None


def require_bern2(bern2_url: str | None = None) -> str:
    url = (bern2_url or settings.bern2_url or "").rstrip("/")
    if not url:
        raise RuntimeError(
            "BERN2 为双写硬依赖：请设置 HMD_BERN2_URL 或 --bern2-url（例如 http://localhost:8888）"
        )
    import httpx

    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(f"{url}/plain", json={"text": "EGFR"})
            r.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"BERN2 不可达（{url}）：{exc}") from exc
    return url


def put_document(
    ctx: IngestContext,
    *,
    file_path: Path | None = None,
    content_type: str = "application/octet-stream",
) -> IngestContext:
    if file_path is None or not file_path.exists():
        return ctx
    store = DocumentObjectStore.from_settings()
    meta = store.put_document(
        source_id=ctx.source_id,
        doc_id=ctx.doc_id,
        path=file_path,
        content_type=content_type,
    )
    ctx.object_uri = meta["object_uri"]
    ctx.checksum = meta["checksum_sha256"]
    try:
        append_documents(
            [
                {
                    "doc_id": ctx.doc_id,
                    "object_uri": ctx.object_uri,
                    "source_id": ctx.source_id,
                    "content_type": content_type,
                    "checksum_sha256": ctx.checksum,
                    "title": ctx.doc_id,
                    "license_tier": "TIER_0",
                }
            ],
            doc_id=ctx.doc_id,
        )
    except Exception as exc:
        ctx.errors.append(f"iceberg.documents: {exc}")
    return ctx


def parse_and_tree(
    ctx: IngestContext,
    *,
    corpus_yaml: Path | None = None,
    document: Document | None = None,
    file_path: Path | None = None,
    layout: str | None = None,
) -> IngestContext:
    if document is not None:
        doc = document
    elif corpus_yaml is not None:
        docs = load_corpus(corpus_yaml)
        doc = next((d for d in docs if d.doc_id == ctx.doc_id), None)
        if doc is None:
            if len(docs) == 1:
                doc = docs[0]
                ctx.doc_id = doc.doc_id
            else:
                raise RuntimeError(f"corpus 中找不到 doc_id={ctx.doc_id}")
    elif file_path is not None:
        from biomed_ontology.parse import parse_document

        parsed = parse_document(
            file_path,
            doc_id=ctx.doc_id,
            source_id=ctx.source_id,
            layout=layout,
        )
        doc = parsed.document
        ctx.parse_degraded = [str(x) for x in (getattr(parsed, "degraded", ()) or ())]
    else:
        raise RuntimeError("需要 --corpus-yaml、原始 file_path 或注入 Document")
    ctx.document = doc
    tree = build_document_tree(doc)
    ctx.chunks = tree_to_chunks(tree)
    return ctx


def annotate_bern2(ctx: IngestContext, *, bern2_url: str | None = None) -> IngestContext:
    url = require_bern2(bern2_url)
    from biomed_ontology.foundation.bern2 import Bern2Client, load_enterprise_dictionary
    from biomed_ontology.foundation.world import load_world_model

    dict_path = Path("ontology/dictionary/enterprise_dictionary.yaml")
    dictionary = load_enterprise_dictionary(dict_path) if dict_path.exists() else None
    world = load_world_model(bern2_url=url)
    resolver = world.resolver
    assert resolver is not None
    ctx.resolver = resolver

    def _resolve(text: str) -> Any:
        return resolver.resolve_text(text)

    texts = [str(getattr(ch, "text", "") or "") for ch in ctx.chunks]
    with Bern2Client(
        base_url=url,
        dictionary=dictionary,
        timeout=settings.bern2_timeout_s,
        concurrency=settings.bern2_concurrency,
        min_chars=settings.bern2_min_chars,
    ) as client:
        mentions_list = client.annotate_many(texts)

    from biomed_ontology.lake.obs_events import emit_er_observation

    def _as_ent(hit: Any) -> str | None:
        if hit is None:
            return None
        if isinstance(hit, dict):
            return hit.get("canonical_entity")
        if isinstance(hit, list):
            for item in hit:
                c = _as_ent(item)
                if c:
                    return c
            return None
        return getattr(hit, "canonical_entity", None)

    for ch, mentions in zip(ctx.chunks, mentions_list, strict=True):
        ents: list[str] = []
        seen: set[str] = set()
        for m in mentions:
            text = str(getattr(m, "mention", None) or m).strip()
            ids = list(getattr(m, "ids", None) or [])
            ent = next((i for i in ids if str(i).startswith("HMD:ENT:")), None)
            if ent is None and text:
                ent = _as_ent(_resolve(text))
            if ent and ent not in seen:
                seen.add(ent)
                ents.append(ent)
            elif not ent and text:
                with contextlib.suppress(Exception):
                    emit_er_observation(
                        mention=text,
                        source="lake_annotate",
                        resolve_status="unmapped",
                        kind_hint=getattr(m, "obj_type", None),
                        tool_name="annotate_bern2",
                        document_id=ctx.doc_id,
                        chunk_id=str(getattr(ch, "chunk_id", "") or ""),
                        bern2_ids=ids,
                        ontology_release_id=getattr(world, "release_id", None),
                    )
        ch.entity_ids = ents
        ch.concept_ids = list(dict.fromkeys([*ch.concept_ids, *ents]))
    return ctx


def write_evidence(ctx: IngestContext) -> IngestContext:
    from biomed_ontology.ingest.catalog import DEFAULT_RELEASE
    from biomed_ontology.lake.chunk_store import chunks_to_evidence_rows

    docs = [ctx.document] if ctx.document is not None else []
    rows = chunks_to_evidence_rows(
        ctx.chunks,
        documents=docs,
        release_id=DEFAULT_RELEASE,
        milvus_collection="foundation_evidence",
    )
    try:
        append_evidence_chunks(rows, document_id=ctx.doc_id)
    except Exception as exc:
        ctx.errors.append(f"iceberg.evidence_chunks: {exc}")
    try:
        ctx.evidence_n = upsert_evidence_objects(ctx.chunks, doc_id=ctx.doc_id)
    except Exception as exc:
        ctx.errors.append(f"milvus.foundation_evidence: {exc}")
        raise
    return ctx


def write_claims(ctx: IngestContext, *, bern2_url: str | None = None) -> IngestContext:
    from biomed_ontology.corpus.extract import TriModalPipeline
    from biomed_ontology.foundation.world import load_world_model
    from biomed_ontology.identity import IdentityService
    from biomed_ontology.normalize import Normalizer
    from biomed_ontology.observability import ObservabilityHub

    assert ctx.document is not None
    hub = ObservabilityHub()
    ctx_trace = hub.start_trace(release_id="lake-ingest", agent_id="lake")
    try:
        identity = IdentityService.from_catalog(resolver=ctx.resolver)
    except Exception:
        identity = IdentityService(
            normalizer=Normalizer(concepts=[], synonyms=[], ambiguity_index={}, release_id="0.0.0"),
            resolver=ctx.resolver,
        )

    # annotate_bern2 已写入 chunk.entity_ids；LLM/候选层直接复用，避免二次 NER
    facts = TriModalPipeline().run(
        [ctx.document], ctx.chunks, normalizer=identity.normalizer, ctx=ctx_trace
    )
    resolver = identity.resolver
    if resolver is None:
        world = load_world_model(bern2_url=bern2_url or settings.bern2_url or None)
        assert world.resolver is not None
        resolver = world.resolver
        ctx.resolver = resolver
        identity.resolver = resolver

    def _resolve(text: str) -> Any:
        return identity.resolve_text(text)

    claims, skipped = facts_to_claims(facts, document_id=ctx.doc_id, resolve_fn=_resolve)
    ctx.claims = claims
    ctx.skipped_claims = skipped
    ctx.claim_n = len(claims)

    claim_rows = [
        {
            "claim_id": c.claim_id,
            "subject_id": c.subject_id,
            "predicate": c.predicate,
            "object_id": c.object_id,
            "object_value": c.object_value,
            "claim_status": "extracted",
            "confidence": c.confidence,
            "evidence_ids": list(c.evidence_ids),
            "extracted_by": c.extracted_by,
            "span": c.span,
            "document_id": ctx.doc_id,
        }
        for c in claims
    ]
    try:
        append_knowledge_claims(claim_rows, document_id=ctx.doc_id)
    except Exception as exc:
        ctx.errors.append(f"iceberg.knowledge_claims: {exc}")

    try:
        from biomed_ontology.foundation.graphdb import GraphDbClient
        from biomed_ontology.foundation.sync import append_extracted_claims

        gdb = GraphDbClient.from_settings()
        if gdb.health():
            append_extracted_claims(gdb, claims)
        else:
            ctx.errors.append("graphdb: unreachable — skipped provenance append")
    except Exception as exc:
        ctx.errors.append(f"graphdb.provenance: {exc}")
    return ctx


def register_om_document(ctx: IngestContext) -> IngestContext:
    try:
        from biomed_ontology.lake.om_governance import upsert_document_asset

        ctx.asset_fqn = upsert_document_asset(
            doc_id=ctx.doc_id,
            source_id=ctx.source_id,
            object_uri=ctx.object_uri,
            title=ctx.document.title if ctx.document else ctx.doc_id,
        )
    except Exception as exc:
        ctx.errors.append(f"openmetadata.document: {exc}")
    return ctx


def load_batch_manifest(path: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(raw.get("documents") or raw.get("items") or [])
