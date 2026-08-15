"""Foundation CLI 子应用（hmd foundation …）。"""

from __future__ import annotations

from pathlib import Path

import typer

from biomed_ontology.cli_ui import command_header, console, metrics_table, tqdm_bar


def _optional_graph_entity(enterprise_id: str) -> dict | None:
    """CLI resolve 展示用：GraphDB 可达时合并 altLabel；失败返回 None。"""
    from biomed_ontology.foundation.graphdb import GraphDbClient
    from biomed_ontology.foundation.store import fetch_entity

    try:
        gdb = GraphDbClient.from_settings()
        if not gdb.health():
            return None
        ent = fetch_entity(gdb, enterprise_id)
    except Exception:
        return None
    return ent.to_dict() if ent is not None else None


foundation_app = typer.Typer(
    help="Enterprise Biomedical World Model（Foundation）",
    no_args_is_help=True,
)


@foundation_app.command("golden")
def foundation_golden(
    candidate: str = typer.Option("HMPL-504", "--candidate", help="候选药别名或企业 ID"),
    json_out: bool = typer.Option(False, "--json", help="输出完整 JSON（机器可读）"),
    compact: bool = typer.Option(False, "--compact", help="仅 Trace 步骤条，不展开详情"),
) -> None:
    """金路径双面验收：WM resolve/context + KB search_documents/restore。

    默认用 Rich 分步展示；`--json` 给脚本，`--compact` 只要计数摘要。
    """
    import json

    from biomed_ontology.foundation.obs_log import configure_foundation_logging
    from biomed_ontology.foundation.render import render_golden_path
    from biomed_ontology.runtime import open_dual_surface

    configure_foundation_logging(json_logs=True)
    surface = open_dual_surface()
    result = surface.foundation.golden_path(candidate, tools=surface.tools)
    if json_out:
        console.print_json(json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            raise typer.Exit(1)
        return
    render_golden_path(result, console=console, verbose=not compact)
    if not result.get("ok"):
        raise typer.Exit(1)


@foundation_app.command("golden-eval")
def foundation_golden_eval(
    candidate: list[str] | None = typer.Argument(
        None,
        help="候选列表；默认 HMPL-504/savolitinib/AZD6094/MET/c-MET/NSCLC",
    ),
    json_out: bool = typer.Option(False, "--json", help="输出完整 JSON（机器可读）"),
    compact: bool = typer.Option(False, "--compact", help="仅 Suite 汇总表，不展开逐路径 checks"),
) -> None:
    """多 Golden Path 双面评估：WM 三后端 + KB 文献腿，禁止 YAML。

    默认用 Rich 汇总展示；`--json` 给脚本，`--compact` 只要 Suite 表。
    """
    import json

    from biomed_ontology.foundation.golden_eval import DEFAULT_CANDIDATES, eval_golden_paths
    from biomed_ontology.foundation.obs_log import configure_foundation_logging
    from biomed_ontology.foundation.render import render_golden_eval
    from biomed_ontology.runtime import open_dual_surface

    configure_foundation_logging(json_logs=True)
    surface = open_dual_surface()
    summary = eval_golden_paths(
        list(candidate) if candidate else list(DEFAULT_CANDIDATES),
        api=surface.foundation,
        tools=surface.tools,
    )
    ok = summary["passed"] == summary["total"]
    if json_out:
        console.print_json(json.dumps(summary, ensure_ascii=False))
        if not ok:
            raise typer.Exit(1)
        return
    render_golden_eval(summary, console=console, verbose=not compact)
    if not ok:
        raise typer.Exit(1)


@foundation_app.command("resolve")
def foundation_resolve(
    text: str = typer.Argument(..., help="待解析文本"),
    json_out: bool = typer.Option(False, "--json", help="输出完整 JSON（含反查别名）"),
) -> None:
    """resolve_entity：词典/候选 → Enterprise Entity ID，并反查全部别名。

    默认 Rich 展示命中 + 实体卡 + 别名全集；`--json` 给脚本。
    """
    import json

    from biomed_ontology.foundation import FoundationApi, load_world_model
    from biomed_ontology.foundation.obs_log import configure_foundation_logging
    from biomed_ontology.foundation.render import enrich_resolve, render_resolve

    configure_foundation_logging(json_logs=True)
    world = load_world_model()
    api = FoundationApi(world)
    raw = api.resolve_entity(text)
    out = enrich_resolve(raw, world=world, fetch_graph_entity=_optional_graph_entity)
    if json_out:
        console.print_json(json.dumps(out, ensure_ascii=False))
        return
    render_resolve(out, console=console)


@foundation_app.command("lookup-bios")
def foundation_lookup_bios(
    query: str | None = typer.Option(None, "--query", "-q", help="自由文本 / 别名（如 阿司匹林）"),
    external_id: str | None = typer.Option(
        None, "--external-id", "-e", help="公开 CURIE（如 CHEBI:DEMO_ASPIRIN）"
    ),
    bios_curie: str | None = typer.Option(None, "--bios-curie", "-b", help="BIOS:… CURIE"),
    json_out: bool = typer.Option(False, "--json", help="输出完整 JSON"),
) -> None:
    """lookup_bios_concept：公开 BIOS 概念卡（无需 / 不 mint HMD:ENT:*）。"""
    import json

    from biomed_ontology.foundation import FoundationApi, load_world_model
    from biomed_ontology.foundation.obs_log import configure_foundation_logging
    from biomed_ontology.foundation.render import render_lookup_bios

    if not any((query, external_id, bios_curie)):
        console.print("[red]需要 --query / --external-id / --bios-curie 之一[/red]")
        raise typer.Exit(2)

    configure_foundation_logging(json_logs=True)
    world = load_world_model()
    api = FoundationApi(world)
    out = api.lookup_bios_concept(
        query=query,
        external_id=external_id,
        bios_curie=bios_curie,
    )
    if json_out:
        console.print_json(json.dumps(out, ensure_ascii=False))
        if not out.get("found"):
            raise typer.Exit(1)
        return
    label = query or external_id or bios_curie or ""
    render_lookup_bios(out, console=console, query_label=str(label))
    if not out.get("found"):
        raise typer.Exit(1)


@foundation_app.command("bios-load")
def foundation_bios_load(
    full: bool = typer.Option(True, "--full/--subset", help="默认全量下载初始化"),
    force: bool = typer.Option(
        False,
        "--force",
        help="忽略 .initialized，强制重新灌库（默认已初始化则跳过）",
    ),
) -> None:
    """BIOS_v3 默认全量下载并灌 GraphDB（需 Settings.bios_license_ack）。

    已有 ``data/cache/bios_v3/.initialized`` 且 GraphDB 仍有 Concept 时跳过；
    ``--force`` 或 ``HMD_BIOS_FORCE=1`` 强制重灌。
    """
    import os

    from biomed_ontology.config import settings
    from biomed_ontology.foundation.bios import (
        BiosLicenseGate,
        initialize_bios,
        read_bios_init_marker,
    )
    from biomed_ontology.foundation.graphdb import GraphDbClient

    want_full = full and settings.bios_init != "subset"
    force = force or os.environ.get("HMD_BIOS_FORCE", "").strip() in {
        "1",
        "true",
        "yes",
    }
    max_concepts = int(settings.bios_max_concepts or 0)
    command_header(
        "foundation bios-load",
        meta=[
            ("mode", "full" if want_full else "subset"),
            ("force", str(force)),
            ("max_concepts", str(max_concepts) if max_concepts else "unlimited"),
        ],
    )
    # 已初始化且非 force：可跳过 ACK；真正重灌时仍要求
    if want_full and not settings.bios_license_ack and (force or read_bios_init_marker() is None):
        console.print(
            "[red]需要 HMD_BIOS_LICENSE_ACK=poc|evaluation|licensed[/red]\n"
            "见 data/foundation/NOTICE_BIOS.md\n"
            "仅子集：HMD_BIOS_INIT=subset"
        )
        raise typer.Exit(2)

    with tqdm_bar(
        total=max_concepts or None,
        desc="BIOS load",
        unit="concept",
    ) as bar:
        last = 0

        def _on_progress(loaded: int) -> None:
            nonlocal last
            bar.update(loaded - last)
            last = loaded

        result = initialize_bios(
            full=want_full,
            cfg=settings,
            graphdb=GraphDbClient.from_settings(settings),
            gate=BiosLicenseGate.from_settings(settings)
            if want_full
            else BiosLicenseGate(True, "poc"),
            force=force,
            on_progress=_on_progress,
        )

    if result.get("skipped"):
        console.print("[yellow]BIOS already initialized — skipped[/yellow]")
    metrics_table(
        "BIOS load",
        [
            ("source", str(result.get("source") or "-")),
            ("concepts", str(result.get("concepts") or 0)),
            ("graph_loaded", str(result.get("graph_loaded") or 0)),
            ("skipped", str(bool(result.get("skipped")))),
            ("index", str(result.get("index") or "-")),
            ("cache", str(result.get("cache") or "-")),
        ],
    )


@foundation_app.command("sync")
def foundation_sync() -> None:
    """YAML seed → GraphDB + Milvus + OpenMetadata（三后端必达入库）。"""
    from biomed_ontology.config import settings
    from biomed_ontology.foundation.sync import sync_world_model

    command_header(
        "foundation sync",
        meta=[
            ("require", "graphdb+milvus+om"),
        ],
    )
    result = sync_world_model(
        cfg=settings,
        require_graphdb=True,
        require_milvus=True,
        require_om=True,
    )
    for line in result.details:
        console.print(f"[dim]{line}[/dim]")
    metrics_table(
        "Foundation Sync",
        [
            ("GraphDB", f"{'✓' if result.graphdb_ok else '✗'}  entities={result.entities}"),
            (
                "Milvus",
                f"{'✓' if result.milvus_ok else '✗'}  evidence={result.evidence_upserted}",
            ),
            ("OpenMetadata", f"{'✓' if result.om_ok else '✗'}  assets={result.assets}"),
        ],
    )
    if not (result.graphdb_ok and result.milvus_ok and result.om_ok):
        raise typer.Exit(1)


@foundation_app.command("evolve-mine")
def foundation_evolve_mine(
    text: list[str] | None = typer.Argument(None, help="待挖掘查询；默认用内置未知词"),
    json_out: bool = typer.Option(False, "--json", help="输出完整 JSON（机器可读）"),
    compact: bool = typer.Option(False, "--compact", help="仅 Suite 摘要，不展开候选表"),
    include_lake: bool | None = typer.Option(
        None,
        "--include-lake/--no-include-lake",
        help="合并 Iceberg/WAL er_observations（默认开；HMD_EVOLVE_INCLUDE_LAKE）",
    ),
) -> None:
    """P2：unmapped / 低置信 → KGCL 候选落库（不自动改本体）。

    默认合并湖/WAL mention；Rich 展示候选与跳过项；`--json` 给脚本。
    """
    import json

    from biomed_ontology.config import settings
    from biomed_ontology.foundation.evolve import mine_unmapped_candidates
    from biomed_ontology.foundation.obs_log import configure_foundation_logging
    from biomed_ontology.foundation.render import render_evolve_mine

    configure_foundation_logging(json_logs=True)
    queries = list(text or []) or ["unknownzyme-xyz-999", "HMPL-504"]
    use_lake = settings.evolve_include_lake if include_lake is None else include_lake
    result = mine_unmapped_candidates(queries, include_lake=use_lake)
    if json_out:
        console.print_json(json.dumps(result.to_dict(), ensure_ascii=False))
        return
    render_evolve_mine(result, console=console, verbose=not compact)


@foundation_app.command("evolve-enrich")
def foundation_evolve_enrich(
    from_path: list[Path] | None = typer.Option(
        None,
        "--from",
        help="candidates.json 路径；可重复。默认读 foundation_candidates/*.candidates.json",
    ),
    policy: Path | None = typer.Option(
        None, "--policy", help="filter 策略 YAML（默认 ontology/policies/evolve_filter.yaml）"
    ),
    out_dir: Path | None = typer.Option(None, "--out-dir", help="proposals 输出目录"),
    skip_tools: bool = typer.Option(
        False, "--skip-tools", help="跳过 Resolver/BIOS 取证（仅 filter + 规则提案）"
    ),
    use_llm: bool = typer.Option(
        True,
        "--llm/--no-llm",
        help="受限 LLM 裁决 borderline（默认开启；无 API key 自动跳过）",
    ),
    json_out: bool = typer.Option(False, "--json", help="输出完整 JSON"),
) -> None:
    """candidates → policy filter → LLM 裁决（默认开）→ 取证 → proposals.jsonl。"""
    import json

    from biomed_ontology.cli_ui import command_header, progress_disabled, tqdm_bar
    from biomed_ontology.foundation.evolve_propose import run_enrich
    from biomed_ontology.foundation.obs_log import configure_foundation_logging
    from biomed_ontology.foundation.render import render_evolve_enrich

    configure_foundation_logging(json_logs=True)
    disable_progress = json_out or progress_disabled()
    if not json_out:
        command_header(
            "foundation evolve-enrich",
            meta=[
                ("from", ",".join(str(p) for p in from_path) if from_path else "glob candidates"),
                ("policy", str(policy or "default")),
                ("skip_tools", str(skip_tools)),
                ("llm", "on" if use_llm else "off"),
            ],
        )
    llm_bar = tqdm_bar(desc="llm-filter", unit="batch", disable=disable_progress)
    bar = tqdm_bar(desc="enrich", unit="mention", disable=disable_progress)
    with llm_bar, bar:
        result = run_enrich(
            from_paths=list(from_path) if from_path else None,
            policy_path=policy,
            out_dir=out_dir,
            skip_tools=skip_tools,
            use_llm=use_llm,
            progress=bar,
            llm_progress=llm_bar,
        )
    if json_out:
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return
    render_evolve_enrich(result, console=console)


@foundation_app.command("evolve-review")
def foundation_evolve_review(
    proposals: Path | None = typer.Option(None, "--proposals", help="proposals.jsonl"),
    pending: bool = typer.Option(True, "--pending/--all", help="仅 pending_approval"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """列出提案队列（Rich Table / JSON）。"""
    import json

    from biomed_ontology.foundation.evolve_apply import load_proposals
    from biomed_ontology.foundation.render import render_evolve_review

    path, rows = load_proposals(proposals)
    if pending:
        rows = [r for r in rows if r.get("status") == "pending_approval"]
    if json_out:
        print(json.dumps({"proposals_path": str(path), "rows": rows}, ensure_ascii=False))
        return
    render_evolve_review(rows, console=console, title=f"{path.name} ({len(rows)})")


@foundation_app.command("evolve-approve")
def foundation_evolve_approve(
    proposal_id: list[str] | None = typer.Argument(None, help="HMDPROP:…；可多个"),
    proposals: Path | None = typer.Option(None, "--proposals"),
    tier: str | None = typer.Option(None, "--tier", help="如 L1"),
    min_confidence: float | None = typer.Option(None, "--min-confidence"),
    by: str = typer.Option("curator", "--by"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """批准提案（只改 proposals.jsonl 状态，不写本体）。"""
    import json

    from biomed_ontology.cli_ui import command_header, metrics_table, progress_disabled, tqdm_bar
    from biomed_ontology.foundation.evolve_apply import approve_proposals
    from biomed_ontology.foundation.render import render_evolve_review

    if not proposal_id and not tier:
        console.print("[red]需要 proposal_id 或 --tier[/red]")
        raise typer.Exit(2)
    if not json_out:
        command_header(
            "foundation evolve-approve",
            meta=[("by", by), ("tier", tier or "—"), ("ids", str(len(proposal_id or [])))],
        )
    # batch path uses tqdm when many ids implied via tier
    bar = tqdm_bar(desc="approve", unit="prop", disable=json_out or progress_disabled())
    with bar:
        path, selected = approve_proposals(
            proposals,
            proposal_ids=list(proposal_id) if proposal_id else None,
            tier=tier,
            min_confidence=min_confidence,
            by=by,
        )
        bar.total = len(selected)
        bar.update(len(selected))
    if json_out:
        print(
            json.dumps(
                {"proposals_path": str(path), "approved": selected},
                ensure_ascii=False,
            )
        )
        return
    render_evolve_review(selected, console=console, title="Approved")
    metrics_table("approve", [("count", str(len(selected))), ("file", str(path))])


@foundation_app.command("evolve-reject")
def foundation_evolve_reject(
    proposal_id: list[str] | None = typer.Argument(None, help="HMDPROP:…"),
    proposals: Path | None = typer.Option(None, "--proposals"),
    tier: str | None = typer.Option(None, "--tier"),
    reason: str = typer.Option("rejected", "--reason"),
    by: str = typer.Option("curator", "--by"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """拒绝提案。"""
    import json

    from biomed_ontology.cli_ui import command_header, metrics_table
    from biomed_ontology.foundation.evolve_apply import reject_proposals
    from biomed_ontology.foundation.render import render_evolve_review

    if not proposal_id and not tier:
        console.print("[red]需要 proposal_id 或 --tier[/red]")
        raise typer.Exit(2)
    if not json_out:
        command_header("foundation evolve-reject", meta=[("reason", reason), ("by", by)])
    path, selected = reject_proposals(
        proposals,
        proposal_ids=list(proposal_id) if proposal_id else None,
        tier=tier,
        reason=reason,
        by=by,
    )
    if json_out:
        print(
            json.dumps(
                {"proposals_path": str(path), "rejected": selected},
                ensure_ascii=False,
            )
        )
        return
    render_evolve_review(selected, console=console, title="Rejected")
    metrics_table("reject", [("count", str(len(selected))), ("file", str(path))])


@foundation_app.command("evolve-apply")
def foundation_evolve_apply(
    proposals: Path | None = typer.Option(None, "--proposals"),
    write: bool = typer.Option(False, "--write", help="真正写入 dictionary/zingg；默认 dry-run"),
    dictionary: Path | None = typer.Option(None, "--dictionary", help="覆盖 dictionary 路径"),
    zingg: Path | None = typer.Option(None, "--zingg", help="覆盖 zingg_matches 路径"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """将 approved 提案确定性写回 Git 策展面（默认 dry-run）。"""
    import json

    from biomed_ontology.cli_ui import command_header, progress_disabled, tqdm_bar
    from biomed_ontology.foundation.evolve_apply import apply_approved
    from biomed_ontology.foundation.render import render_evolve_apply

    if not json_out:
        command_header(
            "foundation evolve-apply",
            meta=[("mode", "write" if write else "dry-run")],
        )
    bar = tqdm_bar(desc="apply", unit="prop", disable=json_out or progress_disabled())
    with bar:
        result = apply_approved(
            proposals,
            dry_run=not write,
            progress=bar,
            dictionary_path=dictionary,
            zingg_path=zingg,
        )
    if json_out:
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return
    render_evolve_apply(result, console=console)


@foundation_app.command("evolve-verify")
def foundation_evolve_verify(
    proposals: Path | None = typer.Option(None, "--proposals"),
    dictionary: Path | None = typer.Option(
        None, "--dictionary", help="用指定 dictionary 做 verify（sandbox e2e）"
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """对 approved/applied 提案再 resolve，断言挂上目标 ENT。"""
    import json

    from biomed_ontology.cli_ui import command_header, progress_disabled, tqdm_bar
    from biomed_ontology.foundation.evolve_apply import verify_proposals
    from biomed_ontology.foundation.render import render_evolve_verify

    if not json_out:
        command_header("foundation evolve-verify")
    bar = tqdm_bar(desc="verify", unit="mention", disable=json_out or progress_disabled())
    with bar:
        result = verify_proposals(proposals, progress=bar, dictionary_path=dictionary)
    if json_out:
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        raise typer.Exit(1 if result.failed else 0)
    render_evolve_verify(result, console=console)
    if result.failed:
        raise typer.Exit(1)


@foundation_app.command("zingg-run")
def foundation_zingg_run(
    mode: str = typer.Option(
        "full",
        "--mode",
        help="full | materialize-only | export-only | stub-link",
    ),
    observations: str | None = typer.Option(
        None,
        "--observations",
        help="lake | bootstrap | all（默认 HMD_ZINGG_OBSERVATIONS）",
    ),
    window_days: int | None = typer.Option(
        None, "--window-days", help="默认 HMD_ZINGG_WINDOW_DAYS"
    ),
    min_occurrences: int | None = typer.Option(
        None, "--min-occurrences", help="默认 HMD_ZINGG_MIN_OCCURRENCES"
    ),
    min_score: float | None = typer.Option(None, "--min-score", help="默认 HMD_ZINGG_MIN_SCORE"),
    raw: Path | None = typer.Option(None, "--raw", help="Zingg 原始 matches JSONL"),
    skip_docker: bool | None = typer.Option(
        None,
        "--skip-docker/--no-skip-docker",
        help="跳过 docker/zingg（默认 HMD_ZINGG_SKIP_DOCKER）",
    ),
) -> None:
    """Zingg 批作业：materialize →（Spark link）→ export → zingg_matches.jsonl。"""

    from biomed_ontology.config import settings
    from biomed_ontology.foundation.zingg_io import (
        ZINGG_DIR,
        export_matches,
        link_stub_from_materialized,
        materialize,
    )

    mode_l = mode.strip().lower().replace("_", "-")
    obs_l = (observations or settings.zingg_observations).strip().lower()
    if obs_l not in {"lake", "bootstrap", "all"}:
        console.print("[red]--observations 须为 lake|bootstrap|all[/red]")
        raise typer.Exit(2)
    win = settings.zingg_window_days if window_days is None else window_days
    min_occ = settings.zingg_min_occurrences if min_occurrences is None else min_occurrences
    no_docker = settings.zingg_skip_docker if skip_docker is None else skip_docker

    if mode_l in {"full", "materialize-only", "stub-link"}:
        result = materialize(
            observations=obs_l,  # type: ignore[arg-type]
            window_days=win,
            min_occurrences=min_occ,
        )
        console.print(
            f"materialize enterprise={result.enterprise_rows} "
            f"observation={result.observation_rows} sources={result.sources}"
        )
        for w in result.warnings:
            console.print(f"[yellow]warn[/yellow] {w}")
        if mode_l == "materialize-only":
            return

    if mode_l in {"full", "stub-link"}:
        if mode_l == "full" and not no_docker:
            compose = Path("docker/zingg/docker-compose.yml")
            if compose.exists():
                import subprocess

                console.print("running docker/zingg link (zingg/zingg --phase train-link)…")
                proc = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "-f",
                        str(compose),
                        "--profile",
                        "zingg",
                        "run",
                        "--rm",
                        "zingg-link",
                    ],
                    check=False,
                )
                if proc.returncode != 0:
                    console.print(
                        "[yellow]docker zingg-link failed; falling back to stub-link[/yellow]"
                    )
                    link_stub_from_materialized()
            else:
                console.print("[yellow]no docker/zingg compose; stub-link[/yellow]")
                link_stub_from_materialized()
        else:
            link_stub_from_materialized()

    raw_path = raw or (ZINGG_DIR / "raw_matches.jsonl")
    if mode_l in {"full", "export-only", "stub-link"}:
        try:
            summary = export_matches(source=raw_path, min_score=min_score)
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        console.print(
            f"export written={summary['written']} ambiguous={summary['ambiguous']} "
            f"path={summary['path']} min_score={summary['min_score']}"
        )
