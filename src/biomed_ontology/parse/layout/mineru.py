"""MinerU 版面后端：本地库（默认）或 HTTP 服务。

- `transport=local`：`import mineru`，经 `do_parse` 写盘后读 content_list
- `transport=http`：纯 HTTP 客户端打 `mineru-api` / 云 API，不 import mineru

knowhere 只用云 API 且只读 `full.md`。我们两处不同：
1. **默认本地**——语料含未公开专利与采购数据，默认不外传；
2. **同时读 `content_list.json`**——Markdown 会丢 bbox，而引用要还原到页面位置。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, cast

from biomed_ontology.observability import TraceContext
from biomed_ontology.parse.layout.base import BlockKind, Capability, LayoutBlock, LayoutResult

__all__ = ["MinerUBackend", "MinerUError", "MinerUTransport"]

MinerUTransport = Literal["local", "http"]

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_WS = re.compile(r"\s+")
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")

_KIND: dict[str, BlockKind] = {
    "text": "text",
    "title": "heading",
    "table": "table",
    "image": "image",
    "equation": "formula",
    "interline_equation": "formula",
}

_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xlsx",
}


class MinerUError(RuntimeError):
    """MinerU 侧失败。刻意不吞：静默降级会产出能力参差的语料。"""


class MinerUBackend:
    name = "mineru"

    def __init__(
        self,
        *,
        transport: MinerUTransport = "local",
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        timeout_s: int = 300,
        mineru_backend: str = "pipeline",
        parse_method: str = "auto",
        lang: str = "ch",
        formula_enable: bool = True,
        table_enable: bool = True,
        effort: str = "medium",
        max_pages: int = 400,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if transport not in {"local", "http"}:
            raise ValueError(f"未知 MinerU transport：{transport!r}")
        self.transport: MinerUTransport = transport
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.mineru_backend = mineru_backend
        self.parse_method = parse_method
        self.lang = lang
        self.formula_enable = formula_enable
        self.table_enable = table_enable
        self.effort = effort
        self.max_pages = max_pages
        self.max_bytes = max_bytes

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() in _SUFFIXES

    def extract(self, path: Path, out_dir: Path, *, ctx: TraceContext) -> LayoutResult:
        size = path.stat().st_size
        if size > self.max_bytes:
            raise ValueError(f"{path.name} 为 {size} 字节，超过上限 {self.max_bytes}")

        out_dir.mkdir(parents=True, exist_ok=True)
        if self.transport == "local":
            return self._extract_local(path, out_dir, ctx=ctx)
        return self._extract_http(path, out_dir, ctx=ctx)

    def _extract_http(self, path: Path, out_dir: Path, *, ctx: TraceContext) -> LayoutResult:
        import httpx

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        content_type = _content_type(path)

        with ctx.span(
            "layout.mineru", doc=path.name, transport="http", endpoint=self.base_url
        ) as span:
            with path.open("rb") as fh:
                resp = httpx.post(
                    f"{self.base_url}/file_parse",
                    files={"files": (path.name, fh, content_type)},
                    data={
                        "return_content_list": "true",
                        "return_md": "true",
                        "backend": self.mineru_backend,
                        "parse_method": self.parse_method,
                        "lang_list": self.lang,
                        "formula_enable": str(self.formula_enable).lower(),
                        "table_enable": str(self.table_enable).lower(),
                    },
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

    def _extract_local(self, path: Path, out_dir: Path, *, ctx: TraceContext) -> LayoutResult:
        try:
            from mineru.cli.common import do_parse
        except ImportError as exc:
            raise MinerUError(
                "MinerU 本地模式需要安装 mineru 包（uv sync，"
                "或设 HMD_MINERU_TRANSPORT=http 改走 HTTP 服务）"
            ) from exc

        stem = _safe_stem(path)
        work = out_dir / "_mineru_work"
        work.mkdir(parents=True, exist_ok=True)
        end_page_id = max(self.max_pages - 1, 0)

        with ctx.span(
            "layout.mineru",
            doc=path.name,
            transport="local",
            mineru_backend=self.mineru_backend,
        ) as span:
            kwargs: dict[str, Any] = {
                "output_dir": str(work),
                "pdf_file_names": [stem],
                "pdf_bytes_list": [path.read_bytes()],
                "p_lang_list": [self.lang],
                "backend": self.mineru_backend,
                "parse_method": self.parse_method,
                "formula_enable": self.formula_enable,
                "table_enable": self.table_enable,
                "f_draw_layout_bbox": False,
                "f_draw_span_bbox": False,
                "f_dump_md": True,
                "f_dump_middle_json": False,
                "f_dump_model_output": False,
                "f_dump_orig_pdf": False,
                "f_dump_content_list": True,
                "start_page_id": 0,
                "end_page_id": end_page_id,
            }
            if self.mineru_backend.startswith("hybrid"):
                kwargs["effort"] = self.effort
            try:
                do_parse(**kwargs)
            except TypeError:
                # 旧版 do_parse 可能不认 effort
                kwargs.pop("effort", None)
                do_parse(**kwargs)
            except Exception as exc:
                raise MinerUError(f"MinerU 本地解析失败：{exc}") from exc

            content_list, md = _load_local_outputs(work, stem, self.parse_method)
            if content_list:
                blocks, degraded = _from_content_list(content_list, out_dir), set()
            elif md:
                blocks, degraded = _from_markdown(md), {"bbox"}
            else:
                raise MinerUError("MinerU 本地输出中既无 content_list 也无 markdown")
            span.attributes["blocks"] = len(blocks)

        return LayoutResult(
            blocks=tuple(blocks),
            assets_dir=out_dir,
            page_count=max((b.page for b in blocks), default=0),
            backend=self.name,
            degraded=cast(tuple[Capability, ...], tuple(sorted(degraded))),
        )


def _safe_stem(path: Path) -> str:
    stem = _SAFE_STEM.sub("_", path.stem)[:80] or "doc"
    return stem


def _load_local_outputs(
    work: Path, stem: str, parse_method: str
) -> tuple[list[dict[str, Any]] | None, str]:
    """兼容 `{stem}/{parse_method}/` 与扁平目录两种落盘布局。"""
    candidates = [
        work / stem / parse_method,
        work / stem / "auto",
        work / stem / "txt",
        work / stem / "ocr",
        work / stem,
        work,
    ]
    content_list: list[dict[str, Any]] | None = None
    md = ""
    for base in candidates:
        if not base.is_dir():
            continue
        for name in (f"{stem}_content_list.json", f"{stem}_content_list_v2.json"):
            cl_path = base / name
            if cl_path.is_file():
                raw = json.loads(cl_path.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    content_list = raw
                    break
                if isinstance(raw, dict) and isinstance(raw.get("content_list"), list):
                    content_list = raw["content_list"]
                    break
        md_path = base / f"{stem}.md"
        if md_path.is_file():
            md = md_path.read_text(encoding="utf-8")
        if content_list is not None or md:
            break
    # 递归兜底：找任意 *_content_list.json
    if content_list is None:
        for hit in work.rglob("*_content_list.json"):
            raw = json.loads(hit.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                content_list = raw
                break
    if not md:
        for hit in work.rglob("*.md"):
            md = hit.read_text(encoding="utf-8")
            if md.strip():
                break
    return content_list, md


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
    return _from_markdown(md), {"bbox"}


def _from_content_list(items: list[dict[str, Any]], out_dir: Path) -> list[LayoutBlock]:
    blocks: list[LayoutBlock] = []
    for idx, item in enumerate(items):
        kind = _KIND.get(str(item.get("type", "")), "text")
        page = int(item.get("page_idx", 0)) + 1
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


_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _content_type(path: Path) -> str:
    return _MIME.get(path.suffix.casefold(), "application/octet-stream")
