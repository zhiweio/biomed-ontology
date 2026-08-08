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
    embedder: str = typer.Option(
        "multimodal-bio",
        "--embedder",
        help="默认 multimodal-bio（五列最全）；接线验证用 fake + --allow-fake",
    ),
    collection: str | None = typer.Option(None, "--collection"),
    reranker: str = typer.Option(
        "bge-reranker-v2-m3",
        "--reranker",
        help="交叉编码器精排；传空字符串可关掉精排臂",
    ),
    allow_fake: bool = typer.Option(
        False, "--allow-fake", help="允许 fake embedder（仅用于验证接线，产出不可入报告）"
    ),
) -> None:
    """跑 gold set 评测：归一化准确率 + 检索消融（默认 multimodal-bio + Milvus）。"""
    from biomed_ontology.eval import eval_normalization, eval_retrieval
    from biomed_ontology.eval.targets import check_targets, render_outcomes
    from biomed_ontology.pipeline import build_knowledge_base
    from biomed_ontology.rerank import get_reranker

    _require_real_embedder(embedder, allow_fake=allow_fake)

    kb = build_knowledge_base()
    ents = frozenset(e.strip() for e in entitlements.split(",") if e.strip())
    console.print(eval_normalization(kb).as_table())
    console.print()

    backend = _milvus_backend(embedder, collection)
    ev = eval_retrieval(
        kb,
        entitlements=ents,
        milvus_backend=backend,
        # 报的是模型真名（bge-m3+sapbert+qwen3-vl），不是命令行别名。
        # 别名说不清生医列到底装了什么，而这行字要给净值背书。
        embedder=backend.embedder.name if backend else "",
        reranker=get_reranker(reranker) if reranker.strip() else None,
    )
    console.print(ev.as_table())
    console.print()
    # 目标与实测同屏输出：把"没达成"和数字摆在一起，避免只有好消息被转述出去
    console.print(render_outcomes(check_targets(ev)))


def _require_real_embedder(name: str, *, allow_fake: bool) -> None:
    """挡住 fake。

    fake 是哈希向量，语义相似度连符号都可能和真模型相反（实测 −0.042 vs +0.104）。
    一个由它产出的数字看起来和真数字一模一样，却会把结论带反方向 ——
    所以这里宁可让命令失败，也不让它默默跑完。
    """
    from biomed_ontology.embed import REAL_EMBEDDERS

    if name in REAL_EMBEDDERS:
        return
    if name == "fake" and allow_fake:
        console.print("[yellow]警告：fake embedder 只验证接线，本次产出不可用于任何报告[/yellow]")
        return
    console.print(
        f"[red]--embedder {name} 不是垂类模型。报告口径必须用 {' / '.join(REAL_EMBEDDERS)} 之一；"
        "只想验证管线接线请显式加 --allow-fake。[/red]"
    )
    raise typer.Exit(1)


def _apply_figure_types(chunks: list, typer_impl, asset_root) -> dict[str, int]:
    """就地给带图切片打上 `figure_type`，返回类型分布。

    在索引期算而不是解析期：判型要读像素，而解析产物是要进版本库的 YAML ——
    把一个模型的输出写进去，等于让语料的内容随模型版本变化。
    索引是可重建的，那里才是模型推断该落的地方。
    """
    from collections import Counter

    from biomed_ontology.parse.assets import resolve_asset

    targets = [c for c in chunks if getattr(c, "asset_path", None)]
    if not targets:
        return {}
    paths = [resolve_asset(asset_root, c.doc_id, c.asset_path) for c in targets]
    typings = typer_impl.classify(paths, [c.text for c in targets])
    for chunk, typing in zip(targets, typings, strict=True):
        chunk.figure_type = typing.figure_type
    return dict(Counter(t.figure_type for t in typings if t.figure_type))


