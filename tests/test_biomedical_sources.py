"""BiomedicalSource 插件：BIOS 常路径 + UMLS 接口占位。"""

from __future__ import annotations

import pytest

from biomed_ontology.foundation.biomedical_sources import (
    SOURCE_REGISTRY,
    load_biomedical_source,
)


def test_bios_and_umls_are_registered():
    assert "bios_v3" in SOURCE_REGISTRY
    assert "umls_subset" in SOURCE_REGISTRY
    assert SOURCE_REGISTRY["umls_subset"].graph_uri.endswith("graph/biomedical")


def test_umls_requires_license_ack():
    with pytest.raises(PermissionError, match="HMD_UMLS_LICENSE_ACK"):
        load_biomedical_source("umls_subset", license_ack="")


def test_umls_loader_not_implemented_after_ack():
    with pytest.raises(NotImplementedError, match="尚未实现"):
        load_biomedical_source("umls_subset", license_ack="poc")


def test_unknown_source_is_loud():
    with pytest.raises(KeyError, match="未知 biomedical source"):
        load_biomedical_source("not_a_source", license_ack="poc")
