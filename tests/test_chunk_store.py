"""ChunkStore：批量 get_chunks + Memory 还原路径。"""

from __future__ import annotations

from biomed_ontology.lake.chunk_store import (
    BATCH_SIZE,
    IcebergChunkStore,
    MemoryChunkStore,
    chunk_record_from_kb_chunk,
    chunks_to_evidence_rows,
)
from biomed_ontology.pipeline import build_literature_base
from biomed_ontology.tools.citation import restore_context


def test_memory_get_chunks_is_batch_shaped() -> None:
    kb = build_literature_base(with_graph=False)
    store = MemoryChunkStore(
        kb.chunks, documents=kb.documents, release_id=kb.release_id
    )
    ids = [c.chunk_id for c in kb.chunks[:5]]
    got = store.get_chunks(ids)
    assert set(got) == set(ids)
    assert store.get_chunk(ids[0]) is not None
    assert store.get_chunk("CHK:missing") is None


def test_memory_restore_does_not_need_kb_chunks_in_loop() -> None:
    kb = build_literature_base(with_graph=False)
    store = MemoryChunkStore(
        kb.chunks, documents=kb.documents, release_id=kb.release_id
    )
    anchor = kb.chunks[0]
    restored = restore_context(None, anchor.chunk_id, store=store, max_chars=100_000)
    assert restored.doc_id == anchor.doc_id
    assert anchor.text in restored.full_text
    assert anchor.chunk_id in restored.restored_chunk_ids


def test_chunks_to_evidence_rows_carry_release() -> None:
    kb = build_literature_base(with_graph=False)
    rows = chunks_to_evidence_rows(
        kb.chunks[:3], documents=kb.documents, release_id="rel-test"
    )
    assert len(rows) == 3
    assert all(r["release_id"] == "rel-test" for r in rows)
    assert all(r["document_id"] for r in rows)


def test_iceberg_get_chunks_batches_queries() -> None:
    """用假 table 断言：N 个 id 只打 ceil(N/BATCH) 次 scan，禁止 N+1。"""

    class _FakeTable:
        def __init__(self) -> None:
            self.filters: list[object] = []

        def scan(self, row_filter=None):
            self.filters.append(row_filter)
            return _FakeScan()

    class _FakeScan:
        def to_arrow(self):
            return None  # 空结果；只计查询次数

    table = _FakeTable()
    store = IcebergChunkStore(release_id="r1", batch_size=BATCH_SIZE, cache_size=0)
    store._table = lambda: table  # type: ignore[method-assign]
    ids = [f"CHK:{i}" for i in range(BATCH_SIZE + 3)]
    assert store.get_chunks(ids) == {}
    assert store.query_count == 2
    assert len(table.filters) == 2


def test_chunk_record_from_kb_chunk_maps_section() -> None:
    kb = build_literature_base(with_graph=False)
    ch = kb.chunks[0]
    rec = chunk_record_from_kb_chunk(ch, release_id="r", source_id="SRC")
    assert rec.chunk_id == ch.chunk_id
    assert rec.section == (ch.section or "")
    assert rec.release_id == "r"
