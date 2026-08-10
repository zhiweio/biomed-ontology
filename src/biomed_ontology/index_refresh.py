"""文献面增量 index：catalog retag / 按 doc 刷新。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from biomed_ontology.corpus import Chunk, Document, load_corpus
from biomed_ontology.corpus.tree import build_document_tree, tree_to_chunks
from biomed_ontology.index_state import (
    LiteratureIndexState,
    compute_catalog_fingerprint,
    load_state,
    save_state,
)
from biomed_ontology.lake.chunk_store import chunks_to_evidence_rows, load_chunks_for_index
from biomed_ontology.licensing import tier_rank
from biomed_ontology.pipeline import (
    DATA_ROOT,
    DEFAULT_RELEASE,
    KnowledgeBase,
    build_literature_base,
    build_normalizer_from_catalog,
    retag_chunks,
)
from biomed_ontology.search.backends.base import ChunkMeta

__all__ = [
    "DirtyChunk",
    "IncrementalIndexResult",
    "concept_label_terms",
    "diff_retag",
    "refresh_catalog_incremental",
    "refresh_document",
]


@dataclass(frozen=True)
class DirtyChunk:
    chunk: Chunk
    needs_reembed: bool
    before_concepts: tuple[str, ...]
    after_concepts: tuple[str, ...]
    before_labels: tuple[str, ...]
    after_labels: tuple[str, ...]


@dataclass
class IncrementalIndexResult:
    mode: str
    skipped: bool = False
    reason: str = ""
    catalog_sha256: str = ""
    chunk_total: int = 0
    dirty_count: int = 0
    reembed_count: int = 0
    patch_count: int = 0
    iceberg_n: int = 0
    milvus_n: int = 0
    dirty_document_ids: list[str] = field(default_factory=list)


def concept_label_terms(kb: KnowledgeBase, chunk: Chunk) -> list[str]:
    terms: list[str] = []
    for cid in chunk.concept_ids:
        c = kb.concept(cid)
        if c:
            terms.extend(filter(None, [c.preferred_label_en, c.preferred_label_zh]))
    return terms


def _clone_chunk(c: Chunk) -> Chunk:
    return Chunk(
        chunk_id=c.chunk_id,
        doc_id=c.doc_id,
        text=c.text,
        section=c.section,
        char_start=c.char_start,
        char_end=c.char_end,
        modality=c.modality,
        page=c.page,
        bbox=list(c.bbox),
        source_ref=c.source_ref,
        asset_path=c.asset_path,
        figure_type=c.figure_type,
        concept_ids=list(c.concept_ids),
        concept_ids_expanded=list(c.concept_ids_expanded),
        labels=list(c.labels),
        parent_id=c.parent_id,
        section_path=c.section_path,
        node_kind=c.node_kind,
        entity_ids=list(c.entity_ids),
    )


def diff_retag(
    before: list[Chunk],
    after: list[Chunk],
    *,
    before_label_fn: Callable[[Chunk], list[str]],
    after_label_fn: Callable[[Chunk], list[str]],
) -> list[DirtyChunk]:
    """比较 retag 前后；preferred-label 文本变则 needs_reembed。"""
    before_by = {c.chunk_id: c for c in before}
    dirty: list[DirtyChunk] = []
    for ch in after:
        prev = before_by.get(ch.chunk_id)
        a_lab = tuple(after_label_fn(ch))
        a_ids = tuple(ch.concept_ids or ())
        a_exp = tuple(ch.concept_ids_expanded or ())
        if prev is None:
            dirty.append(
                DirtyChunk(
                    chunk=ch,
                    needs_reembed=True,
                    before_concepts=(),
                    after_concepts=a_ids,
                    before_labels=(),
                    after_labels=a_lab,
                )
            )
            continue
        b_lab = tuple(before_label_fn(prev))
        b_ids = tuple(prev.concept_ids or ())
        b_exp = tuple(prev.concept_ids_expanded or ())
        if b_ids == a_ids and b_exp == a_exp and b_lab == a_lab:
            continue
        dirty.append(
            DirtyChunk(
                chunk=ch,
                needs_reembed=(b_lab != a_lab),
                before_concepts=b_ids,
                after_concepts=a_ids,
                before_labels=b_lab,
                after_labels=a_lab,
            )
        )
    return dirty


def _meta_for_chunk(chunk: Chunk, *, source_id: str, license_tier: str) -> ChunkMeta:
    from biomed_ontology._generated.hmd_concept import LicenseTierEnum

    try:
        tier = LicenseTierEnum(license_tier or "TIER_0")
    except ValueError:
        tier = LicenseTierEnum.TIER_0
    return ChunkMeta(
        chunk_id=chunk.chunk_id,
        doc_id=chunk.doc_id,
        source_id=source_id or "",
        license_rank=tier_rank(tier),
        labels=tuple(chunk.labels or ()),
        modality=str(getattr(chunk.modality, "value", chunk.modality) or "TEXT"),
        figure_type=str(getattr(chunk, "figure_type", "") or ""),
    )


def _load_iceberg_doc_meta(release_id: str) -> dict[str, tuple[str, str]]:
    """doc_id → (source_id, license_tier)。"""
    from pyiceberg.expressions import EqualTo

    from biomed_ontology.lake.catalog import EVIDENCE_CHUNKS_TABLE, open_catalog

    out: dict[str, tuple[str, str]] = {}
    table = open_catalog().load_table(EVIDENCE_CHUNKS_TABLE)
    arrow = table.scan(row_filter=EqualTo(term="release_id", value=release_id)).to_arrow()
    if arrow is None or arrow.num_rows == 0:
        return out
    for r in arrow.to_pylist():
        did = str(r.get("document_id") or "")
        if did and did not in out:
            out[did] = (
                str(r.get("source_id") or ""),
                str(r.get("license_tier") or "TIER_0"),
            )
    return out


def _doc_meta_map(
    chunks: list[Chunk],
    documents: list[Document] | None,
    iceberg_meta: dict[str, tuple[str, str]] | None = None,
) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for d in documents or []:
        tier = d.license_tier.value if hasattr(d.license_tier, "value") else str(d.license_tier)
        out[d.doc_id] = (d.source_id, tier)
    if iceberg_meta:
        for doc_id, meta in iceberg_meta.items():
            out.setdefault(doc_id, meta)
    for ch in chunks:
        out.setdefault(ch.doc_id, ("", "TIER_0"))
    return out


def _synthetic_doc(doc_id: str, source_id: str, tier_s: str) -> Document:
    from biomed_ontology._generated.hmd_concept import LicenseTierEnum
    from biomed_ontology._generated.hmd_fact import DocTypeEnum, LanguageEnum

    try:
        tier = LicenseTierEnum(tier_s)
    except ValueError:
        tier = LicenseTierEnum.TIER_0
    return Document(
        doc_id=doc_id,
        source_id=source_id or "UNKNOWN",
        title=doc_id,
        doc_type=DocTypeEnum.JOURNAL_ARTICLE,
        language=LanguageEnum.en,
        license_tier=tier,
    )


def _write_iceberg_dirty(
    dirty: list[DirtyChunk],
    *,
    release_id: str,
    documents: list[Document] | None,
    doc_meta: dict[str, tuple[str, str]],
) -> int:
    from biomed_ontology.lake.tables import append_evidence_chunks

    by_doc: dict[str, list[Chunk]] = {}
    for d in dirty:
        by_doc.setdefault(d.chunk.doc_id, []).append(d.chunk)
    total = 0
    doc_by = {d.doc_id: d for d in (documents or [])}
    for doc_id, chs in by_doc.items():
        doc = doc_by.get(doc_id)
        if doc is None:
            source_id, tier_s = doc_meta.get(doc_id, ("", "TIER_0"))
            doc = _synthetic_doc(doc_id, source_id, tier_s)
        rows = chunks_to_evidence_rows(chs, documents=[doc], release_id=release_id)
        total += append_evidence_chunks(rows, document_id=doc_id)
    return total


def _write_milvus_dirty(
    dirty: list[DirtyChunk],
    *,
    kb: KnowledgeBase,
    backend: Any,
    doc_meta: dict[str, tuple[str, str]],
    label_fn: Callable[[Chunk], list[str]],
) -> tuple[int, int, int]:
    from biomed_ontology.search.backends.milvus import chunk_to_row

    if not dirty:
        return 0, 0, 0

    reembed_rows: list[dict[str, Any]] = []
    patch_rows: list[dict[str, Any]] = []
    patch_ids = [d.chunk.chunk_id for d in dirty if not d.needs_reembed]
    existing = backend.query_by_ids(patch_ids) if patch_ids else {}
    vector_fields = set(backend.vector_fields()) if hasattr(backend, "vector_fields") else set()

    for d in dirty:
        ch = d.chunk
        source_id, tier_s = doc_meta.get(ch.doc_id, ("", "TIER_0"))
        meta = _meta_for_chunk(ch, source_id=source_id, license_tier=tier_s)
        row = chunk_to_row(ch, meta, label_terms=label_fn(ch))
        row["release_id"] = kb.release_id
        if d.needs_reembed:
            reembed_rows.append(row)
            continue
        prev = existing.get(ch.chunk_id)
        has_vec = bool(prev) and (
            any(prev.get(f) is not None for f in vector_fields)
            if vector_fields
            else any(k.startswith("dense_") or k == "sparse_lexical" for k in prev)
        )
        if not has_vec:
            reembed_rows.append(row)
            continue
        patch_rows.append({**prev, **row})

    n_re = backend.upsert(reembed_rows, encode=True) if reembed_rows else 0
    n_patch = backend.upsert(patch_rows, encode=False) if patch_rows else 0
    return n_re + n_patch, len(reembed_rows), len(patch_rows)


def _persist_state(
    *,
    fp: str,
    embedder_name: str,
    collection: str,
    chunk_count: int,
    dirty_count: int,
    state_path: Path | None,
) -> None:
    save_state(
        LiteratureIndexState(
            catalog_sha256=fp,
            release_id=DEFAULT_RELEASE,
            embedder=embedder_name,
            collection=collection,
            chunk_count=chunk_count,
            dirty_last_run=dirty_count,
        ),
        state_path,
    )


def refresh_catalog_incremental(
    *,
    embedder_name: str = "multimodal-bio",
    collection: str | None = None,
    state_path: Path | None = None,
    force: bool = False,
    backend: Any | None = None,
    skip_milvus: bool = False,
    skip_iceberg: bool = False,
) -> IncrementalIndexResult:
    """catalog 变更 → Iceberg 装载 → retag → 脏写。fingerprint 未变则 no-op。"""
    from biomed_ontology.config import settings
    from biomed_ontology.embed import get_embedder
    from biomed_ontology.lake.catalog import ensure_lake_tables
    from biomed_ontology.registry import load_registry
    from biomed_ontology.search.backends.milvus import MilvusBackend

    fp = compute_catalog_fingerprint()
    prev = load_state(state_path)
    coll = collection or settings.milvus_collection
    if (
        not force
        and prev is not None
        and prev.catalog_sha256 == fp
        and prev.release_id == DEFAULT_RELEASE
        and (not prev.collection or prev.collection == coll)
    ):
        return IncrementalIndexResult(
            mode="incremental",
            skipped=True,
            reason="catalog fingerprint unchanged",
            catalog_sha256=fp,
            chunk_total=prev.chunk_count,
        )

    kb = build_normalizer_from_catalog(release_id=DEFAULT_RELEASE)
    iceberg_meta: dict[str, tuple[str, str]] = {}
    first_run = False

    try:
        ensure_lake_tables()
        chunks = load_chunks_for_index(release_id=DEFAULT_RELEASE)
        if chunks:
            iceberg_meta = _load_iceberg_doc_meta(DEFAULT_RELEASE)
    except Exception:
        chunks = []

    if not chunks:
        first_run = True
        full = build_literature_base(with_graph=False, release_id=DEFAULT_RELEASE)
        kb.documents = full.documents
        kb.chunks = full.chunks
        kb.labels = full.labels
        before = [_clone_chunk(c) for c in full.chunks]
        for b in before:
            b.concept_ids = []
            b.concept_ids_expanded = []
            b.entity_ids = []
        after = full.chunks
    else:
        before = [_clone_chunk(c) for c in chunks]
        after = retag_chunks(list(chunks), kb.normalizer, hub=kb.hub, release_id=kb.release_id)
        kb.chunks = after

    def labels_for(ch: Chunk) -> list[str]:
        return concept_label_terms(kb, ch)

    dirty = diff_retag(
        before,
        after,
        before_label_fn=labels_for,
        after_label_fn=labels_for,
    )
    if first_run:
        dirty = [
            DirtyChunk(
                chunk=d.chunk,
                needs_reembed=True,
                before_concepts=d.before_concepts,
                after_concepts=d.after_concepts,
                before_labels=d.before_labels,
                after_labels=d.after_labels,
            )
            for d in dirty
        ]

    if not dirty:
        _persist_state(
            fp=fp,
            embedder_name=embedder_name,
            collection=coll,
            chunk_count=len(kb.chunks),
            dirty_count=0,
            state_path=state_path,
        )
        return IncrementalIndexResult(
            mode="incremental",
            skipped=True,
            reason="retag produced no dirty chunks",
            catalog_sha256=fp,
            chunk_total=len(kb.chunks),
        )

    doc_meta = _doc_meta_map(kb.chunks, kb.documents, iceberg_meta)
    iceberg_n = 0
    if not skip_iceberg:
        iceberg_n = _write_iceberg_dirty(
            dirty, release_id=kb.release_id, documents=kb.documents, doc_meta=doc_meta
        )

    milvus_n = reembed_n = patch_n = 0
    if not skip_milvus:
        if backend is None:
            model = get_embedder(embedder_name)
            registry = load_registry()
            backend = MilvusBackend(
                uri=settings.milvus_uri,
                token=settings.milvus_token.get_secret_value(),
                collection=coll,
                embedder=model,
                known_sources=frozenset(s.id for s in registry.active()),
                asset_root=DATA_ROOT / "assets",
                release_id=kb.release_id,
            )
            backend.ensure_collection(drop_existing=False)
        milvus_n, reembed_n, patch_n = _write_milvus_dirty(
            dirty,
            kb=kb,
            backend=backend,
            doc_meta=doc_meta,
            label_fn=labels_for,
        )

    dirty_docs = sorted({d.chunk.doc_id for d in dirty})
    _persist_state(
        fp=fp,
        embedder_name=embedder_name,
        collection=coll,
        chunk_count=len(kb.chunks),
        dirty_count=len(dirty),
        state_path=state_path,
    )
    return IncrementalIndexResult(
        mode="incremental",
        catalog_sha256=fp,
        chunk_total=len(kb.chunks),
        dirty_count=len(dirty),
        reembed_count=reembed_n,
        patch_count=patch_n,
        iceberg_n=iceberg_n,
        milvus_n=milvus_n,
        dirty_document_ids=dirty_docs,
    )


def refresh_document(
    doc_id: str,
    *,
    embedder_name: str = "multimodal-bio",
    collection: str | None = None,
    backend: Any | None = None,
    data_root: Path | None = None,
) -> IncrementalIndexResult:
    """单文档：从 corpus YAML 切树 → normalize → 覆盖 Iceberg/Milvus 该 doc。"""
    from biomed_ontology.config import settings
    from biomed_ontology.embed import get_embedder
    from biomed_ontology.lake.catalog import ensure_lake_tables
    from biomed_ontology.lake.tables import append_evidence_chunks
    from biomed_ontology.observability import ObservabilityHub
    from biomed_ontology.registry import load_registry
    from biomed_ontology.search.backends.milvus import MilvusBackend, chunk_to_row

    root = data_root or DATA_ROOT
    kb = build_normalizer_from_catalog(release_id=DEFAULT_RELEASE)
    corpus_files = sorted((root / "corpus").glob("*.yaml")) + sorted(
        (root / "corpus" / "parsed").glob("*.yaml")
    )
    documents: list[Document] = []
    for f in corpus_files:
        documents.extend(load_corpus(f))
    doc = next((d for d in documents if d.doc_id == doc_id), None)
    if doc is None:
        raise FileNotFoundError(f"corpus 中找不到 doc_id={doc_id!r}")

    hub = ObservabilityHub()
    ctx = hub.start_trace(release_id=DEFAULT_RELEASE, agent_id="index-doc")
    chunks = tree_to_chunks(build_document_tree(doc))
    retag_chunks(chunks, kb.normalizer, ctx=ctx, hub=hub, release_id=DEFAULT_RELEASE)
    kb.documents = [doc]
    kb.chunks = chunks

    ensure_lake_tables()
    rows = chunks_to_evidence_rows(chunks, documents=[doc], release_id=DEFAULT_RELEASE)
    iceberg_n = append_evidence_chunks(rows, document_id=doc_id)

    coll = collection or settings.milvus_collection
    if backend is None:
        model = get_embedder(embedder_name)
        registry = load_registry()
        backend = MilvusBackend(
            uri=settings.milvus_uri,
            token=settings.milvus_token.get_secret_value(),
            collection=coll,
            embedder=model,
            known_sources=frozenset(s.id for s in registry.active()),
            asset_root=DATA_ROOT / "assets",
            release_id=DEFAULT_RELEASE,
        )
        backend.ensure_collection(drop_existing=False)
    backend.delete_by_doc(doc_id)
    milvus_rows = []
    tier_s = doc.license_tier.value if hasattr(doc.license_tier, "value") else str(doc.license_tier)
    for ch in chunks:
        meta = _meta_for_chunk(ch, source_id=doc.source_id, license_tier=tier_s)
        row = chunk_to_row(ch, meta, label_terms=concept_label_terms(kb, ch))
        row["release_id"] = DEFAULT_RELEASE
        milvus_rows.append(row)
    milvus_n = backend.upsert(milvus_rows, encode=True)

    prev = load_state()
    fp = prev.catalog_sha256 if prev else compute_catalog_fingerprint()
    _persist_state(
        fp=fp,
        embedder_name=embedder_name,
        collection=coll,
        chunk_count=(prev.chunk_count if prev else 0) or len(chunks),
        dirty_count=len(chunks),
        state_path=None,
    )
    return IncrementalIndexResult(
        mode="doc",
        catalog_sha256=fp,
        chunk_total=len(chunks),
        dirty_count=len(chunks),
        reembed_count=len(chunks),
        iceberg_n=iceberg_n,
        milvus_n=milvus_n,
        dirty_document_ids=[doc_id],
    )
