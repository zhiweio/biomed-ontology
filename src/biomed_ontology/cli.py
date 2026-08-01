"""命令行入口。

构建期命令可联网拉快照；运行期服务命令必须完全离线。
两类命令不共用配置，避免运行期意外触发网络调用。
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.ingest import build_from_seed, load_ambiguity_registry
from biomed_ontology.licensing import POLICIES
from biomed_ontology.ontology.ids import IdLedger, SequenceLedger
from biomed_ontology.registry import Track, load_registry

app = typer.Typer(help="生物医药语义层数据基座", no_args_is_help=True)
sources_app = typer.Typer(help="数据源注册表", no_args_is_help=True)
build_app = typer.Typer(help="术语层构建", no_args_is_help=True)
app.add_typer(sources_app, name="sources")
app.add_typer(build_app, name="build")

console = Console()

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_DIR = REPO_ROOT / "data" / "seed"
DEFAULT_LEDGER_DIR = REPO_ROOT / "data" / "ledger"


@sources_app.command("list")
def sources_list(
    track: str | None = typer.Option(None, "--track", help="A=开放许可 / B=采购"),
    tier: str | None = typer.Option(None, "--tier", help="TIER_0..TIER_3"),
) -> None:
    """列出已注册数据源。"""
    registry = load_registry()
    rows = list(registry)
    if track:
        rows = [s for s in rows if s.track is Track(track.upper())]
    if tier:
        rows = [s for s in rows if s.license_tier is LicenseTierEnum(tier.upper())]

    table = Table(title=f"数据源注册表 v{registry.registry_version}")
    for col in ("ID", "轨", "Tier", "许可", "角色", "启用", "实体类型"):
        table.add_column(col)
    for s in sorted(rows, key=lambda x: (x.track.value, x.id)):
        table.add_row(
            s.id,
            s.track.value,
            s.license_tier.value,
            s.license_id,
            s.role.value,
            "是" if s.enabled else "插槽",
            ",".join(e.value for e in s.entity_types) or "-",
        )
    console.print(table)


@sources_app.command("procurement")
def sources_procurement() -> None:
    """列出待采购插槽，按优先级排序。"""
    registry = load_registry()
    table = Table(title="Track B 采购插槽")
    for col in ("优先级", "ID", "名称", "Tier", "增量"):
        table.add_column(col)
    for s in registry.procurement_slots():
        table.add_row(
            str(s.procurement_priority or "-"),
            s.id,
            s.name,
            s.license_tier.value,
            (s.notes or "").strip().split("\n")[0],
        )
    console.print(table)


@sources_app.command("policy")
def sources_policy() -> None:
    """显示许可分层策略。"""
    table = Table(title="许可分层策略 (D10)")
    for col in ("Tier", "可导出", "可训练", "需署名", "同源共享", "需凭据", "说明"):
        table.add_column(col)
    for tier, p in POLICIES.items():
        yes = lambda b: "是" if b else "否"  # noqa: E731
        table.add_row(
            tier.value,
            yes(p.exportable),
            yes(p.trainable),
            yes(p.requires_attribution),
            yes(p.share_alike),
            yes(p.requires_entitlement),
            p.description,
        )
    console.print(table)


@build_app.command("seed")
def build_seed(
    seed_dir: Path = typer.Option(DEFAULT_SEED_DIR, "--seed-dir"),
    ledger_dir: Path = typer.Option(DEFAULT_LEDGER_DIR, "--ledger-dir"),
    release: str = typer.Option("0.1.0", "--release"),
    dry_run: bool = typer.Option(False, "--dry-run", help="不写入账本"),
) -> None:
    """从种子切片构建概念与别名。"""
    registry = load_registry()
    seed_files = sorted(p for p in seed_dir.glob("*.yaml") if p.name != "ambiguity.yaml")
    if not seed_files:
        console.print(f"[red]未找到种子文件: {seed_dir}[/red]")
        raise typer.Exit(1)

    ambiguity_path = seed_dir / "ambiguity.yaml"
    ambiguity = load_ambiguity_registry(ambiguity_path) if ambiguity_path.exists() else None

    id_ledger = IdLedger(ledger_dir / "concept_ids.json", release=release)
    alias_ledger = SequenceLedger(ledger_dir / "alias_ids.json", prefix="HMDA")

    result = build_from_seed(
        seed_files,
        registry=registry,
        id_ledger=id_ledger,
        alias_ledger=alias_ledger,
        ambiguity=ambiguity,
    )

    table = Table(title=f"种子构建 release={release}")
    table.add_column("指标")
    table.add_column("值", justify="right")
    table.add_row("种子文件", str(len(seed_files)))
    table.add_row("概念", str(len(result.concepts)))
    table.add_row("别名", str(len(result.synonyms)))
    table.add_row("生成变体", str(sum(1 for s in result.synonyms if s.is_generated_variant)))
    table.add_row("歧义别名", str(sum(1 for s in result.synonyms if s.is_ambiguous)))
    table.add_row("alias_norm 碰撞", str(len(result.ambiguity_collisions)))
    table.add_row("未登记碰撞", str(len(result.unregistered_collisions)))
    console.print(table)

    if result.unregistered_collisions:
        console.print("\n[yellow]未登记的歧义（需人工处理）:[/yellow]")
        for norm, cids in sorted(result.unregistered_collisions.items()):
            console.print(f"  {norm}: {', '.join(cids)}")

    if dry_run:
        console.print("\n[dim]dry-run，账本未写入[/dim]")
        return

    id_ledger.save()
    alias_ledger.save()
    console.print(f"\n账本已写入 {ledger_dir}")


# ------------------------------------------------------------------ 运行期命令


@app.command("kb")
def kb_stats() -> None:
    """构建完整知识库并打印统计。"""
    from biomed_ontology.pipeline import build_knowledge_base

    kb = build_knowledge_base()
    table = Table(title=f"知识库 release={kb.release_id}")
    table.add_column("指标")
    table.add_column("值", justify="right")
    for k, v in kb.stats().items():
        table.add_row(k, f"{v:.4f}" if isinstance(v, float) else str(v))
    console.print(table)
    for w in kb.warnings:
        console.print(f"[yellow]warn[/yellow] {w}")


@app.command("gate")
def gate_cmd(
    accuracy: float = typer.Option(0.94, "--accuracy", help="人工抽检准确率（各实体类型同值）"),
) -> None:
    """跑发版质量守门。"""
    from biomed_ontology.pipeline import build_knowledge_base
    from biomed_ontology.quality import QualityGate

    kb = build_knowledge_base()
    manual = dict.fromkeys(["SUBSTANCE", "TARGET", "DISEASE"], accuracy)
    result = QualityGate().evaluate(kb, manual_accuracy=manual)
    console.print(result.explain())
    raise typer.Exit(0 if result.passed else 1)


@app.command("eval")
def eval_cmd(
    entitlements: str = typer.Option("", "--entitlements", help="逗号分隔的已采购源 ID"),
) -> None:
    """跑 gold set 评测：归一化准确率 + 检索三臂对照。"""
    from biomed_ontology.eval import eval_normalization, eval_retrieval
    from biomed_ontology.pipeline import build_knowledge_base

    kb = build_knowledge_base()
    ents = frozenset(e.strip() for e in entitlements.split(",") if e.strip())
    console.print(eval_normalization(kb).as_table())
    console.print()
    console.print(eval_retrieval(kb, entitlements=ents).as_table())


@app.command("demo")
def demo_cmd(
    demo_id: str | None = typer.Option(None, "--id", help="只跑某个场景，如 D3"),
) -> None:
    """跑演示场景。"""
    from biomed_ontology.agentapi import AgentApi
    from biomed_ontology.demo import DEMOS, run_all, run_demo
    from biomed_ontology.pipeline import build_knowledge_base

    kb = build_knowledge_base()
    if demo_id:
        if demo_id not in DEMOS:
            console.print(f"[red]未知场景 {demo_id}，可用：{sorted(DEMOS)}[/red]")
            raise typer.Exit(2)
        results = [run_demo(demo_id, kb, AgentApi.from_kb(kb))]
    else:
        results = run_all(kb)
    for r in results:
        console.print(r.render())
        console.print()
    passed = sum(r.passed for r in results)
    console.print(f"通过 {passed}/{len(results)}")
    raise typer.Exit(0 if passed == len(results) else 1)


@app.command("signals")
def signals_cmd(
    release: str = typer.Option("0.2.0", "--release", help="候选 release ID"),
    out_dir: Path = typer.Option(REPO_ROOT / "data" / "releases", "--out"),
    approved_by: str | None = typer.Option(None, "--approved-by", help="人工审批人"),
) -> None:
    """挖掘演进信号并生成 KGCL changeset。"""
    from biomed_ontology.agentapi import AgentApi
    from biomed_ontology.demo import run_all
    from biomed_ontology.evolution import (
        MiningInput,
        build_changeset,
        mine_signals,
        plan_release,
        write_release_artifacts,
    )
    from biomed_ontology.pipeline import build_knowledge_base
    from biomed_ontology.quality import QualityGate

    kb = build_knowledge_base()
    api = AgentApi.from_kb(kb)
    # 先跑一遍 demo 制造真实使用痕迹：没有使用就没有信号，
    # 这正是"信号必须来自真实使用"这条设计约束的直接体现。
    run_all(kb, api)
    sigs = mine_signals(MiningInput.from_runtime(kb, api))

    table = Table(title=f"演进信号 {len(sigs)} 条")
    for col in ("优先级", "类型", "载荷", "次数"):
        table.add_column(col)
    for s in sigs:
        table.add_row(s.priority, s.signal_type.value, s.payload[:48], str(s.occurrences))
    console.print(table)

    cs = build_changeset(kb, sigs, release_id=release)
    console.print(f"\n[bold]KGCL changeset[/bold]\n{cs.to_kgcl()}")
    gate = QualityGate().evaluate(
        kb, manual_accuracy=dict.fromkeys(["SUBSTANCE", "TARGET", "DISEASE"], 0.94)
    )
    plan = plan_release(kb, cs, gate_result=gate, approved_by=approved_by)
    console.print(plan.explain())
    if plan.approved:
        written = write_release_artifacts(plan, out_dir)
        console.print(f"\n发版物料：{[str(p) for p in written]}")


@app.command("contract")
def contract_cmd(
    out_dir: Path = typer.Option(REPO_ROOT / "build" / "contract", "--out"),
) -> None:
    """导出 agent 接入契约（MCP 描述符 + OpenAPI）。"""
    from biomed_ontology.agentapi.serve import write_contract_bundle

    written = write_contract_bundle(out_dir)
    for p in written:
        console.print(f"已写入 {p}")


if __name__ == "__main__":
    app()
