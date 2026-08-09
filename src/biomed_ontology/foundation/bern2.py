"""BERN2 NLU 客户端：Recognition + Candidate Normalization。

企业自定义词典优先于远程/本地 BERN2，保证专有名词金标覆盖。
未配置 endpoint 时仅走词典 + 可选 mock annotations。

远程调用复用单个 httpx.Client；批量标注用有界线程池（默认低并发），
避免压垮本机 BERN2。
"""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

from biomed_ontology.foundation.ids import normalize_alias_key

__all__ = [
    "Bern2Client",
    "Bern2Mention",
    "EnterpriseDictionary",
    "load_enterprise_dictionary",
]


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
        concurrency: int = 2,
        min_chars: int = 8,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/") or None
        self.dictionary = dictionary or EnterpriseDictionary()
        self.timeout = timeout
        self.concurrency = max(1, int(concurrency))
        self.min_chars = max(0, int(min_chars))
        self._client: httpx.Client | None = None

    def __enter__(self) -> Bern2Client:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def open(self) -> None:
        if self.base_url and self._client is None:
            self._client = httpx.Client(timeout=self.timeout)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def annotate(self, text: str) -> list[Bern2Mention]:
        dict_hits = self.dictionary.scan(text)
        if not self.base_url:
            return dict_hits
        if len((text or "").strip()) < self.min_chars:
            return dict_hits
        remote = self._remote_plain(text)
        covered = {(h.begin, h.end) for h in dict_hits if h.begin is not None}
        merged = list(dict_hits)
        for m in remote:
            if (m.begin, m.end) in covered:
                continue
            merged.append(m)
        return merged

    def annotate_many(self, texts: Sequence[str]) -> list[list[Bern2Mention]]:
        """有界并发标注；相同正文只打一次远程，结果按输入顺序返回。"""
        if not texts:
            return []
        # 无远程时串行词典扫描即可
        if not self.base_url:
            return [self.annotate(t) for t in texts]

        self.open()
        # 去重：同文只请求一次
        unique_order: list[str] = []
        seen: set[str] = set()
        for t in texts:
            key = t if isinstance(t, str) else str(t or "")
            if key not in seen:
                seen.add(key)
                unique_order.append(key)

        cache: dict[str, list[Bern2Mention]] = {}
        workers = min(self.concurrency, len(unique_order)) or 1

        def _one(text: str) -> tuple[str, list[Bern2Mention]]:
            return text, self.annotate(text)

        if workers == 1:
            for text in unique_order:
                k, v = _one(text)
                cache[k] = v
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_one, text) for text in unique_order]
                for fut in as_completed(futures):
                    k, v = fut.result()
                    cache[k] = v

        return [cache[t if isinstance(t, str) else str(t or "")] for t in texts]

    def _remote_plain(self, text: str) -> list[Bern2Mention]:
        assert self.base_url is not None
        url = f"{self.base_url}/plain"
        client = self._client
        owned = False
        if client is None:
            client = httpx.Client(timeout=self.timeout)
            owned = True
        try:
            resp = client.post(url, json={"text": text})
            resp.raise_for_status()
            payload = resp.json()
        finally:
            if owned:
                client.close()
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
