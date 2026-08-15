"""命令行入口。

构建期命令可联网拉快照；运行期服务命令必须完全离线。
两类命令不共用配置，避免运行期意外触发网络调用。
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.cli_foundation import foundation_app
from biomed_ontology.cli_lake import lake_app
from biomed_ontology.cli_pipeline import pipeline_app
from biomed_ontology.cli_ui import (
    command_header,
    console,
    metrics_table,
    tqdm_bar,
)
from biomed_ontology.ingest import build_from_seed, load_ambiguity_registry
from biomed_ontology.licensing import POLICIES
from biomed_ontology.ontology.ids import IdLedger, SequenceLedger
from biomed_ontology.registry import Track, load_registry

app = typer.Typer(
    help="AI-Ready Scientific Data Foundation for Drug Discovery",
    no_args_is_help=True,
)
sources_app = typer.Typer(help="数据源注册表", no_args_is_help=True)
build_app = typer.Typer(help="术语层构建", no_args_is_help=True)
app.add_typer(sources_app, name="sources")
app.add_typer(build_app, name="build")
app.add_typer(foundation_app, name="foundation")
app.add_typer(lake_app, name="lake")
app.add_typer(pipeline_app, name="pipeline")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_DIR = REPO_ROOT / "ontology" / "catalog"
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
    catalog_dir: Path = typer.Option(DEFAULT_CATALOG_DIR, "--catalog-dir"),
    seed_dir: Path | None = typer.Option(
        None, "--seed-dir", help="已弃用，请用 --catalog-dir（指向 ontology/catalog）"
    ),
    ledger_dir: Path = typer.Option(DEFAULT_LEDGER_DIR, "--ledger-dir"),
    release: str = typer.Option("0.1.0", "--release"),
    dry_run: bool = typer.Option(False, "--dry-run", help="不写入账本"),
) -> None:
    """从 ontology/catalog 构建概念与别名。"""
    if seed_dir is not None:
        console.print("[yellow]--seed-dir 已弃用，请改用 --catalog-dir[/yellow]")
        catalog_dir = seed_dir
    command_header(
        "build seed",
        meta=[
            ("catalog", str(catalog_dir)),
            ("release", release),
            ("dry_run", str(dry_run)),
        ],
    )
    registry = load_registry()
    seed_files = sorted(p for p in catalog_dir.glob("*.yaml") if p.name != "ambiguity.yaml")
    if not seed_files:
        console.print(f"[red]未找到 catalog YAML: {catalog_dir}[/red]")
        raise typer.Exit(1)

    ambiguity_path = catalog_dir / "ambiguity.yaml"
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

    metrics_table(
        f"种子构建 release={release}",
        [
            ("种子文件", str(len(seed_files))),
            ("概念", str(len(result.concepts))),
            ("别名", str(len(result.synonyms))),
            ("生成变体", str(sum(1 for s in result.synonyms if s.is_generated_variant))),
            ("歧义别名", str(sum(1 for s in result.synonyms if s.is_ambiguous))),
            ("alias_norm 碰撞", str(len(result.ambiguity_collisions))),
            ("未登记碰撞", str(len(result.unregistered_collisions))),
        ],
    )

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
    """构建文献知识库（ENT）并打印统计。"""
    from biomed_ontology.pipeline import build_literature_base

    command_header("kb", meta=[("graph", "off")])
    kb = build_literature_base(with_graph=False)
    metrics_table(
        f"知识库 release={kb.release_id}",
        [(k, f"{v:.4f}" if isinstance(v, float) else str(v)) for k, v in kb.stats().items()],
    )
    for w in kb.warnings:
        console.print(f"[yellow]warn[/yellow] {w}")


@app.command("gate")
def gate_cmd(
    accuracy: float = typer.Option(0.94, "--accuracy", help="人工抽检准确率（各实体类型同值）"),
) -> None:
    """跑发版质量守门。"""
    from biomed_ontology.pipeline import build_literature_base
    from biomed_ontology.quality import QualityGate

    kb = build_literature_base(with_graph=True)
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
    suite: str = typer.Option(
        "identity,literature,bridge",
        "--suite",
        help="逗号分隔：identity / literature / bridge / extraction / public_bios",
    ),
    no_retrieval: bool = typer.Option(
        False, "--no-retrieval", help="跳过 Literature ARMS（仍跑 Identity + Bridge）"
    ),
    json_out: bool = typer.Option(False, "--json", help="输出完整 JSON（机器可读）"),
    compact: bool = typer.Option(False, "--compact", help="仅 Trace 摘要，不展开详情"),
) -> None:
    """双面 Scorecard：Identity + Literature(ARMS) + Bridge（+ 可选 extraction / public_bios）。

    World Model 三后端金路径请用 ``hmd foundation golden-eval``（本命令不重复跑）。
    默认 Rich；``--json`` / ``--compact`` 对齐 foundation 命令。
    """
    import json

    from biomed_ontology.eval import ALL_SUITES, run_dual_eval
    from biomed_ontology.eval.render import render_dual_eval
    from biomed_ontology.rerank import get_reranker
    from biomed_ontology.runtime import open_dual_surface

    _EXTRA = {"extraction", "public_bios"}
    _require_real_embedder(embedder, allow_fake=allow_fake)

    suites = [s.strip() for s in suite.split(",") if s.strip()]
    want_extraction = "extraction" in suites
    want_public_bios = "public_bios" in suites
    suites = [s for s in suites if s not in _EXTRA]
    if no_retrieval:
        suites = [s for s in suites if s != "literature"]
    unknown = sorted(set(suites) - set(ALL_SUITES))
    if unknown:
        console.print(f"[red]未知 suite {unknown}；可选：{list(ALL_SUITES) + sorted(_EXTRA)}[/red]")
        raise typer.Exit(2)

    surface = open_dual_surface()
    ents = frozenset(e.strip() for e in entitlements.split(",") if e.strip())
    backend = _milvus_backend(embedder, collection) if suites else None
    report = None
    if suites:
        report = run_dual_eval(
            surface,
            entitlements=ents,
            milvus_backend=backend,
            embedder=backend.embedder.name if backend else "",
            reranker=get_reranker(reranker) if reranker.strip() else None,
            suites=suites,
        )

    if want_extraction:
        from biomed_ontology.eval.extraction import eval_extraction
        from biomed_ontology.pipeline import build_literature_base

        kb = surface.kb or build_literature_base(with_corpus=False, with_graph=False)
        ext = eval_extraction(kb.normalizer)
        console.print(
            f"[bold]extraction[/bold] F1={ext.f1} P={ext.precision} R={ext.recall} "
            f"grounding={ext.grounding_rate} negation_ok={ext.negation_ok}"
        )
        if ext.failures and not compact:
            for line in ext.failures[:12]:
                console.print(f"  · {line}", style="dim")
        if not ext.ok:
            raise typer.Exit(1)

    if want_public_bios:
        from biomed_ontology.eval.public_bios import eval_public_bios

        pub = eval_public_bios(surface)
        console.print(
            f"[bold]public_bios[/bold] accuracy={pub.accuracy:.1%} "
            f"({pub.passed}/{pub.total}) ok={pub.ok}"
        )
        if pub.failures and not compact:
            for row in pub.failures[:12]:
                console.print(f"  · {row}", style="dim")
        if json_out and report is None:
            console.print_json(
                json.dumps(
                    {
                        "ok": pub.ok,
                        "accuracy": pub.accuracy,
                        "passed": pub.passed,
                        "total": pub.total,
                        "failures": pub.failures,
                    },
                    ensure_ascii=False,
                )
            )
        if not pub.ok:
            raise typer.Exit(1)

    if report is None:
        return

    if json_out:
        console.print_json(json.dumps(report.to_dict(), ensure_ascii=False))
        if not report.ok:
            raise typer.Exit(1)
        return

    render_dual_eval(report, console=console, verbose=not compact)
    if not report.ok:
        raise typer.Exit(1)


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


def _milvus_backend(embedder: str, collection: str | None, *, release_id: str = ""):
    """连不上就直接退出，不静默降级 —— 报告里的"Milvus 臂"必须真的是 Milvus 跑的。"""
    from biomed_ontology.config import settings
    from biomed_ontology.embed import get_embedder
    from biomed_ontology.pipeline import DATA_ROOT, DEFAULT_RELEASE
    from biomed_ontology.registry import load_registry
    from biomed_ontology.search.backends.milvus import MilvusBackend

    model = get_embedder(embedder)
    want_release = release_id or DEFAULT_RELEASE
    backend = MilvusBackend(
        uri=settings.milvus_uri,
        token=settings.milvus_token.get_secret_value(),
        collection=collection or settings.milvus_collection,
        embedder=model,
        known_sources=frozenset(s.id for s in load_registry().active()),
        asset_root=DATA_ROOT / "assets",
        release_id=want_release,
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
    try:
        backend.require_release(want_release)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    return backend


@app.command("demo")
def demo_cmd(
    demo_id: str | None = typer.Option(None, "--id", help="只跑某个场景，如 D3"),
    json_out: bool = typer.Option(False, "--json", help="输出完整 JSON（机器可读）"),
    compact: bool = typer.Option(False, "--compact", help="仅 Trace 摘要，不展开详情"),
) -> None:
    """双面能力验收：文献 ToolApi（K*）+ World Model（W*）+ Bridge（B*）。

    默认用 Rich 分步展示；`--json` 给脚本，`--compact` 只要 Trace 摘要。
    """
    from biomed_ontology.demo import (
        DEMOS,
        render_demo_results,
        run_all,
        run_demo,
        summary_json,
    )
    from biomed_ontology.runtime import open_dual_surface

    surface = open_dual_surface()
    if surface.kb is None:
        console.print("[red]open_dual_surface 未返回 KnowledgeBase[/red]")
        raise typer.Exit(2)
    if demo_id:
        if demo_id not in DEMOS:
            console.print(f"[red]未知场景 {demo_id}，可用：{sorted(DEMOS)}[/red]")
            raise typer.Exit(2)
        results = [
            run_demo(
                demo_id,
                surface.kb,
                surface.tools,
                foundation=surface.foundation,
            )
        ]
    else:
        results = run_all(surface.kb, surface.tools, foundation=surface.foundation)

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
    from biomed_ontology.pipeline import build_literature_base
    from biomed_ontology.quality import QualityGate
    from biomed_ontology.runtime import open_dual_surface

    kb = build_literature_base(with_graph=True)
    surface = open_dual_surface(literature_kb=kb)
    api = surface.tools
    # 先跑一遍 demo 制造真实使用痕迹：没有使用就没有信号，
    # 这正是"信号必须来自真实使用"这条设计约束的直接体现。
    run_all(kb, api, foundation=surface.foundation)
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
    layout: str | None = typer.Option(
        None, "--layout", help="auto | pymupdf4llm | docling | mineru"
    ),
    out_dir: Path = typer.Option(REPO_ROOT / "data" / "corpus" / "parsed", "--out"),
) -> None:
    """解析文档为语义树，产出与手写语料同 schema 的 YAML。"""
    import yaml

    from biomed_ontology.observability import TraceContext, new_trace_id
    from biomed_ontology.parse import parse_document

    command_header(
        "parse",
        meta=[
            ("pdf", str(pdf)),
            ("doc_id", doc_id),
            ("source_id", source_id),
            ("layout", layout or "auto"),
        ],
    )
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

    metrics_table(
        f"解析 {doc_id}",
        [
            ("后端", parsed.backend),
            ("章节", str(len(parsed.sections))),
            ("正文段", str(len(parsed.document.sections))),
            ("表格", str(len(parsed.document.tables))),
            ("SAME-AS", str(len(parsed.same_as))),
            ("输出", str(target)),
        ],
    )

    if parsed.degraded:
        # 高亮而非静默：能力缺失必须让运行的人当场看见
        console.print(f"[yellow]降级[/yellow] 本次解析缺失能力：{', '.join(parsed.degraded)}")


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
    incremental: bool = typer.Option(
        False,
        "--incremental",
        help="catalog 增量：Iceberg 装载 → retag → 仅脏 chunk 写回（validate 后推荐）",
    ),
    doc_id: str | None = typer.Option(
        None,
        "--doc-id",
        help="仅重建并写入指定文档（新文档入索引；与 --incremental 互斥）",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="与 --incremental 联用：忽略 catalog fingerprint no-op",
    ),
) -> None:
    """把知识库切片写入 Milvus + Iceberg（Tree Chunk SSOT，同 release_id）。"""
    import sys

    from biomed_ontology.config import settings
    from biomed_ontology.embed import get_embedder
    from biomed_ontology.lake.chunk_store import chunks_to_evidence_rows
    from biomed_ontology.lake.tables import append_evidence_chunks
    from biomed_ontology.ontology.neighborhood import NullNeighborhood
    from biomed_ontology.parse.figure_type import get_figure_typer
    from biomed_ontology.pipeline import DATA_ROOT, build_literature_base
    from biomed_ontology.registry import load_registry
    from biomed_ontology.search import HybridSearcher
    from biomed_ontology.search.backends.milvus import MilvusBackend, chunk_to_row

    if incremental and doc_id:
        console.print("[red]--incremental 与 --doc-id 互斥[/red]")
        raise typer.Exit(2)
    if recreate and (incremental or doc_id):
        console.print("[red]--recreate 仅用于全量路径；勿与 --incremental / --doc-id 联用[/red]")
        raise typer.Exit(2)

    mode = "doc" if doc_id else ("incremental" if incremental else "full")
    coll = collection or settings.milvus_collection
    command_header(
        "index",
        meta=[
            ("mode", mode),
            ("embedder", embedder),
            ("collection", coll),
            ("figure_typer", figure_typer),
            ("recreate", str(recreate)),
        ],
    )
    _require_real_embedder(embedder, allow_fake=allow_fake)

    if incremental or doc_id:
        from biomed_ontology.index_refresh import refresh_catalog_incremental, refresh_document

        try:
            if doc_id:
                result = refresh_document(doc_id, embedder_name=embedder, collection=collection)
            else:
                result = refresh_catalog_incremental(
                    embedder_name=embedder,
                    collection=collection,
                    force=force,
                )
        except Exception as exc:
            print(f"增量 index 失败：{exc}", file=sys.stderr)
            raise typer.Exit(1) from exc

        rows_m = [
            ("skipped", str(result.skipped)),
        ]
        if result.reason:
            rows_m.append(("reason", result.reason))
        rows_m.extend(
            [
                ("catalog_sha256", (result.catalog_sha256 or "")[:16] + "…"),
                ("chunks", str(result.chunk_total)),
                ("dirty", str(result.dirty_count)),
                ("reembed", str(result.reembed_count)),
                ("patch", str(result.patch_count)),
                ("Milvus", str(result.milvus_n)),
                ("Iceberg", str(result.iceberg_n)),
            ]
        )
        metrics_table(f"索引增量 · {result.mode}", rows_m)
        if result.dirty_document_ids:
            console.print(f"dirty docs {result.dirty_document_ids[:20]}")
        return

    kb = build_literature_base(with_graph=False)
    registry = load_registry()
    asset_root = DATA_ROOT / "assets"

    # Iceberg 先于 embedder：避免 FlagEmbedding/多模态打开成百上千 FD 后再扫湖。
    lake_n = 0
    try:
        from biomed_ontology.lake.catalog import ensure_lake_tables

        ensure_lake_tables()
        lake_rows = chunks_to_evidence_rows(
            kb.chunks, documents=kb.documents, release_id=kb.release_id
        )
        lake_n = append_evidence_chunks(lake_rows)
    except Exception as exc:
        # EMFILE 时 Rich/emoji 再 import 会二次炸；用纯文本。
        print(
            f"Iceberg dual-write 失败（Citationware 需要 evidence_chunks）：{exc}",
            file=sys.stderr,
        )
        raise typer.Exit(1) from exc

    model = get_embedder(embedder)
    backend = MilvusBackend(
        uri=settings.milvus_uri,
        token=settings.milvus_token.get_secret_value(),
        collection=collection or settings.milvus_collection,
        embedder=model,
        known_sources=frozenset(s.id for s in registry.active()),
        asset_root=asset_root,
        release_id=kb.release_id,
    )
    # 写索引只需 concept labels / meta；不灌 GraphDB、不开 GRAPH 通道
    searcher = HybridSearcher(kb, backend=backend, neighborhood=NullNeighborhood())
    backend.ensure_collection(drop_existing=recreate)

    typed = _apply_figure_types(kb.chunks, get_figure_typer(figure_typer), asset_root)
    rows = []
    for ch in kb.chunks:
        meta = searcher.chunk_meta(ch.chunk_id)
        if meta is None:
            raise RuntimeError(f"缺少 chunk meta：{ch.chunk_id}")
        rows.append(
            chunk_to_row(
                ch,
                meta,
                label_terms=searcher.index_text_terms(ch),
            )
        )
    for row in rows:
        row["release_id"] = kb.release_id
    batch_size = 128
    with tqdm_bar(total=len(rows), desc="Milvus upsert", unit="chunk") as bar:
        last = 0

        def _on_batch(written: int, _total: int) -> None:
            nonlocal last
            bar.update(written - last)
            last = written

        written = backend.upsert(rows, batch_size=batch_size, on_batch=_on_batch)

    # 全量成功后刷新 fingerprint，便于后续 --incremental no-op
    try:
        from biomed_ontology.index_state import (
            LiteratureIndexState,
            compute_catalog_fingerprint,
            save_state,
        )

        save_state(
            LiteratureIndexState(
                catalog_sha256=compute_catalog_fingerprint(),
                release_id=kb.release_id,
                embedder=model.name,
                collection=backend.collection,
                chunk_count=len(kb.chunks),
                dirty_last_run=len(kb.chunks),
            )
        )
    except Exception as exc:
        console.print(f"[yellow]index state 未写入：{exc}[/yellow]")

    metrics_table(
        f"索引 {backend.collection}",
        [
            ("release_id", kb.release_id),
            ("embedder", model.name),
            ("Milvus 切片", str(written)),
            ("Iceberg 切片", str(lake_n)),
            ("向量列", str(len(backend.vector_fields()))),
            ("带图切片", str(sum(1 for r in rows if r["asset_path"]))),
            ("图型已标注", f"{sum(typed.values())}（{figure_typer}）"),
        ],
    )
    if typed:
        console.print(f"图型分布 {dict(sorted(typed.items(), key=lambda kv: -kv[1]))}")

    # 分档计数：受限内容有没有真的进库，是许可过滤能否被验证的前提
    by_tier: dict[int, int] = {}
    for row in rows:
        by_tier[row["license_rank"]] = by_tier.get(row["license_rank"], 0) + 1
    console.print(f"许可分档 {dict(sorted(by_tier.items()))}")


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
