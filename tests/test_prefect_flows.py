"""Prefect 生产平面：失败域、人审闸、Zingg 不 stub、eval 合同、sync 不清 extracted。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from biomed_ontology.foundation.graphs import (
    GRAPH_KNOWLEDGE,
    GRAPH_ONTOLOGY,
    GRAPH_PROVENANCE,
    GRAPH_PROVENANCE_EXTRACTED,
)
from biomed_ontology.foundation.sync import SyncResult
from biomed_ontology.foundation.zingg_io import ZinggMaterializeResult
from biomed_ontology.lake.ingest_qa import IngestQAError, IngestQAReport
from biomed_ontology.lake.steps import IngestContext


@pytest.fixture(autouse=True)
def _prefect_home(tmp_path_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path_factory.mktemp("prefect-home")
    qdir = tmp_path_factory.mktemp("quarantine")
    zfp = tmp_path_factory.mktemp("zingg-fp") / "fp.txt"
    monkeypatch.setenv("PREFECT_HOME", str(home))
    monkeypatch.setenv("HMD_QUARANTINE_DIR", str(qdir))
    monkeypatch.setenv("HMD_ZINGG_FP_PATH", str(zfp))
    monkeypatch.delenv("PREFECT_API_URL", raising=False)
    monkeypatch.setenv("PREFECT_LOGGING_LEVEL", "ERROR")


def _qa_error(msg: str = "empty tree") -> IngestQAError:
    return IngestQAError(IngestQAReport(passed=False, blocking=[msg]))


def test_pipeline_cli_registered() -> None:
    from biomed_ontology.cli import app

    result = CliRunner().invoke(app, ["pipeline", "--help"])
    assert result.exit_code == 0
    out = result.stdout
    assert "literature-refresh" in out
    assert "literature-reindex" in out
    assert "identity-match" in out
    assert "data-loop-enrich" in out
    assert "eval" in out
    assert "replay" in out
    assert "ops-snapshot" in out
    assert "ingest" in out
    assert "bios-bootstrap" in out


def test_resolve_repo_path_joins_relative_to_repo() -> None:
    from biomed_ontology.foundation.paths import REPO_ROOT
    from biomed_ontology.lake.steps import resolve_repo_path

    assert resolve_repo_path("data/corpus/pipeline.yaml") == REPO_ROOT / "data/corpus/pipeline.yaml"
    assert resolve_repo_path(REPO_ROOT / "data/corpus/pipeline.yaml") == (
        REPO_ROOT / "data/corpus/pipeline.yaml"
    )
    assert resolve_repo_path(None) is None


def test_batch_one_doc_failure_does_not_count_as_ok(tmp_path: Path) -> None:
    from biomed_ontology.lake.flows import document_batch_ingest

    manifest = tmp_path / "batch.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "documents": [
                    {"source_id": "PUBMED", "doc_id": "DOC:ok", "file": "a.pdf"},
                    {"source_id": "PUBMED", "doc_id": "DOC:qa", "file": "b.pdf"},
                    {"source_id": "PUBMED", "doc_id": "DOC:fail", "file": "c.pdf"},
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_ingest(*, source_id: str, doc_id: str, **_kwargs):
        if doc_id == "DOC:ok":
            return {"doc_id": doc_id, "claim_status": "extracted"}
        if doc_id == "DOC:qa":
            raise _qa_error()
        raise RuntimeError("iceberg down")

    with patch("biomed_ontology.lake.flows.document_ingest", side_effect=fake_ingest):
        result = document_batch_ingest(manifest=str(manifest))

    assert result["ok_n"] == 1
    assert result["failed_n"] == 1
    assert result["quarantined_n"] == 1
    assert result["ok"][0]["doc_id"] == "DOC:ok"
    assert result["quarantined"][0]["doc_id"] == "DOC:qa"
    assert result["failed"][0]["doc_id"] == "DOC:fail"
    assert result["failed"][0]["reason"] == "RuntimeError"


def test_ingest_qa_does_not_write_sinks() -> None:
    from biomed_ontology.lake.flows import document_ingest

    def _parse(ctx: IngestContext, **_kwargs) -> IngestContext:
        ctx.chunks = [type("C", (), {"text": "hello"})()]
        return ctx

    with (
        patch("biomed_ontology.pipelines.preflight.probe_ingest", return_value={}),
        patch("biomed_ontology.lake.flows.put_document"),
        patch("biomed_ontology.lake.flows.parse_and_tree", side_effect=_parse),
        patch("biomed_ontology.lake.flows.run_ingest_qa", side_effect=_qa_error()),
        patch("biomed_ontology.lake.flows.write_evidence") as write_ev,
        patch("biomed_ontology.lake.flows.write_claims") as write_cl,
        patch("biomed_ontology.lake.flows.annotate_bern2") as bern2,
    ):
        with pytest.raises(IngestQAError):
            document_ingest(source_id="PUBMED", doc_id="DOC:x", file_path="x.pdf")
        write_ev.assert_not_called()
        write_cl.assert_not_called()
        bern2.assert_not_called()


def test_write_evidence_iceberg_failure_raises() -> None:
    from biomed_ontology.lake.steps import write_evidence

    ctx = IngestContext(source_id="PUBMED", doc_id="DOC:x")
    ctx.document = MagicMock()
    ctx.chunks = []
    with (
        patch("biomed_ontology.lake.chunk_store.chunks_to_evidence_rows", return_value=[]),
        patch(
            "biomed_ontology.lake.steps.append_evidence_chunks",
            side_effect=OSError("catalog down"),
        ),
        patch("biomed_ontology.lake.steps.upsert_evidence_objects") as milvus,
    ):
        with pytest.raises(RuntimeError, match=r"iceberg\.evidence_chunks"):
            write_evidence(ctx)
        milvus.assert_not_called()


def test_data_loop_enrich_stops_at_pending_approval() -> None:
    from biomed_ontology.pipelines.data_loop import data_loop_enrich

    enrich = SimpleNamespace(
        to_dict=lambda: {"proposals": 2, "dismissed": 0},
    )
    with (
        patch("biomed_ontology.foundation.evolve_propose.run_enrich", return_value=enrich),
        patch("biomed_ontology.foundation.evolve_apply.apply_approved") as apply,
    ):
        out = data_loop_enrich(use_llm=False)
    assert out["status"] == "pending_approval"
    assert out["auto_apply"] is False
    apply.assert_not_called()


def test_data_loop_apply_requires_approved() -> None:
    from biomed_ontology.pipelines.data_loop import data_loop_apply

    with (
        patch(
            "biomed_ontology.foundation.evolve_apply.load_proposals",
            return_value=(Path("proposals.jsonl"), [{"status": "pending_approval"}]),
        ),
        pytest.raises(RuntimeError, match="no approved proposals"),
    ):
        data_loop_apply(write=False, publish=False)


def test_identity_match_prod_never_stubs() -> None:
    from biomed_ontology.pipelines.identity_match import identity_match

    mat = ZinggMaterializeResult(
        enterprise_path=Path("data/zingg/input/enterprise.parquet"),
        observation_path=Path("data/zingg/input/observation.parquet"),
        enterprise_rows=3,
        observation_rows=2,
        sources=["lake"],
    )
    with (
        patch("biomed_ontology.foundation.zingg_io.materialize", return_value=mat),
        patch(
            "biomed_ontology.foundation.zingg_io.run_zingg_docker",
            side_effect=RuntimeError("zingg-link failed rc=1 (production must not stub)"),
        ),
        patch("biomed_ontology.foundation.zingg_io.link_stub_from_materialized") as stub,
        patch("biomed_ontology.foundation.zingg_io.export_matches") as export,
    ):
        with pytest.raises(RuntimeError, match="must not stub"):
            identity_match(skip_smoke=True)
        stub.assert_not_called()
        export.assert_not_called()


def test_identity_match_lake_empty_fails_without_bootstrap() -> None:
    from biomed_ontology.pipelines.identity_match import identity_match

    mat = ZinggMaterializeResult(
        enterprise_path=Path("e.parquet"),
        observation_path=Path("o.parquet"),
        enterprise_rows=4,
        observation_rows=0,
        sources=["lake"],
    )
    with (
        patch("biomed_ontology.foundation.zingg_io.materialize", return_value=mat),
        patch("biomed_ontology.foundation.zingg_io.run_zingg_docker") as docker,
        patch("biomed_ontology.foundation.zingg_io.link_stub_from_materialized") as stub,
    ):
        with pytest.raises(RuntimeError, match="lake observations empty"):
            identity_match(observations="lake", skip_smoke=True)
        docker.assert_not_called()
        stub.assert_not_called()


def test_identity_match_dev_may_stub() -> None:
    from biomed_ontology.pipelines.identity_match import identity_match_dev

    mat = ZinggMaterializeResult(
        enterprise_path=Path("e.parquet"),
        observation_path=Path("o.parquet"),
        enterprise_rows=1,
        observation_rows=1,
        sources=["bootstrap"],
    )
    with (
        patch("biomed_ontology.foundation.zingg_io.materialize", return_value=mat),
        patch(
            "biomed_ontology.foundation.zingg_io.link_stub_from_materialized",
            return_value=Path("data/zingg/raw_matches.jsonl"),
        ),
        patch("biomed_ontology.foundation.zingg_io.export_matches", return_value={"n": 1}),
        patch("biomed_ontology.foundation.zingg_io.run_zingg_docker") as docker,
    ):
        out = identity_match_dev(skip_smoke=True)
    assert out["allow_stub"] is True
    assert out["link"]["mode"] == "stub"
    docker.assert_not_called()


def test_cli_zingg_may_fallback_to_stub() -> None:
    from biomed_ontology.pipelines.identity_match import run_zingg_link_for_cli

    with (
        patch(
            "biomed_ontology.foundation.zingg_io.run_zingg_docker",
            side_effect=RuntimeError("docker down"),
        ),
        patch("biomed_ontology.foundation.zingg_io.link_stub_from_materialized") as stub,
    ):
        assert run_zingg_link_for_cli(skip_docker=False) == "stub_fallback"
        stub.assert_called_once()


def test_task_facet_golden_all_passed_is_ok() -> None:
    from biomed_ontology.pipelines.ontology_eval import task_facet_golden

    summary = {"total": 6, "passed": 6, "failed": [], "paths": []}
    with patch(
        "biomed_ontology.foundation.golden_eval.eval_golden_paths",
        return_value=summary,
    ):
        out = task_facet_golden.fn()
    assert out["ok"] is True
    assert out["summary"]["passed"] == 6


def test_ontology_eval_facet_failure_fails_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from biomed_ontology.pipelines import ontology_eval as oe

    monkeypatch.setattr(oe, "EVAL_DIR", tmp_path)
    with (
        patch.object(oe, "task_facet_validate", return_value={"ok": True, "facet": "validate"}),
        patch.object(
            oe,
            "task_facet_identity",
            side_effect=RuntimeError("identity gate failed"),
        ),
        patch.object(oe, "task_facet_extraction") as extraction,
    ):
        with pytest.raises(RuntimeError, match="identity gate failed"):
            oe.ontology_eval(suite="cheap")
        extraction.assert_not_called()
    assert list(tmp_path.glob("*.json")) == []


def test_sync_world_model_does_not_clear_extracted() -> None:
    from biomed_ontology.foundation import sync as sync_mod

    wm = SimpleNamespace(entities={}, claims=[], assets=[])
    gdb = MagicMock()
    gdb.health.return_value = True
    cleared: list[str] = []
    gdb.clear_graph.side_effect = lambda uri: cleared.append(uri)

    with (
        patch.object(sync_mod, "ensure_repository"),
        patch.object(sync_mod, "_upsert_evidence_milvus", return_value=0),
        patch.object(sync_mod, "OpenMetadataClient") as om_cls,
    ):
        om = om_cls.from_settings.return_value
        om.ping.side_effect = RuntimeError("om skip")
        result = sync_mod.sync_world_model(
            world=wm,  # ty: ignore[invalid-argument-type]
            graphdb=gdb,
            require_graphdb=True,
            require_milvus=False,
            require_om=False,
        )

    assert GRAPH_PROVENANCE_EXTRACTED not in cleared
    assert GRAPH_ONTOLOGY in cleared
    assert GRAPH_KNOWLEDGE in cleared
    assert GRAPH_PROVENANCE in cleared
    assert "extracted graph preserved" in " ".join(result.details)
    source = Path(sync_mod.__file__).read_text(encoding="utf-8")
    assert "clear_graph(GRAPH_PROVENANCE_EXTRACTED)" not in source


def test_world_model_sync_calls_sync_once() -> None:
    from biomed_ontology.pipelines.world_model import world_model_sync

    ok = SyncResult(
        entities=1,
        claims=1,
        evidence_upserted=0,
        assets=0,
        graphdb_ok=True,
        milvus_ok=True,
        om_ok=True,
        details=["ok"],
    )
    with (
        patch("biomed_ontology.pipelines.preflight.probe_foundation", return_value={}),
        patch("biomed_ontology.foundation.sync.sync_world_model", return_value=ok) as sync,
        patch(
            "biomed_ontology.pipelines.world_model.compute_world_model_fingerprint",
            return_value="abc",
        ),
        patch("biomed_ontology.pipelines.world_model._save_fingerprint"),
    ):
        out = world_model_sync()
    assert sync.call_count == 1
    assert out["extracted_graph_cleared"] is False
    assert out["fingerprint"] == "abc"


def test_catalog_publish_noop_when_fingerprint_unchanged() -> None:
    from biomed_ontology.pipelines.world_model import catalog_publish

    with (
        patch(
            "biomed_ontology.pipelines.world_model.compute_world_model_fingerprint",
            return_value="same",
        ),
        patch("biomed_ontology.pipelines.world_model._load_fingerprint", return_value="same"),
        patch("biomed_ontology.pipelines.world_model.world_model_sync") as sync,
    ):
        out = catalog_publish()
    assert out["skipped"] is True
    sync.assert_not_called()


def test_discover_dirty_docs_uses_sha256_sidecar(tmp_path: Path) -> None:
    from biomed_ontology.pipelines.literature import discover_dirty_docs

    raw = tmp_path / "raw"
    out = tmp_path / "parsed"
    raw.mkdir()
    out.mkdir()
    pdf = raw / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    (raw / "corpus.json").write_text(
        json.dumps([{"doc_id": "DOC:A", "pdf": "a.pdf"}]),
        encoding="utf-8",
    )

    dirty = discover_dirty_docs(raw_dir=raw, out_dir=out)
    assert len(dirty) == 1
    assert dirty[0]["reason"] == "unparsed"

    checksum = hashlib.sha256(pdf.read_bytes()).hexdigest()
    (out / "DOC_A.yaml").write_text("title: x\n", encoding="utf-8")
    (out / "DOC_A.sha256").write_text(checksum + "\n", encoding="utf-8")
    assert discover_dirty_docs(raw_dir=raw, out_dir=out) == []

    pdf.write_bytes(b"%PDF-1.4 changed")
    dirty = discover_dirty_docs(raw_dir=raw, out_dir=out)
    assert dirty[0]["reason"] == "checksum_changed"


def test_literature_qa_failure_quarantines_without_index() -> None:
    from biomed_ontology.pipelines.literature import literature_refresh

    item = {
        "doc_id": "DOC:A",
        "reason": "unparsed",
        "pdf": "a.pdf",
        "record": {"doc_id": "DOC:A", "pdf": "a.pdf"},
    }
    state = SimpleNamespace(catalog_sha256="fp")
    with (
        patch(
            "biomed_ontology.pipelines.literature.discover_dirty_docs",
            return_value=[item],
        ),
        patch("biomed_ontology.pipelines.literature.task_parse_document", return_value={}),
        patch(
            "biomed_ontology.pipelines.literature.task_literature_qa",
            side_effect=_qa_error(),
        ),
        patch("biomed_ontology.pipelines.literature.task_refresh_document") as refresh,
        patch(
            "biomed_ontology.pipelines.literature.compute_catalog_fingerprint",
            return_value="fp",
        ),
        patch("biomed_ontology.pipelines.literature.load_state", return_value=state),
        patch("biomed_ontology.pipelines.literature.task_catalog_incremental") as incr,
    ):
        out = literature_refresh()
    assert out["quarantined"][0]["doc_id"] == "DOC:A"
    assert out["ok"] == []
    refresh.assert_not_called()
    incr.assert_not_called()


def test_lake_steps_still_do_not_import_pipeline() -> None:
    from biomed_ontology.lake import steps as lake_steps

    text = Path(lake_steps.__file__).read_text(encoding="utf-8")
    assert "biomed_ontology.pipeline" not in text
    assert "build_literature_base" not in text
