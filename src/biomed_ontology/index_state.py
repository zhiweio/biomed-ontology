"""文献 index 状态：catalog fingerprint + 上次增量写回元数据。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from biomed_ontology.pipeline import DATA_ROOT, DEFAULT_RELEASE, ONTOLOGY_CATALOG

__all__ = [
    "DEFAULT_STATE_PATH",
    "LiteratureIndexState",
    "compute_catalog_fingerprint",
    "load_state",
    "save_state",
]

DEFAULT_STATE_PATH = DATA_ROOT / "cache" / "literature_index_state.json"


@dataclass
class LiteratureIndexState:
    catalog_sha256: str = ""
    release_id: str = DEFAULT_RELEASE
    embedder: str = ""
    collection: str = ""
    updated_at: str = ""
    chunk_count: int = 0
    dirty_last_run: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def compute_catalog_fingerprint(catalog_dir: Path | None = None) -> str:
    """规范化哈希 ``ontology/catalog/*.yaml``（含 ambiguity.yaml）。"""
    catalog = catalog_dir or ONTOLOGY_CATALOG
    if not catalog.is_dir():
        raise FileNotFoundError(f"ontology catalog 不存在：{catalog}")
    files = sorted(catalog.glob("*.yaml"))
    h = hashlib.sha256()
    for path in files:
        # path 相对名 + 内容，避免同内容不同文件名碰撞
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def load_state(path: Path | None = None) -> LiteratureIndexState | None:
    p = path or DEFAULT_STATE_PATH
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    known = {f.name for f in LiteratureIndexState.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in raw.items() if k in known and k != "extra"}
    extra = {k: v for k, v in raw.items() if k not in known}
    if isinstance(raw.get("extra"), dict):
        extra = {**extra, **raw["extra"]}
    return LiteratureIndexState(**kwargs, extra=extra)


def save_state(state: LiteratureIndexState, path: Path | None = None) -> Path:
    p = path or DEFAULT_STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = state.to_dict()
    if not state.updated_at:
        payload["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state.updated_at = payload["updated_at"]
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p
