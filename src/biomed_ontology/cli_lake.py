"""Document Lake CLI 子应用（hmd lake …）。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from biomed_ontology.cli_ui import command_header, console, metrics_table, tqdm_bar

_err_console = Console(stderr=True)

lake_app = typer.Typer(
    help="Document Lake：MinIO / Iceberg / Trino / 双写 ingest（BERN2 必接）",
    no_args_is_help=True,
)


@lake_app.command("ensure")
def lake_ensure() -> None:
    """确保 MinIO buckets 存在。"""
    from biomed_ontology.lake.minio_store import ensure_buckets

    created = ensure_buckets()
    console.print(f"buckets ok created={created or 'none'}")


@lake_app.command("init")
def lake_init() -> None:
    """创建 Iceberg 湖表（含 obs_tool_io / er_observations；需 REST + MinIO）。"""
    from biomed_ontology.lake.catalog import ensure_lake_tables
    from biomed_ontology.lake.minio_store import ensure_buckets

    ensure_buckets()
    created = ensure_lake_tables()
    console.print(f"iceberg tables ok created={created or 'already'}")


@lake_app.command("trino-smoke")
def lake_trino_smoke() -> None:
    """SHOW TABLES FROM iceberg.hmd。"""
    from trino.dbapi import connect

    from biomed_ontology.config import settings

    conn = connect(
        host=settings.trino_host,
        port=settings.trino_port,
        user="hmd",
        catalog=settings.trino_catalog,
        schema=settings.trino_schema,
    )
    cur = conn.cursor()
    cur.execute(f"SHOW TABLES FROM {settings.trino_catalog}.{settings.trino_schema}")
    rows = cur.fetchall()
    console.print(f"trino tables: {[r[0] for r in rows]}")


@lake_app.command("om-ingest")
def lake_om_ingest() -> None:
    """确保 OM Trino DatabaseService（官方 connector 路径）。"""
    from biomed_ontology.lake.om_governance import trigger_trino_metadata_ingest

    result = trigger_trino_metadata_ingest()
    console.print(result)
    if not result.get("ok"):
        raise typer.Exit(1)


@lake_app.command("ingest-doc")
def lake_ingest_doc(
    source: str = typer.Option(..., "--source"),
    doc_id: str = typer.Option(..., "--doc-id"),
    file: Path | None = typer.Option(None, "--file", exists=True, dir_okay=False),
    corpus_yaml: Path | None = typer.Option(None, "--corpus-yaml", exists=True),
    bern2_url: str | None = typer.Option(None, "--bern2-url"),
    no_asset: bool = typer.Option(False, "--no-asset"),
) -> None:
    """单文档双写（纯函数；claim_status=extracted）。"""
    from biomed_ontology.lake.ingest import ingest_document

    command_header(
        "lake ingest-doc",
        meta=[("source", source), ("doc_id", doc_id)],
        console=_err_console,
    )
    result = ingest_document(
        source_id=source,
        doc_id=doc_id,
        file_path=file,
        corpus_yaml=corpus_yaml,
        bern2_url=bern2_url,
        register_asset=not no_asset,
    )
    metrics_table(
        "ingest-doc",
        [
            ("doc_id", str(result.get("doc_id") or doc_id)),
            ("claim_status", str(result.get("claim_status") or "-")),
            ("errors", str(len(result.get("errors") or []))),
        ],
        console=_err_console,
    )
    console.print_json(data=result)
    if result.get("errors"):
        raise typer.Exit(2)


@lake_app.command("ingest-flow")
def lake_ingest_flow(
    source: str = typer.Option(..., "--source"),
    doc_id: str = typer.Option(..., "--doc-id"),
    file: Path | None = typer.Option(None, "--file"),
    corpus_yaml: Path | None = typer.Option(None, "--corpus-yaml"),
    bern2_url: str | None = typer.Option(None, "--bern2-url"),
) -> None:
    """Prefect Flow 编排单文档双写。"""
    from biomed_ontology.lake.flows import document_dual_write_flow

    command_header(
        "lake ingest-flow",
        meta=[("source", source), ("doc_id", doc_id)],
        console=_err_console,
    )
    result = document_dual_write_flow(
        source_id=source,
        doc_id=doc_id,
        file_path=str(file) if file else None,
        corpus_yaml=str(corpus_yaml) if corpus_yaml else None,
        bern2_url=bern2_url,
    )
    metrics_table(
        "ingest-flow",
        [
            ("doc_id", str(result.get("doc_id") or doc_id)),
            ("claim_status", str(result.get("claim_status") or "-")),
            ("errors", str(len(result.get("errors") or []))),
        ],
        console=_err_console,
    )
    console.print_json(data=result)


@lake_app.command("ingest-batch")
def lake_ingest_batch(
    manifest: Path = typer.Option(..., "--manifest", exists=True),
    bern2_url: str | None = typer.Option(None, "--bern2-url"),
) -> None:
    """Prefect 批量双写。"""
    from biomed_ontology.lake.flows import document_batch_ingest_flow

    command_header(
        "lake ingest-batch",
        meta=[("manifest", str(manifest))],
        console=_err_console,
    )
    with tqdm_bar(desc="lake ingest", unit="doc") as bar:
        last = 0

        def _on_item(done: int, total: int) -> None:
            nonlocal last
            if bar.total != total:
                bar.total = total
            bar.update(done - last)
            last = done

        result = document_batch_ingest_flow(
            manifest=str(manifest),
            bern2_url=bern2_url,
            on_item=_on_item,
        )
    failed_n = int(result.get("failed_n") or 0)
    quarantined_n = int(result.get("quarantined_n") or 0)
    metrics_table(
        "ingest-batch",
        [
            ("ok", str(result.get("ok_n") or 0)),
            ("failed", str(failed_n)),
            ("quarantined", str(quarantined_n)),
        ],
        console=_err_console,
    )
    console.print_json(data=result)
    if failed_n or quarantined_n:
        raise typer.Exit(1)
