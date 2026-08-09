"""版面后端边界：法务闸门与降级声明。"""

from __future__ import annotations

from pathlib import Path

import pytest

from biomed_ontology.config import load_settings
from biomed_ontology.licensing import LicenseViolation
from biomed_ontology.parse.layout import LayoutBlock, LayoutResult, get_layout_backend


def test_enabling_a_backend_requires_legal_clearance():
    """默认 auto 不能直接 get_layout_backend；显式 Fast Path 同样受闸门约束。"""
    with pytest.raises(LicenseViolation, match="尚未经法务结论"):
        get_layout_backend(
            "pymupdf4llm",
            config=load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "false"}),
        )


def test_explicit_acknowledgement_reaches_the_implementation():
    cfg = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "true"})
    assert get_layout_backend("pymupdf4llm", config=cfg).name == "pymupdf4llm"


def test_backend_switch_is_the_only_thing_that_changes_the_implementation():
    cfg = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "true", "HMD_LAYOUT_BACKEND": "mineru"})
    assert get_layout_backend(config=cfg).name == "mineru"
    assert get_layout_backend("docling", config=cfg).name == "docling"


def test_pymupdf_alias_is_rejected():
    cfg = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "true"})
    with pytest.raises(ValueError, match="已废弃"):
        get_layout_backend("pymupdf", config=cfg)


def test_auto_must_go_through_router():
    cfg = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "true", "HMD_LAYOUT_BACKEND": "auto"})
    with pytest.raises(ValueError, match="Document Router"):
        get_layout_backend(config=cfg)


def test_unknown_backend_is_rejected():
    cfg = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "true"})
    with pytest.raises(ValueError, match="未知版面后端"):
        get_layout_backend("qdrant", config=cfg)


def test_missing_bbox_is_empty_not_fabricated():
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
        backend="pymupdf4llm",
        degraded=("formula", "ocr"),
    )
    assert "formula" in result.degraded
