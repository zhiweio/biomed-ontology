"""版面后端边界：法务闸门与降级声明。

实现在 P10；这里锁的是"启用某个后端"这条路径上必须成立的性质。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biomed_ontology.config import load_settings
from biomed_ontology.licensing import LicenseViolation
from biomed_ontology.parse.layout import LayoutBlock, LayoutResult, get_layout_backend


def test_enabling_a_backend_requires_legal_clearance():
    """默认后端 PyMuPDF 的 AGPL 义务同样未结论，闸门对它一视同仁。"""
    with pytest.raises(LicenseViolation, match="尚未经法务结论"):
        get_layout_backend(config=load_settings({}))


def test_explicit_acknowledgement_reaches_the_implementation():
    cfg = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "true"})
    assert get_layout_backend(config=cfg).name == "pymupdf"


def test_backend_switch_is_the_only_thing_that_changes_the_implementation():
    """配置开关的唯一落点。各调用处自己 import 会让闸门形同虚设。"""
    cfg = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "true", "HMD_LAYOUT_BACKEND": "mineru"})
    assert get_layout_backend(config=cfg).name == "mineru"
    assert get_layout_backend("pymupdf", config=cfg).name == "pymupdf"


def test_unknown_backend_is_rejected():
    cfg = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "true"})
    with pytest.raises(ValueError, match="未知版面后端"):
        get_layout_backend("qdrant", config=cfg)


def test_missing_bbox_is_empty_not_fabricated():
    """拿不到坐标就留空。伪造成整页坐标会让引用看起来精确而实际指错地方。"""
    block = LayoutBlock(kind="text", text="ORR 49.2%", page=3)
    assert block.bbox == ()


def test_page_numbers_are_one_based_original_pages():
    block = LayoutBlock(kind="heading", text="## Results", page=1, level=2)
    assert block.page >= 1


def test_degraded_capabilities_are_carried_on_the_result():
    result = LayoutResult(
        blocks=(LayoutBlock(kind="text", text="x", page=1),),
        assets_dir=Path("data/assets/DOC_1"),
        page_count=1,
        backend="pymupdf",
        degraded=("formula", "ocr"),
    )
    assert "formula" in result.degraded
