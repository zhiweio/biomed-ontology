"""生产 DAG 的 CLI 单步入口（不启 Prefect Server 也可 ``flow()``）。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import typer

from biomed_ontology.cli_ui import command_header, console

pipeline_app = typer.Typer(
    help="Prefect 生产平面：文献 / 入仓 / sync / Zingg / data loop / 评测合同",
    no_args_is_help=True,
)


@pipeline_app.command("literature-refresh")
def pipeline_literature_refresh(
    source_id: str = typer.Option("PUBMED", "--source"),
    embedder: str = typer.Option("multimodal-bio", "--embedder"),
) -> None:
    from biomed_ontology.pipelines.literature import literature_refresh

    command_header("pipeline literature-refresh")
    result = literature_refresh(source_id=source_id, embedder_name=embedder)
    console.print_json(data=result)
    if result.get("failed") or result.get("quarantined"):
        raise typer.Exit(1)


@pipeline_app.command("sync")
def pipeline_sync() -> None:
    from biomed_ontology.pipelines.world_model import world_model_sync

    command_header("pipeline sync")
    console.print_json(data=world_model_sync())


@pipeline_app.command("catalog-publish")
def pipeline_catalog_publish(
    rematerialize_zingg: bool = typer.Option(False, "--zingg"),
) -> None:
    from biomed_ontology.pipelines.world_model import catalog_publish

    command_header("pipeline catalog-publish")
    console.print_json(data=catalog_publish(rematerialize_zingg=rematerialize_zingg))


@pipeline_app.command("identity-match")
def pipeline_identity_match(
    dev: bool = typer.Option(False, "--dev", help="允许 stub-link"),
    observations: str = typer.Option("lake", "--observations"),
) -> None:
    from biomed_ontology.pipelines.identity_match import identity_match, identity_match_dev

    command_header("pipeline identity-match", meta=[("dev", str(dev))])
    allowed = {"lake", "bootstrap", "all"}
    if observations not in allowed:
        raise typer.BadParameter("observations must be lake|bootstrap|all")
    obs = cast(Literal["lake", "bootstrap", "all"], observations)
    result = identity_match_dev(observations=obs) if dev else identity_match(observations=obs)
    console.print_json(data=result)


@pipeline_app.command("data-loop-mine")
def pipeline_mine() -> None:
    from biomed_ontology.pipelines.data_loop import data_loop_mine

    command_header("pipeline data-loop-mine")
    console.print_json(data=data_loop_mine())


@pipeline_app.command("data-loop-enrich")
def pipeline_enrich(
    no_llm: bool = typer.Option(False, "--no-llm"),
) -> None:
    from biomed_ontology.pipelines.data_loop import data_loop_enrich

    command_header("pipeline data-loop-enrich")
    console.print_json(data=data_loop_enrich(use_llm=not no_llm))


@pipeline_app.command("data-loop-apply")
def pipeline_apply(
    write: bool = typer.Option(False, "--write"),
    proposals: Path | None = typer.Option(None, "--proposals"),
) -> None:
    from biomed_ontology.pipelines.data_loop import data_loop_apply

    command_header("pipeline data-loop-apply", meta=[("write", str(write))])
    console.print_json(
        data=data_loop_apply(write=write, proposals=str(proposals) if proposals else None)
    )


@pipeline_app.command("eval")
def pipeline_eval(
    suite: str = typer.Option("cheap", "--suite", help="cheap | release"),
) -> None:
    from biomed_ontology.pipelines.ontology_eval import ontology_eval

    command_header("pipeline eval", meta=[("suite", suite)])
    console.print_json(data=ontology_eval(suite=suite))
