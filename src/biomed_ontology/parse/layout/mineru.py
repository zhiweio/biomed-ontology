"""MinerU 版面后端：**纯 HTTP 客户端，绝不 import mineru**。

理由是依赖隔离：`mineru[all]` 会把 vLLM + torch + ray 拖进本项目的依赖树，
而我们只需要它的解析结果。同一个客户端既能打自建 `mineru-api`，
也能打官方云 API，只差 base_url 与鉴权。

knowhere 只用云 API 且只读 `full.md`。我们两处不同：
1. **优先自建**——语料含未公开专利与采购数据，默认不外传第三方；
2. **同时读 `content_list.json`**——Markdown 会丢 bbox，而引用要还原到页面位置。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from biomed_ontology.observability import TraceContext
from biomed_ontology.parse.layout.base import BlockKind, Capability, LayoutBlock, LayoutResult

__all__ = ["MinerUBackend", "MinerUError"]

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_WS = re.compile(r"\s+")

# MinerU content_list 的 type → 我们的 kind。未知类型一律当正文，
# 不猜 —— 猜错会让内容悄悄换个语义进库。
_KIND: dict[str, BlockKind] = {
    "text": "text",
    "title": "heading",
    "table": "table",
    "image": "image",
    "equation": "formula",
    "interline_equation": "formula",
}


class MinerUError(RuntimeError):
    """MinerU 侧失败。刻意不吞：静默降级会产出能力参差的语料。"""


class MinerUBackend:
    name = "mineru"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        timeout_s: int = 300,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() in {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".ppt"}

    def extract(self, path: Path, out_dir: Path, *, ctx: TraceContext) -> LayoutResult:
        import httpx

        out_dir.mkdir(parents=True, exist_ok=True)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        with ctx.span("layout.mineru", doc=path.name, endpoint=self.base_url) as span:
            with path.open("rb") as fh:
                resp = httpx.post(
                    f"{self.base_url}/file_parse",
                    files={"files": (path.name, fh, "application/pdf")},
                    data={"return_content_list": "true", "return_md": "true"},
                    headers=headers,
                    timeout=self.timeout_s,
                )
            if resp.status_code >= 400:
                raise MinerUError(f"MinerU 返回 {resp.status_code}：{resp.text[:200]}")
            payload = resp.json()
            blocks, degraded = _parse_payload(payload, out_dir)
            span.attributes["blocks"] = len(blocks)

        return LayoutResult(
            blocks=tuple(blocks),
            assets_dir=out_dir,
            page_count=max((b.page for b in blocks), default=0),
            backend=self.name,
            degraded=tuple(sorted(degraded)),
        )


def _parse_payload(
    payload: dict[str, Any], out_dir: Path
) -> tuple[list[LayoutBlock], set[Capability]]:
    results = payload.get("results") or {}
    first = next(iter(results.values()), {}) if isinstance(results, dict) else {}
    content_list = first.get("content_list") or payload.get("content_list")

    if isinstance(content_list, str):
        content_list = json.loads(content_list)
    if content_list:
        return _from_content_list(content_list, out_dir), set()

    md = first.get("md_content") or payload.get("md_content") or ""
    if not md:
        raise MinerUError("MinerU 响应里既无 content_list 也无 md_content")
    # 退回纯 Markdown：页码与坐标都拿不到，必须如实声明而不是填 0 冒充
    return _from_markdown(md), {"bbox"}


def _from_content_list(items: list[dict[str, Any]], out_dir: Path) -> list[LayoutBlock]:
    blocks: list[LayoutBlock] = []
    for idx, item in enumerate(items):
        kind = _KIND.get(str(item.get("type", "")), "text")
        page = int(item.get("page_idx", 0)) + 1  # MinerU 是 0-based，对外一律 1-based
        bbox = tuple(float(v) for v in item.get("bbox", ()) or ())
        text, asset = _item_text(item, kind, idx, out_dir)
        if not text:
            continue
        level = int(item.get("text_level", 1)) if kind == "heading" else None
        if kind == "heading":
            text = "#" * (level or 1) + " " + text
        blocks.append(
            LayoutBlock(
                kind=kind,
                text=text,
                page=page,
                bbox=bbox,
                level=level,
                asset_path=asset,
                backend_meta={"mineru_type": item.get("type")},
            )
        )
    return blocks


def _item_text(
    item: dict[str, Any], kind: BlockKind, idx: int, out_dir: Path
) -> tuple[str, str | None]:
    if kind == "table":
        html = str(item.get("table_body") or "")
        if not html:
            return "", None
        rel = f"tables/mineru_{idx:04d}.html"
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")
        caption = " ".join(item.get("table_caption") or ())
        return (f"{caption}\n{html}".strip(), rel)
    if kind == "image":
        caption = " ".join(item.get("img_caption") or ())
        # 资产路径由 MinerU 给出，只取 basename —— 拼接远端字符串是路径穿越入口
        remote = str(item.get("img_path") or "")
        rel = f"images/{Path(remote).name}" if remote else None
        return caption, rel
    if kind == "formula":
        return str(item.get("text") or ""), None
    return _WS.sub(" ", str(item.get("text") or "")).strip(), None


def _from_markdown(md: str) -> list[LayoutBlock]:
    blocks: list[LayoutBlock] = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _HEADING.match(line)
        if m:
            blocks.append(LayoutBlock(kind="heading", text=line, page=1, level=len(m.group(1))))
        else:
            blocks.append(LayoutBlock(kind="text", text=line, page=1))
    return blocks
