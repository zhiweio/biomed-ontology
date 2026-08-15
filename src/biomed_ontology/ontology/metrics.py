"""指标受控词表。口径变更走 ontology release，抽取器不得私写 ORR/PFS。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

__all__ = ["MetricTerm", "MetricVocab", "load_metric_vocab"]

_DEFAULT = Path(__file__).resolve().parents[3] / "ontology" / "extract" / "table_metrics.yaml"


@dataclass(frozen=True)
class MetricTerm:
    key: str
    metric: str
    unit: str
    definition: str = ""
    population: str = ""


@dataclass(frozen=True)
class MetricVocab:
    version: str
    terms: tuple[MetricTerm, ...]

    def canonicalize(self, header: str) -> MetricTerm | None:
        return self._by_key.get(header.strip().casefold())

    @property
    def _by_key(self) -> dict[str, MetricTerm]:
        return {t.key: t for t in self.terms}

    def as_header_map(self) -> dict[str, tuple[str, str]]:
        return {t.key: (t.metric, t.unit) for t in self.terms}


@lru_cache(maxsize=4)
def load_metric_vocab(path: str | None = None) -> MetricVocab:
    p = Path(path) if path else _DEFAULT
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    version = str(raw.get("version") or "0.0.0")
    terms: list[MetricTerm] = []
    for key, spec in (raw.get("metrics") or {}).items():
        if not isinstance(spec, dict):
            continue
        terms.append(
            MetricTerm(
                key=str(key).casefold(),
                metric=str(spec.get("metric") or key),
                unit=str(spec.get("unit") or ""),
                definition=str(spec.get("definition") or ""),
                population=str(spec.get("population") or ""),
            )
        )
    return MetricVocab(version=version, terms=tuple(terms))
