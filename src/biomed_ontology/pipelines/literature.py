"""文献装配 DAG：discover → parse → IngestQA → refresh_document / incremental。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from prefect import flow, task

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology._generated.hmd_fact import DocTypeEnum, LanguageEnum
from biomed_ontology.corpus.tree import build_document_tree, tree_to_chunks
from biomed_ontology.index_state import compute_catalog_fingerprint, load_state
from biomed_ontology.lake.ingest_qa import IngestQAError, run_ingest_qa
from biomed_ontology.lake.steps import IngestContext
from biomed_ontology.parse import parse_document

__all__ = [
    "DEFAULT_OUT",
    "DEFAULT_RAW",
    "discover_dirty_docs",
    "literature_refresh",
    "literature_reindex_full",
    "parse_one_record",
]

DEFAULT_RAW = Path("data/raw")
DEFAULT_OUT = Path("data/corpus/parsed")
DEFAULT_ASSETS = Path("data/assets")
_TIER = {"CC BY": LicenseTierEnum.TIER_0}


def _safe_id(doc_id: str) -> str:
    return doc_id.replace(":", "_").replace("/", "_")


def _pdf_checksum(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def discover_dirty_docs(
    *,
    raw_dir: Path | None = None,
    out_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """对比 corpus.json、parsed YAML 与 PDF checksum。"""
    raw = Path(raw_dir or DEFAULT_RAW)
    out = Path(out_dir or DEFAULT_OUT)
    manifest = raw / "corpus.json"
    if not manifest.is_file():
        return []
    records = json.loads(manifest.read_text(encoding="utf-8"))
    dirty: list[dict[str, Any]] = []
    for rec in records:
        doc_id = str(rec["doc_id"])
        pdf = raw / rec["pdf"]
        parsed = out / f"{_safe_id(doc_id)}.yaml"
        stamp = out / f"{_safe_id(doc_id)}.sha256"
        if not pdf.is_file():
            dirty.append({"doc_id": doc_id, "reason": "pdf_missing", "pdf": str(pdf)})
            continue
        checksum = _pdf_checksum(pdf)
        if not parsed.is_file():
            dirty.append(
                {
                    "doc_id": doc_id,
                    "reason": "unparsed",
                    "pdf": str(pdf),
                    "checksum": checksum,
                    "record": rec,
                }
            )
            continue
        prev = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else ""
        if prev != checksum:
            dirty.append(
                {
                    "doc_id": doc_id,
                    "reason": "checksum_changed",
                    "pdf": str(pdf),
                    "checksum": checksum,
                    "record": rec,
                }
            )
    return dirty


def parse_one_record(
    rec: dict[str, Any],
    *,
    raw_dir: Path,
    out_dir: Path,
    assets_dir: Path,
    source_id: str,
) -> Path:
    """单篇 parse → YAML。逻辑对齐 ``scripts/parse_corpus.py``。"""
    doc_id = str(rec["doc_id"])
    pdf = raw_dir / rec["pdf"]
    tier = _TIER.get(rec.get("license", ""))
    if tier is None:
        raise RuntimeError(f"许可 {rec.get('license')!r} 未登记等级")
    if not pdf.is_file():
        raise FileNotFoundError(str(pdf))
    safe = _safe_id(doc_id)
    parsed = parse_document(
        pdf,
        doc_id=doc_id,
        source_id=source_id,
        title=rec.get("title") or doc_id,
        doc_type=DocTypeEnum.JOURNAL_ARTICLE,
        license_tier=tier,
        language=LanguageEnum.en,
        external_id=doc_id.removeprefix("DOC:"),
        out_dir=assets_dir / safe,
    )
    dest = out_dir / f"{safe}.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        yaml.safe_dump(parsed.to_yaml_obj(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (out_dir / f"{safe}.sha256").write_text(_pdf_checksum(pdf) + "\n", encoding="utf-8")
    return dest


@task(retries=2, timeout_seconds=300, tags=["mineru"])
def task_parse_document(item: dict[str, Any], source_id: str) -> dict[str, Any]:
    rec = item.get("record") or {"doc_id": item["doc_id"], "pdf": item.get("pdf")}
    path = parse_one_record(
        rec,
        raw_dir=DEFAULT_RAW,
        out_dir=DEFAULT_OUT,
        assets_dir=DEFAULT_ASSETS,
        source_id=source_id,
    )
    return {"doc_id": item["doc_id"], "parsed": str(path)}


@task
def task_literature_qa(doc_id: str, source_id: str) -> dict[str, Any]:
    from biomed_ontology.corpus import load_corpus

    parsed = DEFAULT_OUT / f"{_safe_id(doc_id)}.yaml"
    docs = load_corpus(parsed)
    doc = next((d for d in docs if d.doc_id == doc_id), docs[0] if docs else None)
    if doc is None:
        raise RuntimeError(f"parsed yaml 无文档 {doc_id}")
    ctx = IngestContext(source_id=source_id, doc_id=doc_id, document=doc)
    ctx.chunks = tree_to_chunks(build_document_tree(doc))
    run_ingest_qa(ctx)
    return {"doc_id": doc_id, "chunks": len(ctx.chunks)}


@task(tags=["embed"])
def task_refresh_document(doc_id: str, embedder_name: str) -> dict[str, Any]:
    from biomed_ontology.index_refresh import refresh_document

    result = refresh_document(doc_id, embedder_name=embedder_name)
    return asdict(result)


@task(tags=["embed"])
def task_catalog_incremental(embedder_name: str, force: bool = False) -> dict[str, Any]:
    from biomed_ontology.index_refresh import refresh_catalog_incremental

    result = refresh_catalog_incremental(embedder_name=embedder_name, force=force)
    return asdict(result)


@flow(name="literature_refresh")
def literature_refresh(
    *,
    source_id: str = "PUBMED",
    embedder_name: str = "multimodal-bio",
    raw_dir: str | None = None,
    out_dir: str | None = None,
) -> dict[str, Any]:
    """脏 PDF 解析 + IngestQA + 单篇 index；catalog 变了再 incremental retag。"""
    dirty = discover_dirty_docs(
        raw_dir=Path(raw_dir) if raw_dir else None,
        out_dir=Path(out_dir) if out_dir else None,
    )
    ok: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    for item in dirty:
        if item.get("reason") == "pdf_missing":
            failed.append(item)
            continue
        doc_id = str(item["doc_id"])
        try:
            task_parse_document(item, source_id)
            task_literature_qa(doc_id, source_id)
            ok.append(task_refresh_document(doc_id, embedder_name))
        except IngestQAError as exc:
            quarantined.append(
                {
                    "doc_id": doc_id,
                    "reason": "ingest_qa",
                    "error": str(exc),
                    "retry": {
                        "source_id": source_id,
                        "file": item.get("pdf"),
                        "record": item.get("record"),
                        "embedder_name": embedder_name,
                    },
                }
            )
        except Exception as exc:
            failed.append(
                {
                    "doc_id": doc_id,
                    "reason": type(exc).__name__,
                    "error": str(exc),
                    "retry": {
                        "source_id": source_id,
                        "file": item.get("pdf"),
                        "record": item.get("record"),
                    },
                }
            )

    fp = compute_catalog_fingerprint()
    prev = load_state()
    incr: dict[str, Any] | None = None
    if prev is None or prev.catalog_sha256 != fp:
        incr = task_catalog_incremental(embedder_name)
    from biomed_ontology.lake.quarantine import persist_records

    persist_records(quarantined, plane="literature")
    persist_records(
        [{**r, "reason_code": r.get("reason")} for r in failed],
        plane="literature",
    )
    return {
        "dirty_n": len(dirty),
        "ok": ok,
        "failed": failed,
        "quarantined": quarantined,
        "catalog_incremental": incr,
        "catalog_sha256": fp,
    }


@flow(name="literature_reindex_full")
def literature_reindex_full(
    *,
    embedder_name: str = "multimodal-bio",
    collection: str | None = None,
) -> dict[str, Any]:
    """低频全量 recreate。日常请用 literature_refresh。"""
    from biomed_ontology.config import settings
    from biomed_ontology.embed import get_embedder
    from biomed_ontology.index_state import LiteratureIndexState, save_state
    from biomed_ontology.lake.catalog import ensure_lake_tables
    from biomed_ontology.lake.chunk_store import chunks_to_evidence_rows
    from biomed_ontology.lake.tables import append_evidence_chunks
    from biomed_ontology.ontology.neighborhood import NullNeighborhood
    from biomed_ontology.pipeline import DATA_ROOT, build_literature_base
    from biomed_ontology.registry import load_registry
    from biomed_ontology.search import HybridSearcher
    from biomed_ontology.search.backends.milvus import MilvusBackend, chunk_to_row

    kb = build_literature_base(with_graph=False)
    ensure_lake_tables()
    lake_rows = chunks_to_evidence_rows(kb.chunks, documents=kb.documents, release_id=kb.release_id)
    lake_n = append_evidence_chunks(lake_rows)
    model = get_embedder(embedder_name)
    registry = load_registry()
    backend = MilvusBackend(
        uri=settings.milvus_uri,
        token=settings.milvus_token.get_secret_value(),
        collection=collection or settings.milvus_collection,
        embedder=model,
        known_sources=frozenset(s.id for s in registry.active()),
        asset_root=DATA_ROOT / "assets",
        release_id=kb.release_id,
    )
    searcher = HybridSearcher(kb, backend=backend, neighborhood=NullNeighborhood())
    backend.ensure_collection(drop_existing=True)
    rows = []
    for ch in kb.chunks:
        meta = searcher.chunk_meta(ch.chunk_id)
        if meta is None:
            raise RuntimeError(f"缺少 chunk meta：{ch.chunk_id}")
        row = chunk_to_row(ch, meta, label_terms=searcher.index_text_terms(ch))
        row["release_id"] = kb.release_id
        rows.append(row)
    written = backend.upsert(rows, batch_size=128)
    save_state(
        LiteratureIndexState(
            catalog_sha256=compute_catalog_fingerprint(),
            release_id=kb.release_id,
            embedder=model.name,
            collection=backend.collection,
            chunk_count=len(kb.chunks),
            dirty_last_run=len(kb.chunks),
        )
    )
    return {
        "mode": "recreate",
        "chunks": len(kb.chunks),
        "milvus": written,
        "iceberg": lake_n,
        "collection": backend.collection,
    }
