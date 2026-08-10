"""图片资产：从 PDF 页面渲染出图块并交给视觉后端。

渲染而不是抽取嵌入图：科研图表常由矢量指令绘制，PDF 里根本没有"一张图"可抽，
只有一堆线段。按 bbox 渲染页面区域才能拿到人眼看到的那张图。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "AssetRecord",
    "RenderedAsset",
    "asset_dir_name",
    "asset_lookup_key",
    "image_regions",
    "load_backend_asset",
    "render_regions",
    "resolve_asset",
    "safe_asset_name",
]

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_DOTS = re.compile(r"\.{2,}")


@dataclass(frozen=True)
class RenderedAsset:
    rel_path: str
    page: int
    bbox: tuple[float, ...]
    data: bytes


@dataclass(frozen=True)
class AssetRecord:
    """一个图块的两份产物：落盘的像素，以及可选的 VLM 文本摘要。

    分成两个字段而不是一个，是因为二者的可得性不同：像素总有，摘要只在配了 VLM 时才有。
    """

    rel_path: str
    vision: Any = None

    @property
    def summary(self) -> str:
        return getattr(self.vision, "summary", "") or ""

    @property
    def extracted(self) -> dict[str, str]:
        return dict(getattr(self.vision, "extracted", {}) or {})


def asset_dir_name(doc_id: str) -> str:
    """doc_id → 存放该文档资产的目录名。`DOC:PMC12133497` → `DOC_PMC12133497`。

    CURIE 里的冒号在 Windows 上不是合法文件名字符，落盘时必须换掉。
    写入方与读取方共用这一个函数，而不是各写一遍同样的 `replace` ——
    两处各写一遍，就意味着两处可以不一致，而下面 `resolve_asset` 的注释
    说的正是这种不一致上一次是怎么无声发生的。
    """
    return doc_id.replace(":", "_").replace("/", "_")


def resolve_asset(root: Path | None, doc_id: Any, rel_path: Any) -> str | None:
    """``data/assets/<doc_id>/`` + 切片相对路径 → 本机绝对路径；缺失返回 None。

    切片内路径（如 ``images/p0002_r000.png``）相对 ``render_regions`` 的
    ``out_dir``，必须带 ``doc_id`` 段。路径拼装只允许这一处，避免读写不一致。
    读不到图时视觉列会退回编码 caption，指标上难察觉，故失败须显式为 None。
    """
    if not rel_path or not doc_id or root is None:
        return None
    path = root / asset_dir_name(str(doc_id)) / str(rel_path)
    return str(path) if path.is_file() else None


def safe_asset_name(stem: str, suffix: str) -> str:
    """资产文件名只由白名单字符构成 —— 文档内容不得参与路径拼接。

    去掉分隔符还不够：`..` 在某些拼接场景里仍能上跳一级，一并压掉。
    """
    cleaned = _DOTS.sub("_", _UNSAFE.sub("_", stem))[:80] or "asset"
    return f"{cleaned}{suffix}"


def render_regions(
    pdf_path: Path,
    regions: list[tuple[int, tuple[float, ...]]],
    out_dir: Path,
    *,
    dpi: int = 144,
) -> list[RenderedAsset]:
    from biomed_ontology.config import settings
    from biomed_ontology.licensing import assert_component_cleared

    # 渲染绕开了版面后端直接调底层 PDF 库，法务闸门挂在 pymupdf4llm 上 ——
    # 否则 layout_backend=mineru/docling 时，AGPL 组件会从这条侧门被拉进来。
    assert_component_cleared("pymupdf4llm", accept_uncleared=settings.accept_uncleared_components)

    suf = pdf_path.suffix.casefold()
    if suf not in {".pdf", ".xps", ".epub"}:
        # Office / 图像：无页面 pixmap；调用方应使用后端已导出资产
        return []

    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    zoom = dpi / 72.0
    assets: list[RenderedAsset] = []

    with pymupdf.open(pdf_path) as doc:
        for idx, (page_no, bbox) in enumerate(regions):
            if not 1 <= page_no <= doc.page_count:
                continue
            page = doc.load_page(page_no - 1)
            clip = pymupdf.Rect(*bbox) if len(bbox) == 4 else page.rect
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
            data: bytes = pix.tobytes("png")

            name = safe_asset_name(f"p{page_no:04d}_r{idx:03d}", ".png")
            rel = f"images/{name}"
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            assets.append(RenderedAsset(rel_path=rel, page=page_no, bbox=tuple(clip), data=data))
    return assets


def image_regions(blocks: Any) -> list[tuple[int, tuple[float, ...]]]:
    """从版面块里挑出需要渲染的区域。没有 bbox 的跳过 —— 渲染整页会把正文也当图。"""
    return [(b.page, b.bbox) for b in blocks if b.kind in {"image", "table"} and len(b.bbox) == 4]


def asset_lookup_key(block: Any) -> tuple[int, tuple[Any, ...]]:
    """``describe_assets`` / ``emit`` 共用的资产查找键。

    有合法 bbox 时用 ``(page, bbox)``；Office 常无 bbox，改用 ``asset_path``
    避免同页多图撞在 ``(page, ())`` 上互相覆盖。
    """
    page = int(getattr(block, "page", 0) or 0)
    bbox = tuple(getattr(block, "bbox", ()) or ())
    if len(bbox) == 4:
        return page, bbox
    rel = str(getattr(block, "asset_path", None) or "")
    return page, ("__path__", rel)


def load_backend_asset(out_dir: Path, rel_path: str | None) -> RenderedAsset | None:
    """读取版面后端已写入 ``out_dir`` 的侧车图（Office Docling / MinerU）。"""
    if not rel_path:
        return None
    rel = str(rel_path).replace("\\", "/").lstrip("/")
    if ".." in rel.split("/"):
        return None
    path = out_dir / rel
    if not path.is_file():
        return None
    return RenderedAsset(rel_path=rel, page=0, bbox=(), data=path.read_bytes())
