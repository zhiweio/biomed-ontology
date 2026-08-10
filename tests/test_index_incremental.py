"""文献面增量 index：fingerprint / retag dirty / 保向量 upsert。"""

from __future__ import annotations

from pathlib import Path

from biomed_ontology._generated.hmd_fact import ModalityChannelEnum
from biomed_ontology.corpus import Chunk
from biomed_ontology.index_refresh import (
    concept_label_terms,
    diff_retag,
    refresh_catalog_incremental,
)
from biomed_ontology.index_state import (
    LiteratureIndexState,
    compute_catalog_fingerprint,
    load_state,
    save_state,
)
from biomed_ontology.pipeline import build_normalizer_from_catalog, retag_chunks


def _chunk(
    cid: str,
    text: str,
    *,
    concepts: list[str] | None = None,
    expanded: list[str] | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=cid,
        doc_id="DOC_T",
        text=text,
        section="Results",
        char_start=0,
        char_end=len(text),
        modality=ModalityChannelEnum.TEXT,
        concept_ids=list(concepts or []),
        concept_ids_expanded=list(expanded or []),
        entity_ids=list(concepts or []),
        section_path="Results",
    )


def test_catalog_fingerprint_stable():
    a = compute_catalog_fingerprint()
    b = compute_catalog_fingerprint()
    assert a == b
    assert len(a) == 64


def test_index_state_roundtrip(tmp_path: Path):
    path = tmp_path / "state.json"
    save_state(
        LiteratureIndexState(
            catalog_sha256="abc",
            release_id="0.3.0-ent",
            embedder="fake",
            collection="hmd_chunks",
            chunk_count=3,
            dirty_last_run=1,
        ),
        path,
    )
    loaded = load_state(path)
    assert loaded is not None
    assert loaded.catalog_sha256 == "abc"
    assert loaded.chunk_count == 3


def test_diff_retag_alias_hit_ids_only():
    before = [_chunk("c1", "HMPL-504 inhibits MET", concepts=[])]
    after = [
        _chunk(
            "c1",
            "HMPL-504 inhibits MET",
            concepts=["HMD:ENT:DC:savolitinib"],
            expanded=["HMD:ENT:TGT:MET"],
        )
    ]

    def labels(ch: Chunk) -> list[str]:
        return list(ch.concept_ids)  # surrogate: IDs as labels for unit test

    dirty = diff_retag(before, after, before_label_fn=labels, after_label_fn=labels)
    assert len(dirty) == 1
    assert dirty[0].needs_reembed is True  # labels (IDs) changed
    # same labels → no reembed
    before2 = [_chunk("c1", "x", concepts=["HMD:ENT:DC:savolitinib"], expanded=[])]
    after2 = [
        _chunk(
            "c1",
            "x",
            concepts=["HMD:ENT:DC:savolitinib"],
            expanded=["HMD:ENT:TGT:MET"],
        )
    ]

    def lab2(ch: Chunk) -> list[str]:
        return ["savolitinib"]  # preferred label unchanged

    dirty2 = diff_retag(before2, after2, before_label_fn=lab2, after_label_fn=lab2)
    assert len(dirty2) == 1
    assert dirty2[0].needs_reembed is False


def test_retag_chunks_uses_catalog():
    kb = build_normalizer_from_catalog()
    chunks = [_chunk("c1", "savolitinib inhibits MET in NSCLC")]
    out = retag_chunks(chunks, kb.normalizer, hub=kb.hub, release_id=kb.release_id)
    assert out[0].concept_ids
    assert any("savolitinib" in c.lower() or c.startswith("HMD:ENT:") for c in out[0].concept_ids)


def test_incremental_noop_when_fingerprint_matches(tmp_path: Path):
    fp = compute_catalog_fingerprint()
    state_path = tmp_path / "literature_index_state.json"
    save_state(
        LiteratureIndexState(
            catalog_sha256=fp,
            release_id="0.3.0-ent",
            collection="hmd_chunks",
            chunk_count=10,
        ),
        state_path,
    )
    result = refresh_catalog_incremental(
        embedder_name="fake",
        state_path=state_path,
        skip_milvus=True,
        skip_iceberg=True,
    )
    assert result.skipped is True
    assert "fingerprint" in result.reason