def _milvus_backend(embedder: str, collection: str | None):
    """连不上就直接退出，不静默降级 —— 报告里的"Milvus 臂"必须真的是 Milvus 跑的。"""
    from biomed_ontology.config import settings
    from biomed_ontology.embed import get_embedder
    from biomed_ontology.pipeline import DATA_ROOT
    from biomed_ontology.registry import load_registry
    from biomed_ontology.search.backends.milvus import MilvusBackend

    model = get_embedder(embedder)
    backend = MilvusBackend(
        uri=settings.milvus_uri,
        token=settings.milvus_token.get_secret_value(),
        collection=collection or settings.milvus_collection,
        embedder=model,
        known_sources=frozenset(s.id for s in load_registry().active()),
        asset_root=DATA_ROOT / "assets",
    )
    if not backend.client.has_collection(backend.collection):
        console.print(f"[red]集合 {backend.collection} 不存在，先跑 hmd index[/red]")
        raise typer.Exit(1)

    stamped = backend.stamped_embedder()
    if stamped and stamped != model.name:
        console.print(
            f"[red]集合 {backend.collection} 是用 {stamped} 建的，现在却拿 {model.name} 检索。"
            "两套向量不在同一空间，算出来的分数没有意义 —— 先 hmd index --recreate。[/red]"
        )
        raise typer.Exit(1)
    return backend


@app.command("demo")
def demo_cmd(
    demo_id: str | None = typer.Option(None, "--id", help="只跑某个场景，如 D3"),
    json_out: bool = typer.Option(False, "--json", help="输出完整 JSON（机器可读）"),
    compact: bool = typer.Option(False, "--compact", help="仅 Trace 摘要，不展开详情"),
) -> None:
    """基座能力验收场景（自带可证伪断言）。

    默认用 Rich 分步展示（对齐 `hmd foundation golden`）；
    `--json` 给脚本，`--compact` 只要 Trace 摘要。
    """
    from biomed_ontology.demo import (
        DEMOS,
        render_demo_results,
        run_all,
        run_demo,
        summary_json,
    )
    from biomed_ontology.pipeline import build_knowledge_base
    from biomed_ontology.tools import ToolApi

    kb = build_knowledge_base()
    if demo_id:
        if demo_id not in DEMOS:
            console.print(f"[red]未知场景 {demo_id}，可用：{sorted(DEMOS)}[/red]")
            raise typer.Exit(2)
        results = [run_demo(demo_id, kb, ToolApi.from_kb(kb))]
    else:
        results = run_all(kb)

    passed = sum(r.passed for r in results)
    ok = passed == len(results)

    if json_out:
        console.print_json(summary_json(results))
        raise typer.Exit(0 if ok else 1)

    render_demo_results(results, console=console, verbose=not compact)
    raise typer.Exit(0 if ok else 1)


@app.command("signals")
def signals_cmd(
    release: str = typer.Option("0.2.0", "--release", help="候选 release ID"),
    out_dir: Path = typer.Option(REPO_ROOT / "data" / "releases", "--out"),
    approved_by: str | None = typer.Option(None, "--approved-by", help="人工审批人"),
) -> None:
    """挖掘演进信号并生成 KGCL changeset。"""
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
    from biomed_ontology.tools import ToolApi

    kb = build_knowledge_base()
    api = ToolApi.from_kb(kb)
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
    """导出 tools 接入契约（MCP 描述符 + OpenAPI）。"""
    from biomed_ontology.tools import write_contract_bundle

    written = write_contract_bundle(out_dir)
    for p in written:
        console.print(f"已写入 {p}")


