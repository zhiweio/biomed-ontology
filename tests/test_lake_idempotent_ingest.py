"""同 doc_id 重跑：Iceberg / Milvus / GraphDB extracted 幂等。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from biomed_ontology.foundation.graphs import GRAPH_PROVENANCE_EXTRACTED
from biomed_ontology.foundation.models import KnowledgeClaim
from biomed_ontology.foundation.sync import append_extracted_claims
from biomed_ontology.lake import tables as lake_tables
from biomed_ontology.lake.evidence_index import delete_evidence_by_doc, upsert_evidence_objects


def test_append_evidence_chunks_calls_replace_by_document_id() -> None:
    rows = [
        {
            "chunk_id": "CHK:1",
            "document_id": "DOC:A",
            "content": "x",
        }
    ]
    with patch.object(lake_tables, "replace_rows", return_value=1) as replace:
        n = lake_tables.append_evidence_chunks(rows, document_id="DOC:A")
    assert n == 1
    replace.assert_called_once_with(
        lake_tables.EVIDENCE_CHUNKS_TABLE, "document_id", "DOC:A", rows
    )


def test_append_knowledge_claims_empty_still_replaces() -> None:
    with patch.object(lake_tables, "replace_rows", return_value=0) as replace:
        n = lake_tables.append_knowledge_claims([], document_id="DOC:A")
    assert n == 0
    replace.assert_called_once_with(
        lake_tables.KNOWLEDGE_CLAIMS_TABLE, "document_id", "DOC:A", []
    )


def test_append_documents_replace_by_doc_id() -> None:
    rows = [{"doc_id": "DOC:A", "object_uri": "s3://x", "source_id": "pubmed"}]
    with patch.object(lake_tables, "replace_rows", return_value=1) as replace:
        n = lake_tables.append_documents(rows, doc_id="DOC:A")
    assert n == 1
    assert replace.call_args[0][0] == lake_tables.DOCUMENTS_TABLE
    assert replace.call_args[0][1:3] == ("doc_id", "DOC:A")


def test_replace_rows_delete_then_append() -> None:
    import pyarrow as pa

    fake_table = MagicMock()
    schema = MagicMock()
    schema.fields = [MagicMock(name="document_id"), MagicMock(name="chunk_id")]
    schema.as_arrow.return_value = pa.schema(
        [("document_id", pa.large_string()), ("chunk_id", pa.large_string())]
    )
    fake_table.schema.return_value = schema
    cat = MagicMock()
    cat.load_table.return_value = fake_table

    with patch.object(lake_tables, "open_catalog", return_value=cat):
        n = lake_tables.replace_rows(
            "hmd.evidence_chunks",
            "document_id",
            "DOC:A",
            [{"document_id": "DOC:A", "chunk_id": "CHK:1"}],
        )
    assert n == 1
    fake_table.delete.assert_called_once_with("document_id = 'DOC:A'")
    fake_table.append.assert_called_once()


def test_upsert_evidence_objects_purges_doc_before_write() -> None:
    chunk = type(
        "C",
        (),
        {
            "chunk_id": "CHK:1",
            "doc_id": "DOC:A",
            "text": "hello",
            "entity_ids": [],
            "parent_id": "",
            "section_path": "",
            "node_kind": "sentence",
        },
    )()
    client = MagicMock()
    client.has_collection.return_value = True
    with (
        patch("pymilvus.MilvusClient", return_value=client),
        patch(
            "biomed_ontology.lake.evidence_index._ensure_collection",
        ),
        patch(
            "biomed_ontology.lake.evidence_index.delete_evidence_by_doc",
        ) as purge,
    ):
        n = upsert_evidence_objects([chunk], doc_id="DOC:A", uri="http://localhost:19530")
    assert n == 1
    purge.assert_called_once_with("DOC:A", uri="http://localhost:19530")
    client.upsert.assert_called_once()


def test_delete_evidence_by_doc_filter_expr() -> None:
    client = MagicMock()
    client.has_collection.return_value = True
    with patch("pymilvus.MilvusClient", return_value=client):
        delete_evidence_by_doc('DOC:quote"x', uri="http://x")
    kwargs = client.delete.call_args.kwargs
    assert 'doc_id == "DOC:quote\\"x"' in (kwargs.get("filter") or kwargs.get("expr") or "")


def test_append_extracted_claims_deletes_by_source_then_loads_extracted_graph() -> None:
    gdb = MagicMock()
    claims = [
        KnowledgeClaim(
            claim_id="claim:x:1",
            subject_id="HMD:ENT:DC:savolitinib",
            predicate="inhibits",
            object_id="HMD:ENT:TGT:MET",
            claim_status="extracted",
            source_id="DOC:A",
            confidence=0.8,
        ),
        KnowledgeClaim(
            claim_id="claim:x:2",
            subject_id="HMD:ENT:DC:savolitinib",
            predicate="targets",
            object_id="HMD:ENT:TGT:MET",
            claim_status="extracted",
            source_id="DOC:A",
            confidence=0.9,
        ),
    ]
    n = append_extracted_claims(gdb, claims)
    assert n == 2
    assert gdb.update.call_count == 1
    delete_sparql = gdb.update.call_args[0][0]
    assert GRAPH_PROVENANCE_EXTRACTED in delete_sparql
    assert 'hmd:sourceId "DOC:A"' in delete_sparql
    gdb.load_turtle.assert_called_once()
    assert gdb.load_turtle.call_args.kwargs["graph_uri"] == GRAPH_PROVENANCE_EXTRACTED
    ttl = gdb.load_turtle.call_args[0][0]
    assert 'hmd:claimStatus "extracted"' in ttl


def test_append_extracted_claims_skips_validated() -> None:
    gdb = MagicMock()
    n = append_extracted_claims(
        gdb,
        [
            KnowledgeClaim(
                claim_id="claim:v",
                subject_id="HMD:ENT:DC:savolitinib",
                predicate="targets",
                object_id="HMD:ENT:TGT:MET",
                claim_status="validated",
                source_id="DOC:A",
            )
        ],
    )
    assert n == 0
    gdb.update.assert_not_called()
    gdb.load_turtle.assert_not_called()
