"""非结构化文档 → 语义树。

算法衍生自 Ontos-AI/knowhere（Apache-2.0），修改点见仓库根目录 NOTICE。

链路：版面后端 → 标题候选（三源合票）→ 章节骨架 → 叶节点 → 语料产物。
每一步都是纯函数且各自可测；只有第一步碰外部世界（PDF 字节 / HTTP）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from biomed_ontology._generated.hmd_concept import LicenseTierEnum, MappingJustificationEnum
from biomed_ontology._generated.hmd_fact import DocTypeEnum, LanguageEnum
from biomed_ontology.config import Settings
from biomed_ontology.observability import TraceContext, new_trace_id
from biomed_ontology.parse.assets import AssetRecord, asset_dir_name
from biomed_ontology.parse.emit import ParsedDocument, emit_document
from biomed_ontology.parse.layout import (
    LayoutBackend,
    LayoutBlock,
    LayoutResult,
    get_layout_backend,
)
from biomed_ontology.parse.layout._pdf_io import read_toc
from biomed_ontology.parse.nodes import LeafNode, assign_blocks, dedupe_same_as
from biomed_ontology.parse.outline import (
    HeadingCandidate,
    extract_toc_nodes,
    grep_headings,
    merge_candidates,
)
from biomed_ontology.parse.router import route_and_extract
from biomed_ontology.parse.skeleton import SectionSkeleton, build_skeleton, fat_leaves
from biomed_ontology.parse.vision import (
    NullVisionProvider,
    VisionCache,
    VisionProvider,
    VisionResult,
)

__all__ = [
    "AssetRecord",
    "HeadingCandidate",
    "LayoutBackend",
    "LayoutBlock",
    "LayoutResult",
    "LeafNode",
    "NullVisionProvider",
    "ParsedDocument",
    "SectionSkeleton",
    "VisionCache",
    "VisionProvider",
    "VisionResult",
    "asset_dir_name",
    "assign_blocks",
    "build_skeleton",
    "build_tree",
    "dedupe_same_as",
    "describe_assets",
    "emit_document",
    "extract_toc_nodes",
    "fat_leaves",
    "get_layout_backend",
    "get_vision_provider",
    "grep_headings",
    "merge_candidates",
    "parse_document",
    "route_and_extract",
]


def build_tree(
    layout: LayoutResult,
    *,
    toc: list[list[object]] | None = None,
    ctx: TraceContext | None = None,
    root_title: str = "Document",
) -> tuple[list[SectionSkeleton], list[LeafNode]]:
    """版面结果 → (骨架, 叶节点)。不碰 IO，两个后端共用同一条路径。

    **跨后端树结构一致性正是靠这里保证的**：后端只负责产出 `LayoutBlock`，
    层级判定与归并逻辑完全共享，于是差异被压缩到 `degraded` 声明的能力上。
    """
    candidates = merge_candidates(
        extract_toc_nodes(toc or []),
        grep_headings(layout),
        ctx=ctx,
    )
    skeleton = build_skeleton(candidates, page_count=layout.page_count or 1, root_title=root_title)
    return skeleton, assign_blocks(skeleton, layout.blocks)


def parse_document(
    path: Path,
    *,
    doc_id: str,
    source_id: str,
    title: str | None = None,
    doc_type: DocTypeEnum = DocTypeEnum.JOURNAL_ARTICLE,
    license_tier: LicenseTierEnum = LicenseTierEnum.TIER_0,
    language: LanguageEnum = LanguageEnum.en,
    external_id: str | None = None,
    published_on: str | None = None,
    out_dir: Path | None = None,
    layout: str | None = None,
    vision: VisionProvider | None = None,
    config: Settings | None = None,
    ctx: TraceContext | None = None,
) -> ParsedDocument:
    ctx = ctx or TraceContext(trace_id=new_trace_id(), ontology_release_id="0.1.0")
    assets = out_dir or Path("data/assets") / asset_dir_name(doc_id)

    with ctx.span("parse.document", doc_id=doc_id):
        result, route = route_and_extract(
            path, assets, ctx=ctx, config=config, forced=layout
        )
        toc = read_toc(path) if path.suffix.casefold() in {".pdf", ".xps", ".epub"} else []
        skeleton, leaves = build_tree(
            result, toc=toc, ctx=ctx, root_title=title or doc_id
        )
        described = describe_assets(
            path, result, assets, vision=vision or get_vision_provider(config), ctx=ctx
        )

    return emit_document(
        doc_id=doc_id,
        source_id=source_id,
        title=title or doc_id,
        doc_type=doc_type,
        license_tier=license_tier,
        language=language,
        skeleton=skeleton,
        leaves=leaves,
        layout=result,
        assets=described,
        external_id=external_id,
        published_on=published_on,
        route=route.as_dict(),
    )


def get_vision_provider(config: Settings | None = None) -> VisionProvider:
    """配置开关的唯一落点。默认 null —— 不配 key 也能跑通全链路。"""
    from biomed_ontology.config import settings as default_settings

    cfg = config or default_settings
    if cfg.vision_provider == "null":
        return NullVisionProvider()

    from biomed_ontology.parse.vision import OpenAIVisionProvider

    provider = OpenAIVisionProvider(
        model=cfg.vision_model,
        api_key=cfg.vision_api_key.get_secret_value(),
        base_url=cfg.vision_base_url,
    )
    return VisionCache(cfg.vision_cache_dir, provider)


_ASSET_PROMPT = (
    "Describe this scientific figure or table for retrieval. "
    "Report every numeric result you can read, with its unit."
)


def describe_assets(
    pdf_path: Path,
    layout: LayoutResult,
    out_dir: Path,
    *,
    vision: VisionProvider,
    ctx: TraceContext | None = None,
) -> dict[tuple[int, tuple[Any, ...]], AssetRecord]:
    """图片/表格 → 落盘的图像 + 可选的文本摘要。

    键与 ``asset_lookup_key(block)`` 对齐：PDF 多为 ``(page, bbox)``；
    Office 无 bbox 时为 ``(page, ('__path__', asset_path))``。

    **渲染与描述是两件事，不能绑在一起**：即使没配 VLM 也要把图渲染出来，
    因为多模态向量列吃的是像素，不是摘要。早先这里对 NullVisionProvider 直接返回空，
    结果是"没配 VLM"连带把视觉检索也悄悄关掉了。

    像素来源：① PDF 族 ``render_regions``；② 版面后端已导出的 ``block.asset_path``
    （Office Docling / MinerU）。二者都没有则记 ``asset.missing_pixels``，不假装看过图。
    """
    from biomed_ontology.parse.assets import (
        asset_lookup_key,
        image_regions,
        load_backend_asset,
        render_regions,
    )

    targets = [b for b in layout.blocks if b.kind in {"image", "table"}]
    if not targets:
        return {}

    regions = image_regions(targets)
    rendered_by_region: dict[tuple[int, tuple[float, ...]], Any] = {}
    if regions:
        for region, asset in zip(
            regions, render_regions(pdf_path, regions, out_dir), strict=False
        ):
            rendered_by_region[region] = asset

    out: dict[tuple[int, tuple[Any, ...]], AssetRecord] = {}
    described = not isinstance(vision, NullVisionProvider)

    for block in targets:
        key = asset_lookup_key(block)
        asset = None
        if len(block.bbox) == 4:
            asset = rendered_by_region.get((block.page, block.bbox))
        if asset is None:
            asset = load_backend_asset(out_dir, getattr(block, "asset_path", None))
        if asset is None:
            if ctx is not None and block.kind == "image":
                ctx.record_decision(
                    stage="parse.vision",
                    justification=MappingJustificationEnum.UnspecifiedMatching,
                    chosen=f"page={block.page}",
                    confidence=0.0,
                    rule_id="asset.missing_pixels",
                    state_after="image 块无 PDF 渲染结果且无后端侧车像素",
                )
            continue

        result = (
            vision.describe(asset.data, prompt=_ASSET_PROMPT, media_type="image/png")
            if described
            else None
        )
        out[key] = AssetRecord(rel_path=asset.rel_path, vision=result)
        if ctx is not None and result is not None:
            for w in result.warnings:
                ctx.record_decision(
                    stage="parse.vision",
                    justification=MappingJustificationEnum.UnspecifiedMatching,
                    chosen=asset.rel_path,
                    confidence=0.0,
                    rule_id="vision.rejected",
                    state_after=w,
                )
    return out