def test_milvus_upsert_encode_false_keeps_vectors():
    from biomed_ontology.embed import FakeEmbedder
    from biomed_ontology.search.backends.milvus import MilvusBackend

    class FakeClient:
        def __init__(self) -> None:
            self.data: list[dict] = []
            self.has = True

        def has_collection(self, _name: str) -> bool:
            return self.has

        def describe_collection(self, _name: str) -> dict:
            return {
                "description": "embedder=fake;release=0.3.0-ent",
                "fields": [
                    {"name": "chunk_id"},
                    {"name": "dense_general"},
                    {"name": "sparse_lexical"},
                ],
            }

        def upsert(self, *, collection_name: str, data: list) -> None:
            self.data.extend(data)

        def flush(self, _name: str) -> None:
            return None

        def query(self, **kwargs):
            return []

        def delete(self, **kwargs):
            return None

    client = FakeClient()
    backend = MilvusBackend(
        collection="hmd_chunks",
        embedder=FakeEmbedder(),
        client=client,
        release_id="0.3.0-ent",
    )
    row = {
        "chunk_id": "c1",
        "doc_id": "d1",
        "source_id": "SRC",
        "license_rank": 0,
        "section_id": "",
        "section_path": "",
        "sort_order": 0,
        "page": 1,
        "modality": "TEXT",
        "degraded": "",
        "asset_path": "",
        "figure_type": "",
        "labels": [],
        "concept_ids_expanded": ["HMD:ENT:DC:savolitinib"],
        "text": "hello",
        "release_id": "0.3.0-ent",
        "dense_general": [0.1] * 8,
        "sparse_lexical": {"1": 1.0},
    }
    n = backend.upsert([row], encode=False)
    assert n == 1
    assert client.data[0]["dense_general"] == [0.1] * 8
    assert "concept_ids_expanded" in client.data[0]


def test_milvus_upsert_on_batch_callback():
    from biomed_ontology.embed import FakeEmbedder
    from biomed_ontology.search.backends.milvus import MilvusBackend

    class FakeClient:
        def upsert(self, *, collection_name: str, data: list) -> None:
            return None

        def flush(self, _name: str) -> None:
            return None

        def has_collection(self, _name: str) -> bool:
            return True

        def describe_collection(self, _name: str) -> dict:
            return {
                "description": "embedder=fake;release=0.3.0-ent",
                "fields": [{"name": "chunk_id"}, {"name": "dense_general"}],
            }

    backend = MilvusBackend(
        collection="hmd_chunks",
        embedder=FakeEmbedder(),
        client=FakeClient(),
        release_id="0.3.0-ent",
    )
    rows = [
        {
            "chunk_id": f"c{i}",
            "doc_id": "d1",
            "source_id": "SRC",
            "license_rank": 0,
            "section_id": "",
            "section_path": "",
            "sort_order": i,
            "page": 1,
            "modality": "TEXT",
            "degraded": "",
            "asset_path": "",
            "figure_type": "",
            "labels": [],
            "concept_ids_expanded": [],
            "text": f"t{i}",
            "release_id": "0.3.0-ent",
            "dense_general": [0.1] * 8,
            "sparse_lexical": {"1": 1.0},
        }
        for i in range(5)
    ]
    seen: list[tuple[int, int]] = []
    n = backend.upsert(
        rows,
        encode=False,
        batch_size=2,
        on_batch=lambda written, total: seen.append((written, total)),
    )
    assert n == 5
    assert seen == [(2, 5), (4, 5), (5, 5)]


def test_concept_label_terms_from_kb():
    kb = build_normalizer_from_catalog()
    # pick any concept
    cid = kb.concepts[0].concept_id
    ch = _chunk("c1", "x", concepts=[cid])
    terms = concept_label_terms(kb, ch)
    assert terms
