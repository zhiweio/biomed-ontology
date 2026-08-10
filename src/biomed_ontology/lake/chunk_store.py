"""Evidence ChunkStore：按 chunk_id 从 Iceberg 懒加载（支持批量）。

Citationware / hydrate 的权威正文在此，不在进程内全量 ``kb.chunks``。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

__all__ = [
    "BATCH_SIZE",
    "ChunkRecord",
    "ChunkStore",
    "IcebergChunkStore",
    "MemoryChunkStore",
    "chunk_from_record",
    "chunk_record_from_kb_chunk",
    "chunks_to_evidence_rows",
    "load_chunks_for_index",
]

BATCH_SIZE = 64


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    document_id: str
    content: str
    section_path: str = ""
    parent_id: str = ""
    node_kind: str = ""
    modality: str = "TEXT"
    page: int = 0
    sort_order: int = 0
    source_id: str = ""
    license_tier: str = "TIER_0"
    release_id: str = ""
    entity_ids: tuple[str, ...] = ()
    title: str = ""

    @property
    def doc_id(self) -> str:
        return self.document_id

    @property
    def section(self) -> str:
        return self.section_path

    @property
    def text(self) -> str:
        return self.content

    @property
    def char_start(self) -> int:
        return self.sort_order


class ChunkStore(Protocol):
    def get_chunk(self, chunk_id: str) -> ChunkRecord | None: ...

    def get_chunks(self, chunk_ids: Sequence[str]) -> dict[str, ChunkRecord]: ...

    def get_section_chunks(self, document_id: str, section_path: str) -> list[ChunkRecord]: ...

    def get_document_chunks(self, document_id: str) -> list[ChunkRecord]: ...


def chunk_record_from_kb_chunk(
    chunk: Any,
    *,
    source_id: str = "",
    license_tier: str = "TIER_0",
    release_id: str = "",
    title: str = "",
) -> ChunkRecord:
    modality = getattr(chunk, "modality", "TEXT")
    modality_s = modality.value if hasattr(modality, "value") else str(modality or "TEXT")
    tier = license_tier
    if hasattr(tier, "value"):
        tier = tier.value
    return ChunkRecord(
        chunk_id=str(chunk.chunk_id),
        document_id=str(chunk.doc_id),
        content=str(getattr(chunk, "text", "") or ""),
        section_path=str(
            getattr(chunk, "section_path", None) or getattr(chunk, "section", "") or ""
        ),
        parent_id=str(getattr(chunk, "parent_id", "") or ""),
        node_kind=str(getattr(chunk, "node_kind", "") or ""),
        modality=modality_s,
        page=int(getattr(chunk, "page", 0) or 0),
        sort_order=int(getattr(chunk, "char_start", 0) or 0),
        source_id=str(source_id or ""),
        license_tier=str(tier or "TIER_0"),
        release_id=str(release_id or ""),
        entity_ids=tuple(
            str(x) for x in (getattr(chunk, "entity_ids", None) or getattr(chunk, "concept_ids", None) or [])
        ),
        title=str(title or ""),
    )


def chunks_to_evidence_rows(
    chunks: Sequence[Any],
    *,
    documents: Sequence[Any] | None = None,
    release_id: str,
    milvus_collection: str = "hmd_chunks",
) -> list[dict[str, Any]]:
    """Tree/flat Chunk → Iceberg ``evidence_chunks`` 行。"""
    doc_by_id = {d.doc_id: d for d in (documents or [])}
    rows: list[dict[str, Any]] = []
    for i, ch in enumerate(chunks):
        doc = doc_by_id.get(ch.doc_id)
        source_id = getattr(doc, "source_id", "") if doc else ""
        tier = getattr(doc, "license_tier", None)
        tier_s = tier.value if hasattr(tier, "value") else str(tier or "TIER_0")
        modality = getattr(ch, "modality", "TEXT")
        modality_s = modality.value if hasattr(modality, "value") else str(modality or "TEXT")
        rows.append(
            {
                "chunk_id": ch.chunk_id,
                "parent_id": getattr(ch, "parent_id", "") or "",
                "document_id": ch.doc_id,
                "section_path": getattr(ch, "section_path", None) or getattr(ch, "section", "") or "",
                "node_kind": getattr(ch, "node_kind", "") or "",
                "content": getattr(ch, "text", "") or "",
                "modality": modality_s,
                "page": int(getattr(ch, "page", 0) or 0),
                "entity_ids": list(
                    getattr(ch, "entity_ids", None) or getattr(ch, "concept_ids", None) or []
                ),
                "milvus_collection": milvus_collection,
                "release_id": release_id,
                "source_id": source_id,
                "license_tier": tier_s,
                "sort_order": int(getattr(ch, "char_start", 0) or i),
            }
        )
    return rows


class MemoryChunkStore:
    """单测 / 无湖环境：包装进程内 chunks。"""

    def __init__(
        self,
        chunks: Sequence[Any],
        *,
        documents: Sequence[Any] | None = None,
        release_id: str = "",
    ) -> None:
        doc_by_id = {d.doc_id: d for d in (documents or [])}
        self._by_id: dict[str, ChunkRecord] = {}
        self._by_doc: dict[str, list[ChunkRecord]] = {}
        for ch in chunks:
            doc = doc_by_id.get(ch.doc_id)
            source_id = getattr(doc, "source_id", "") if doc else ""
            tier = getattr(doc, "license_tier", None)
            tier_s = tier.value if hasattr(tier, "value") else str(tier or "TIER_0")
            title = getattr(doc, "title", "") if doc else ""
            rec = chunk_record_from_kb_chunk(
                ch,
                source_id=source_id,
                license_tier=tier_s,
                release_id=release_id,
                title=title,
            )
            self._by_id[rec.chunk_id] = rec
            self._by_doc.setdefault(rec.document_id, []).append(rec)
        for doc_id, rows in self._by_doc.items():
            self._by_doc[doc_id] = sorted(
                rows, key=lambda r: (r.page, r.sort_order, r.chunk_id)
            )

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        return self.get_chunks([chunk_id]).get(chunk_id)

    def get_chunks(self, chunk_ids: Sequence[str]) -> dict[str, ChunkRecord]:
        out: dict[str, ChunkRecord] = {}
        for cid in dict.fromkeys(str(x) for x in chunk_ids if x):
            rec = self._by_id.get(cid)
            if rec is not None:
                out[cid] = rec
        return out

    def get_section_chunks(self, document_id: str, section_path: str) -> list[ChunkRecord]:
        return [
            r
            for r in self._by_doc.get(document_id, ())
            if r.section_path == section_path
        ]

    def get_document_chunks(self, document_id: str) -> list[ChunkRecord]:
        return list(self._by_doc.get(document_id, ()))


class IcebergChunkStore:
    """Iceberg ``hmd.evidence_chunks`` 批量懒加载。"""

    def __init__(
        self,
        *,
        release_id: str,
        batch_size: int = BATCH_SIZE,
        cache_size: int = 512,
        catalog: Any | None = None,
    ) -> None:
        self.release_id = release_id
        self.batch_size = max(1, int(batch_size))
        self._cache_size = max(0, int(cache_size))
        self._catalog = catalog
        self._cache: OrderedDict[str, ChunkRecord] = OrderedDict()
        self._query_count = 0  # 测试可断言批次数

    @property
    def query_count(self) -> int:
        return self._query_count

    def get_chunk(self, chunk_id: str) -> ChunkRecord | None:
        return self.get_chunks([chunk_id]).get(chunk_id)

    def get_chunks(self, chunk_ids: Sequence[str]) -> dict[str, ChunkRecord]:
        wanted = [str(x) for x in dict.fromkeys(chunk_ids) if x]
        if not wanted:
            return {}

        out: dict[str, ChunkRecord] = {}
        missing: list[str] = []
        for cid in wanted:
            hit = self._cache_get(cid)
            if hit is not None:
                out[cid] = hit
            else:
                missing.append(cid)

        for i in range(0, len(missing), self.batch_size):
            batch = missing[i : i + self.batch_size]
            fetched = self._scan_chunk_ids(batch)
            for cid, rec in fetched.items():
                self._cache_put(cid, rec)
                out[cid] = rec
        return out

    def get_section_chunks(self, document_id: str, section_path: str) -> list[ChunkRecord]:
        rows = self._scan_document(document_id, section_path=section_path)
        for rec in rows:
            self._cache_put(rec.chunk_id, rec)
        return rows

    def get_document_chunks(self, document_id: str) -> list[ChunkRecord]:
        rows = self._scan_document(document_id, section_path=None)
        for rec in rows:
            self._cache_put(rec.chunk_id, rec)
        return rows

    def _cache_get(self, chunk_id: str) -> ChunkRecord | None:
        if chunk_id not in self._cache:
            return None
        self._cache.move_to_end(chunk_id)
        return self._cache[chunk_id]

    def _cache_put(self, chunk_id: str, rec: ChunkRecord) -> None:
        if self._cache_size <= 0:
            return
        self._cache[chunk_id] = rec
        self._cache.move_to_end(chunk_id)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _table(self) -> Any:
        from biomed_ontology.lake.catalog import EVIDENCE_CHUNKS_TABLE, open_catalog

        cat = self._catalog or open_catalog()
        return cat.load_table(EVIDENCE_CHUNKS_TABLE)

    def _scan_chunk_ids(self, chunk_ids: list[str]) -> dict[str, ChunkRecord]:
        from pyiceberg.expressions import And, EqualTo, In

        self._query_count += 1
        table = self._table()
        filt: Any = In("chunk_id", chunk_ids)
        if self.release_id:
            filt = And(filt, EqualTo("release_id", self.release_id))
        arrow = table.scan(row_filter=filt).to_arrow()
        return {rec.chunk_id: rec for rec in _records_from_arrow(arrow)}

    def _scan_document(
        self, document_id: str, *, section_path: str | None
    ) -> list[ChunkRecord]:
        from pyiceberg.expressions import And, EqualTo

        self._query_count += 1
        table = self._table()
        filt: Any = EqualTo("document_id", document_id)
        if self.release_id:
            filt = And(filt, EqualTo("release_id", self.release_id))
        if section_path is not None:
            filt = And(filt, EqualTo("section_path", section_path))
        arrow = table.scan(row_filter=filt).to_arrow()
        rows = _records_from_arrow(arrow)
        return sorted(rows, key=lambda r: (r.page, r.sort_order, r.chunk_id))


def _records_from_arrow(arrow: Any) -> list[ChunkRecord]:
    if arrow is None or arrow.num_rows == 0:
        return []
    rows = arrow.to_pylist()
    out: list[ChunkRecord] = []
    for r in rows:
        entity_ids = r.get("entity_ids") or []
        if not isinstance(entity_ids, (list, tuple)):
            entity_ids = []
        out.append(
            ChunkRecord(
                chunk_id=str(r.get("chunk_id") or ""),
                document_id=str(r.get("document_id") or ""),
                content=str(r.get("content") or ""),
                section_path=str(r.get("section_path") or ""),
                parent_id=str(r.get("parent_id") or ""),
                node_kind=str(r.get("node_kind") or ""),
                modality=str(r.get("modality") or "TEXT"),
                page=int(r.get("page") or 0),
                sort_order=int(r.get("sort_order") or 0),
                source_id=str(r.get("source_id") or ""),
                license_tier=str(r.get("license_tier") or "TIER_0"),
                release_id=str(r.get("release_id") or ""),
                entity_ids=tuple(str(x) for x in entity_ids),
            )
        )
    return out


def chunk_from_record(rec: ChunkRecord) -> Any:
    """``ChunkRecord`` → 文献 ``Chunk``（供 retag / index）。"""
    from biomed_ontology._generated.hmd_fact import ModalityChannelEnum
    from biomed_ontology.corpus import Chunk

    try:
        modality = ModalityChannelEnum(rec.modality or "TEXT")
    except ValueError:
        modality = ModalityChannelEnum.TEXT
    concepts = list(rec.entity_ids)
    return Chunk(
        chunk_id=rec.chunk_id,
        doc_id=rec.document_id,
        text=rec.content,
        section=rec.section_path,
        char_start=rec.sort_order,
        char_end=rec.sort_order + max(len(rec.content), 0),
        modality=modality,
        page=rec.page or 1,
        parent_id=rec.parent_id,
        section_path=rec.section_path,
        node_kind=rec.node_kind,
        concept_ids=list(concepts),
        entity_ids=list(concepts),
    )


def load_chunks_for_index(
    *,
    release_id: str,
    document_ids: list[str] | None = None,
    catalog: Any | None = None,
) -> list[Any]:
    """从 Iceberg ``evidence_chunks`` 装载 Chunk，跳过 corpus rechunk。

    ``document_ids`` 为 None 时装载该 ``release_id`` 下全部行。
    """
    from biomed_ontology.lake.catalog import EVIDENCE_CHUNKS_TABLE, open_catalog
    from pyiceberg.expressions import And, EqualTo, In

    cat = catalog or open_catalog()
    table = cat.load_table(EVIDENCE_CHUNKS_TABLE)
    filt: Any = None
    if release_id:
        filt = EqualTo("release_id", release_id)
    if document_ids:
        doc_filt = In("document_id", list(document_ids))
        filt = And(filt, doc_filt) if filt is not None else doc_filt
    scan = table.scan(row_filter=filt) if filt is not None else table.scan()
    arrow = scan.to_arrow()
    records = _records_from_arrow(arrow)
    records.sort(key=lambda r: (r.document_id, r.page, r.sort_order, r.chunk_id))
    return [chunk_from_record(r) for r in records]
