"""BERN2 NLU 客户端：Recognition + Candidate Normalization。

企业自定义词典优先于远程/本地 BERN2，保证专有名词金标覆盖。
未配置 endpoint 时仅走词典 + 可选 mock annotations。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from biomed_ontology.foundation.ids import normalize_alias_key

__all__ = ["Bern2Client", "Bern2Mention", "EnterpriseDictionary", "load_enterprise_dictionary"]


@dataclass(frozen=True)
class Bern2Mention:
    mention: str
    obj_type: str
    ids: list[str]
    begin: int | None = None
    end: int | None = None
    prob: float = 1.0
    source: str = "bern2"


@dataclass
class EnterpriseDictionary:
    """BERN2 自定义词典：mention → 外部标准 ID（ChEBI/DrugBank/…）或直接 Enterprise ID。"""

    entries: list[dict[str, Any]] = field(default_factory=list)
    _norm_index: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        idx: dict[str, dict[str, Any]] = {}
        for e in self.entries:
            for alias in [*e.get("aliases", []), e.get("mention", "")]:
                if not alias:
                    continue
                idx[normalize_alias_key(str(alias))] = e
        self._norm_index = idx

    def lookup(self, text: str) -> dict[str, Any] | None:
        return self._norm_index.get(normalize_alias_key(text))

    def scan(self, text: str) -> list[Bern2Mention]:
        """简单全词扫描（专有名词金标路径；不依赖 BERN2 服务）。"""
        lowered = text.lower()
        hits: list[Bern2Mention] = []
        seen: set[str] = set()
        # 长别名优先，避免短码误切
        aliases = sorted(self._norm_index.keys(), key=len, reverse=True)
        for key in aliases:
            if key in seen or key not in lowered:
                continue
            entry = self._norm_index[key]
            start = lowered.find(key)
            ids = list(entry.get("external_ids", []))
            if entry.get("enterprise_id"):
                ids = [entry["enterprise_id"], *ids]
            hits.append(
                Bern2Mention(
                    mention=text[start : start + len(key)],
                    obj_type=str(entry.get("type", "chemical")),
                    ids=ids,
                    begin=start,
                    end=start + len(key),
                    prob=1.0,
                    source="enterprise_dictionary",
                )
            )
            seen.add(key)
        return hits


def load_enterprise_dictionary(path: Path) -> EnterpriseDictionary:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return EnterpriseDictionary(entries=list(raw.get("entries", [])))


class Bern2Client:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        dictionary: EnterpriseDictionary | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/") or None
        self.dictionary = dictionary or EnterpriseDictionary()
        self.timeout = timeout

    def annotate(self, text: str) -> list[Bern2Mention]:
        dict_hits = self.dictionary.scan(text)
        if not self.base_url:
            return dict_hits
        remote = self._remote_plain(text)
        # 词典命中覆盖同 span 的远程结果
        covered = {(h.begin, h.end) for h in dict_hits if h.begin is not None}
        merged = list(dict_hits)
        for m in remote:
            if (m.begin, m.end) in covered:
                continue
            merged.append(m)
        return merged

    def _remote_plain(self, text: str) -> list[Bern2Mention]:
        assert self.base_url is not None
        url = f"{self.base_url}/plain"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json={"text": text})
            resp.raise_for_status()
            payload = resp.json()
        out: list[Bern2Mention] = []
        for ann in payload.get("annotations", []):
            span = ann.get("span") or {}
            out.append(
                Bern2Mention(
                    mention=str(ann.get("mention", "")),
                    obj_type=str(ann.get("obj", "unknown")),
                    ids=[str(i) for i in ann.get("id") or [] if i],
                    begin=span.get("begin"),
                    end=span.get("end"),
                    prob=float(ann.get("prob") or 0.0),
                    source="bern2",
                )
            )
        return out
