"""MinerU 后端：用录制的 HTTP 响应测，CI 不打真网络。

真网络测试会在别人的服务宕机时把我们的 CI 染红，而那和我们的代码无关。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from biomed_ontology.observability import TraceContext, new_trace_id
from biomed_ontology.parse import build_tree
from biomed_ontology.parse.layout.mineru import MinerUBackend, MinerUError

BASE = "http://mineru.test"

_CONTENT_LIST = [
    {
        "type": "title",
        "text": "Abstract",
        "text_level": 1,
        "page_idx": 0,
        "bbox": [10, 10, 300, 30],
    },
    {
        "type": "text",
        "text": "Surufatinib is an oral angio-immuno kinase inhibitor.",
        "page_idx": 0,
        "bbox": [10, 40, 300, 60],
    },
    {"type": "title", "text": "Methods", "text_level": 1, "page_idx": 1},
    {"type": "equation", "text": "E = mc^2", "page_idx": 1},
    {
        "type": "table",
        "table_body": "<table><tr><td>ORR</td><td>49.2%</td></tr></table>",
        "table_caption": ["Table 1. Response"],
        "page_idx": 1,
    },
    {"type": "image", "img_path": "images/fig1.jpg", "img_caption": ["Figure 1."], "page_idx": 2},
]


def _ctx() -> TraceContext:
    return TraceContext(trace_id=new_trace_id(), ontology_release_id="0.1.0")


def _pdf(tmp_path: Path) -> Path:
    p = tmp_path / "in.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    return p


@respx.mock
def test_content_list_gives_pages_and_bboxes(tmp_path: Path):
    """只读 full.md 会丢掉 bbox —— 而引用要还原到页面位置，所以我们读 content_list。"""
    respx.post(f"{BASE}/file_parse").mock(
        return_value=httpx.Response(
            200, json={"results": {"in.pdf": {"content_list": _CONTENT_LIST}}}
        )
    )
    result = MinerUBackend(transport="http", base_url=BASE).extract(
        _pdf(tmp_path), tmp_path / "out", ctx=_ctx()
    )

    abstract = result.blocks[0]
    assert abstract.kind == "heading"
    assert abstract.page == 1, "MinerU 是 0-based，对外必须一律 1-based"
    assert abstract.bbox == (10.0, 10.0, 300.0, 30.0)
    assert result.page_count == 3
    assert result.degraded == ()


@respx.mock
def test_markdown_only_response_declares_missing_bbox(tmp_path: Path):
    """拿不到坐标就声明缺失，不是填 0 冒充。"""
    respx.post(f"{BASE}/file_parse").mock(
        return_value=httpx.Response(200, json={"md_content": "# Results\n\nORR was 49.2%."})
    )
    result = MinerUBackend(transport="http", base_url=BASE).extract(
        _pdf(tmp_path), tmp_path / "out", ctx=_ctx()
    )
    assert "bbox" in result.degraded
    assert all(b.bbox == () for b in result.blocks)


@respx.mock
def test_table_html_is_written_as_an_asset(tmp_path: Path):
    respx.post(f"{BASE}/file_parse").mock(
        return_value=httpx.Response(200, json={"content_list": json.dumps(_CONTENT_LIST)})
    )
    out = tmp_path / "out"
    result = MinerUBackend(transport="http", base_url=BASE).extract(_pdf(tmp_path), out, ctx=_ctx())
    table = next(b for b in result.blocks if b.kind == "table")
    assert table.asset_path is not None
    assert (out / table.asset_path).read_text(encoding="utf-8").startswith("<table>")


@respx.mock
def test_remote_image_path_cannot_escape_the_asset_directory(tmp_path: Path):
    """远端返回的路径是不可信输入。直接拼接就是路径穿越。"""
    respx.post(f"{BASE}/file_parse").mock(
        return_value=httpx.Response(
            200,
            json={
                "content_list": [
                    {
                        "type": "image",
                        "img_path": "../../../../etc/passwd",
                        "img_caption": ["evil"],
                        "page_idx": 0,
                    }
                ]
            },
        )
    )
    result = MinerUBackend(transport="http", base_url=BASE).extract(
        _pdf(tmp_path), tmp_path / "out", ctx=_ctx()
    )
    img = next(b for b in result.blocks if b.kind == "image")
    assert img.asset_path == "images/passwd"
    assert ".." not in (img.asset_path or "")


@respx.mock
def test_backend_failure_propagates_instead_of_degrading_silently(tmp_path: Path):
    """静默降级会产出能力参差的语料，而没人知道是哪一部分。"""
    respx.post(f"{BASE}/file_parse").mock(return_value=httpx.Response(503, text="overloaded"))
    with pytest.raises(MinerUError, match="503"):
        MinerUBackend(transport="http", base_url=BASE).extract(
            _pdf(tmp_path), tmp_path / "out", ctx=_ctx()
        )


@respx.mock
def test_empty_response_is_an_error_not_an_empty_document(tmp_path: Path):
    respx.post(f"{BASE}/file_parse").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(MinerUError):
        MinerUBackend(transport="http", base_url=BASE).extract(
            _pdf(tmp_path), tmp_path / "out", ctx=_ctx()
        )


@respx.mock
def test_api_key_is_sent_only_when_configured(tmp_path: Path):
    route = respx.post(f"{BASE}/file_parse").mock(
        return_value=httpx.Response(200, json={"content_list": _CONTENT_LIST})
    )
    MinerUBackend(transport="http", base_url=BASE).extract(
        _pdf(tmp_path), tmp_path / "o1", ctx=_ctx()
    )
    assert "authorization" not in route.calls[0].request.headers

    MinerUBackend(transport="http", base_url=BASE, api_key="k").extract(
        _pdf(tmp_path), tmp_path / "o2", ctx=_ctx()
    )
    assert route.calls[1].request.headers["authorization"] == "Bearer k"


@respx.mock
def test_mineru_tree_matches_layout_for_the_same_blocks(tmp_path: Path):
    """验收项：content_list 进共享 build_tree 后 section_path 稳定。"""
    respx.post(f"{BASE}/file_parse").mock(
        return_value=httpx.Response(200, json={"content_list": _CONTENT_LIST})
    )
    mineru = MinerUBackend(transport="http", base_url=BASE).extract(
        _pdf(tmp_path), tmp_path / "out", ctx=_ctx()
    )
    skeleton, _ = build_tree(mineru)
    assert [s.section_path for s in skeleton] == ["Abstract", "Methods"]


def test_local_transport_reads_do_parse_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """本地模式：mock do_parse，断言从落盘 content_list 还原块。"""
    import sys
    import types

    def _fake_do_parse(**kwargs):
        out = Path(kwargs["output_dir"])
        stem = kwargs["pdf_file_names"][0]
        method = kwargs.get("parse_method") or "auto"
        dest = out / stem / method
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{stem}_content_list.json").write_text(json.dumps(_CONTENT_LIST), encoding="utf-8")

    pkg = types.ModuleType("mineru")
    cli = types.ModuleType("mineru.cli")
    common = types.ModuleType("mineru.cli.common")
    common.do_parse = _fake_do_parse  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "mineru", pkg)
    monkeypatch.setitem(sys.modules, "mineru.cli", cli)
    monkeypatch.setitem(sys.modules, "mineru.cli.common", common)

    result = MinerUBackend(transport="local").extract(_pdf(tmp_path), tmp_path / "out", ctx=_ctx())
    assert result.backend == "mineru"
    assert result.blocks[0].kind == "heading"
    assert result.blocks[0].page == 1


def test_local_transport_missing_package_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import sys

    # sys.modules[name] is None → import 抛 ModuleNotFoundError
    monkeypatch.setitem(sys.modules, "mineru", None)
    monkeypatch.setitem(sys.modules, "mineru.cli", None)
    monkeypatch.setitem(sys.modules, "mineru.cli.common", None)
    with pytest.raises(MinerUError, match="需要安装 mineru"):
        MinerUBackend(transport="local").extract(_pdf(tmp_path), tmp_path / "out", ctx=_ctx())


def test_registry_defaults_to_local_transport():
    from biomed_ontology.config import load_settings
    from biomed_ontology.parse.layout.mineru import MinerUBackend
    from biomed_ontology.parse.layout.registry import get_layout_backend

    cfg = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "true", "HMD_LAYOUT_BACKEND": "mineru"})
    backend = get_layout_backend(config=cfg)
    assert isinstance(backend, MinerUBackend)
    assert backend.transport == "local"