@app.command("parse")
def parse_cmd(
    pdf: Path = typer.Argument(..., help="待解析文档"),
    doc_id: str = typer.Option(..., "--doc-id", help="如 DOC:PMC.1234567"),
    source_id: str = typer.Option("PMC", "--source-id"),
    title: str | None = typer.Option(None, "--title"),
    layout: str | None = typer.Option(None, "--layout", help="pymupdf | mineru"),
    out_dir: Path = typer.Option(REPO_ROOT / "data" / "corpus" / "parsed", "--out"),
) -> None:
    """解析文档为语义树，产出与手写语料同 schema 的 YAML。"""
    import yaml

    from biomed_ontology.observability import TraceContext, new_trace_id
    from biomed_ontology.parse import parse_document

    ctx = TraceContext(trace_id=new_trace_id(), ontology_release_id="0.1.0")
    parsed = parse_document(
        pdf,
        doc_id=doc_id,
        source_id=source_id,
        title=title,
        layout=layout,
        ctx=ctx,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{doc_id.replace(':', '_').replace('/', '_')}.yaml"
    target.write_text(
        yaml.safe_dump(parsed.to_yaml_obj(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    table = Table(title=f"解析 {doc_id}")
    table.add_column("指标")
    table.add_column("值", justify="right")
    table.add_row("后端", parsed.backend)
    table.add_row("章节", str(len(parsed.sections)))
    table.add_row("正文段", str(len(parsed.document.sections)))
    table.add_row("表格", str(len(parsed.document.tables)))
    table.add_row("SAME-AS", str(len(parsed.same_as)))
    console.print(table)

    if parsed.degraded:
        # 高亮而非静默：能力缺失必须让运行的人当场看见
        console.print(f"[yellow]降级[/yellow] 本次解析缺失能力：{', '.join(parsed.degraded)}")
    console.print(f"已写入 {target}")


@app.command("index")
def index_cmd(
    embedder: str = typer.Option(
        "multimodal-bio",
        "--embedder",
        help="默认 multimodal-bio（五列最全）；接线验证用 fake + --allow-fake",
    ),
    collection: str | None = typer.Option(None, "--collection"),
    recreate: bool = typer.Option(False, "--recreate", help="先删表再建，用于换 embedder"),
    figure_typer: str = typer.Option(
        "biomedclip",
        "--figure-typer",
        help="默认 biomedclip；无权重时可用 caption 关键词兜底",
    ),
    allow_fake: bool = typer.Option(
        False, "--allow-fake", help="允许 fake embedder（仅用于验证接线，产出不可入报告）"
    ),
) -> None:
    """把知识库切片写入 Milvus（默认 multimodal-bio 五列 + BiomedCLIP 图型）。"""
    from biomed_ontology.config import settings
    from biomed_ontology.embed import get_embedder
    from biomed_ontology.parse.figure_type import get_figure_typer
    from biomed_ontology.pipeline import DATA_ROOT, build_knowledge_base
    from biomed_ontology.registry import load_registry
    from biomed_ontology.search import HybridSearcher
    from biomed_ontology.search.backends.milvus import MilvusBackend, chunk_to_row

    _require_real_embedder(embedder, allow_fake=allow_fake)

    kb = build_knowledge_base()
    searcher = HybridSearcher(kb)
    registry = load_registry()

    model = get_embedder(embedder)
    asset_root = DATA_ROOT / "assets"
    backend = MilvusBackend(
        uri=settings.milvus_uri,
        token=settings.milvus_token.get_secret_value(),
        collection=collection or settings.milvus_collection,
        embedder=model,
        known_sources=frozenset(s.id for s in registry.active()),
        asset_root=asset_root,
    )
    backend.ensure_collection(drop_existing=recreate)

    typed = _apply_figure_types(kb.chunks, get_figure_typer(figure_typer), asset_root)
    rows = [chunk_to_row(ch, searcher.chunk_meta(ch.chunk_id)) for ch in kb.chunks]
    written = backend.upsert(rows)

    table = Table(title=f"索引 {backend.collection}")
    table.add_column("指标")
    table.add_column("值", justify="right")
    table.add_row("embedder", model.name)
    table.add_row("切片", str(written))
    table.add_row("向量列", str(len(backend.vector_fields())))
    table.add_row("带图切片", str(sum(1 for r in rows if r["asset_path"])))
    table.add_row("图型已标注", f"{sum(typed.values())}（{figure_typer}）")
    console.print(table)
    if typed:
        console.print(f"图型分布 {dict(sorted(typed.items(), key=lambda kv: -kv[1]))}")

    # 分档计数：受限内容有没有真的进库，是许可过滤能否被验证的前提
    by_tier: dict[int, int] = {}
    for row in rows:
        by_tier[row["license_rank"]] = by_tier.get(row["license_rank"], 0) + 1
    console.print(f"许可分档 {dict(sorted(by_tier.items()))}")


foundation_app = typer.Typer(
    help="Enterprise Biomedical World Model（Foundation）",
    no_args_is_help=True,
)
app.add_typer(foundation_app, name="foundation")


@foundation_app.command("golden")
def foundation_golden(
    candidate: str = typer.Option("HMPL-504", "--candidate", help="候选药别名或企业 ID"),
    json_out: bool = typer.Option(False, "--json", help="输出完整 JSON（机器可读）"),
    compact: bool = typer.Option(False, "--compact", help="仅 Trace 步骤条，不展开详情"),
) -> None:
    """金路径验收：按实体类型走 Drug / Target / Indication 路径。

    默认用 Rich 分步展示推理过程（resolve / graph / citationware / assets）；
    `--json` 给脚本，`--compact` 只要计数摘要。
    """
    import json

    from biomed_ontology.foundation import FoundationApi, load_world_model
    from biomed_ontology.foundation.obs_log import configure_foundation_logging
    from biomed_ontology.foundation.render import render_golden_path

    configure_foundation_logging(json_logs=True)
    api = FoundationApi(load_world_model())
    result = api.golden_path(candidate)
    if json_out:
        console.print_json(json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            raise typer.Exit(1)
        return
    if not result.get("ok"):
        render_golden_path(result, console=console)
        raise typer.Exit(1)
    render_golden_path(result, console=console, verbose=not compact)


@foundation_app.command("golden-eval")
def foundation_golden_eval(
    candidate: list[str] | None = typer.Argument(
        None,
        help="候选列表；默认 HMPL-504/savolitinib/AZD6094/MET/c-MET/NSCLC",
    ),
) -> None:
    """多 Golden Path JSON 评估：GraphDB(+BIOS) / Milvus / OM，禁止 YAML。"""
    import json

    from biomed_ontology.foundation.golden_eval import DEFAULT_CANDIDATES, eval_golden_paths
    from biomed_ontology.foundation.obs_log import configure_foundation_logging

    configure_foundation_logging(json_logs=True)
    summary = eval_golden_paths(list(candidate) if candidate else list(DEFAULT_CANDIDATES))
    console.print_json(json.dumps(summary, ensure_ascii=False))
    if summary["passed"] != summary["total"]:
        raise typer.Exit(1)


@foundation_app.command("resolve")
def foundation_resolve(
    text: str = typer.Argument(..., help="待解析文本"),
) -> None:
    """resolve_entity：BERN2 词典/候选 → Enterprise Entity ID。"""
    from biomed_ontology.foundation import FoundationApi, load_world_model

    api = FoundationApi(load_world_model())
    out = api.resolve_entity(text)
    for row in out["resolved"]:
        console.print(
            f"{row['mention']!r} → {row.get('canonical_entity') or 'UNMAPPED'} "
            f"[{row['resolution_method']}] conf={row['confidence']}"
        )


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
    # 已初始化且非 force：可跳过 ACK；真正重灌时仍要求
    if want_full and not settings.bios_license_ack and (force or read_bios_init_marker() is None):
        console.print(
            "[red]需要 HMD_BIOS_LICENSE_ACK=poc|evaluation|licensed[/red]\n"
            "见 data/foundation/NOTICE_BIOS.md\n"
            "仅子集：HMD_BIOS_INIT=subset"
        )
        raise typer.Exit(2)
    result = initialize_bios(
        full=want_full,
        cfg=settings,
        graphdb=GraphDbClient.from_settings(settings),
        gate=BiosLicenseGate.from_settings(settings) if want_full else BiosLicenseGate(True, "poc"),
        force=force,
    )
    if result.get("skipped"):
        console.print("[yellow]BIOS already initialized — skipped[/yellow]")
    console.print(result)


@foundation_app.command("sync")
def foundation_sync() -> None:
    """YAML seed → GraphDB + Milvus + OpenMetadata（三后端必达入库）。"""
    from biomed_ontology.config import settings
    from biomed_ontology.foundation.sync import sync_world_model

    result = sync_world_model(
        cfg=settings,
        require_graphdb=True,
        require_milvus=True,
        require_om=True,
    )
    for line in result.details:
        console.print(line)
    table = Table(title="Foundation Sync")
    table.add_column("backend")
    table.add_column("ok")
    table.add_column("count", justify="right")
    table.add_row("GraphDB", "✓" if result.graphdb_ok else "✗", str(result.entities))
    table.add_row("Milvus", "✓" if result.milvus_ok else "✗", str(result.evidence_upserted))
    table.add_row("OpenMetadata", "✓" if result.om_ok else "✗", str(result.assets))
    console.print(table)
    if not (result.graphdb_ok and result.milvus_ok and result.om_ok):
        raise typer.Exit(1)


@foundation_app.command("evolve-mine")
def foundation_evolve_mine(
    text: list[str] | None = typer.Argument(None, help="待挖掘查询；默认用内置未知词"),
) -> None:
    """P2：unmapped → KGCL 候选落库（不自动改本体）。"""
    from biomed_ontology.foundation.evolve import mine_unmapped_candidates

    queries = list(text or []) or ["unknownzyme-xyz-999", "HMPL-504"]
    result = mine_unmapped_candidates(queries)
    console.print(f"signals={result.signals}")
    console.print(f"kgcl={result.kgcl_path}")
    console.print(f"json={result.json_path}")


@foundation_app.command("zingg-run")
def foundation_zingg_run() -> None:
    """Zingg 批处理桩：当前校验 matches 文件存在；完整 Spark 作业后续接入。"""
    from pathlib import Path

    matches = Path("data/foundation/zingg_matches.jsonl")
    if not matches.exists():
        console.print(f"[red]缺少 {matches}[/red]")
        raise typer.Exit(1)
    n = sum(1 for line in matches.read_text(encoding="utf-8").splitlines() if line.strip())
    console.print(f"zingg matches file OK lines={n} path={matches}")
    console.print("完整 Zingg Spark 作业：见计划 docker/zingg（预计算表已接入 Resolver）")


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    bern2_url: str | None = typer.Option(None, "--bern2-url", help="BERN2 base URL"),
    mcp: bool = typer.Option(True, "--mcp/--no-mcp", help="在 /mcp 挂载 MCP（HTTP 协议）"),
) -> None:
    """唯一 Semantic Access Layer（KB tools + Foundation ops）。"""
    import uvicorn

    from biomed_ontology.config import settings
    from biomed_ontology.service import create_app, create_mcp

    # MCP 子应用要先建好再交给 create_app —— 它的 lifespan 必须由父应用串起来跑，
    # mount() 自己不会跑子应用的 lifespan，漏掉就只有 404。
    mcp_app = create_mcp().http_app(path="/") if mcp else None
    application = create_app(mcp_app=mcp_app, bern2_url=bern2_url)

    table = Table(title="Semantic Access Layer")
    table.add_column("路径")
    table.add_column("说明")
    table.add_row(f"http://{host}:{port}/v1/*", "KB tools + Foundation ops")
    table.add_row(f"http://{host}:{port}/v1/golden_path", "金路径")
    table.add_row(f"http://{host}:{port}/docs", "交互式文档")
    table.add_row(f"http://{host}:{port}/health", "健康检查")
    if mcp:
        table.add_row(f"http://{host}:{port}/mcp", "MCP streamable HTTP")
    console.print(table)

    for warning in settings.warnings():
        console.print(f"[yellow]WARN[/yellow] {warning}")

    uvicorn.run(application, host=host, port=port)


if __name__ == "__main__":
    app()
