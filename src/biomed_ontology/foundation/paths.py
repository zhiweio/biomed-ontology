"""Foundation / Ontology 策展与运行时数据路径（分层 SSOT）。

- ``ontology/``：企业实体、词典、claims、映射（Git 策展）
- ``data/foundation/``：evidence / assets / BIOS 子集等运行投影与闸门文件
- ``schema/``：LinkML 模式 SSOT（本模块不涉及）
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "REPO_ROOT",
    "ONTOLOGY_ROOT",
    "FOUNDATION_DATA",
    "ENTITIES_PATH",
    "DICTIONARY_PATH",
    "CLAIMS_PATH",
    "ZINGG_MATCHES_PATH",
    "EVIDENCE_INDEX_PATH",
    "ASSETS_PATH",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
ONTOLOGY_ROOT = REPO_ROOT / "ontology"
FOUNDATION_DATA = REPO_ROOT / "data" / "foundation"

ENTITIES_PATH = ONTOLOGY_ROOT / "entities" / "enterprise_entities.yaml"
DICTIONARY_PATH = ONTOLOGY_ROOT / "dictionary" / "enterprise_dictionary.yaml"
CLAIMS_PATH = ONTOLOGY_ROOT / "claims" / "knowledge_claims.yaml"
ZINGG_MATCHES_PATH = ONTOLOGY_ROOT / "mappings" / "zingg_matches.jsonl"
EVIDENCE_INDEX_PATH = FOUNDATION_DATA / "evidence_index.yaml"
ASSETS_PATH = FOUNDATION_DATA / "assets.yaml"
