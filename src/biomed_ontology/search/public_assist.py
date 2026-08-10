"""查询期公开实体辅助：exact xref → ENT；无 ENT → BIOS 表面词全文改写。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from biomed_ontology.foundation.bios_lookup import hydrate_search_surfaces
from biomed_ontology.foundation.graphdb import GraphDbClient
from biomed_ontology.foundation.resolve import ResolutionIndex

__all__ = [
    "PublicAssistResult",
    "PublicLexicalExpand",
    "PublicNenAssist",
    "HasBern2Annotate",
]


class HasBern2Annotate(Protocol):
    def annotate(self, text: str) -> list[Any]: ...


@dataclass
class PublicAssistResult:
    ent_seeds: list[str] = field(default_factory=list)
    expansion_terms: list[str] = field(default_factory=list)
    expansion_source: str = "none"  # enterprise | public_lexical | client_terms | assist_xref | none
    seed_sources: dict[str, str] = field(default_factory=dict)


class PublicNenAssist:
    """BERN2/公开 ID → 唯一 exact xref → HMD:ENT:*（不写 catalog）。"""

    def __init__(
        self,
        index: ResolutionIndex,
        *,
        bern2: HasBern2Annotate | None = None,
        graphdb: GraphDbClient | None = None,
    ) -> None:
        self.index = index
        self.bern2 = bern2
        self.graphdb = graphdb

    def propose_ents(
        self,
        query: str,
        *,
        exclude: set[str] | frozenset[str] | None = None,
        top_mentions: int = 8,
    ) -> list[str]:
        exclude = set(exclude or ())
        out: list[str] = []
        seen: set[str] = set()

        # 查询本身是公开 CURIE
        for token in _curie_tokens(query):
            ents = self.index.lookup_exact_external(token)
            if len(ents) == 1 and ents[0] not in exclude:
                if ents[0] not in seen:
                    seen.add(ents[0])
                    out.append(ents[0])

        mentions: list[Any] = []
        if self.bern2 is not None:
            try:
                mentions = list(self.bern2.annotate(query) or [])
            except Exception:
                mentions = []

        mentions = sorted(
            mentions,
            key=lambda m: (-(getattr(m, "end", 0) - getattr(m, "begin", 0)), -float(getattr(m, "prob", 0) or 0)),
        )[:top_mentions]

        for m in mentions:
            ids = [str(x) for x in (getattr(m, "ids", None) or []) if x]
            public_ids = [i for i in ids if not str(i).startswith("HMD:ENT:")]
            hit_ents: list[str] = []
            for xid in public_ids:
                hit_ents.extend(self.index.lookup_exact_external(xid))
            hit_ents = list(dict.fromkeys(hit_ents))
            if len(hit_ents) == 1 and hit_ents[0] not in exclude and hit_ents[0] not in seen:
                seen.add(hit_ents[0])
                out.append(hit_ents[0])
        return out


class PublicLexicalExpand:
    """无 ENT 时：BERN2/公开 ID → BIOS 名 → 全文改写词（不进 GRAPH）。"""

    def __init__(
        self,
        *,
        bern2: HasBern2Annotate | None = None,
        graphdb: GraphDbClient | None = None,
        max_surfaces: int = 8,
    ) -> None:
        self.bern2 = bern2
        self.graphdb = graphdb
        self.max_surfaces = max_surfaces

    def propose_terms(self, query: str) -> list[str]:
        external_ids: list[str] = []
        bios_concepts: list[str] = []
        mention = query.strip()
        curies = _curie_tokens(query)
        # 整段即 CURIE：只信公开 ID→BIOS，不让 BERN2 把 DEMO_* 局部片段洗进 surfaces
        pure_curie = bool(curies) and mention in curies

        for token in curies:
            if token.upper().startswith("BIOS:"):
                bios_concepts.append(token)
            else:
                external_ids.append(token)

        if self.bern2 is not None and not pure_curie:
            try:
                for m in self.bern2.annotate(query) or []:
                    for xid in getattr(m, "ids", None) or []:
                        s = str(xid)
                        if s.startswith("HMD:ENT:"):
                            continue
                        if s.upper().startswith("BIOS:"):
                            bios_concepts.append(s)
                        else:
                            external_ids.append(s)
                    mt = getattr(m, "mention", None)
                    if mt and ":" not in str(mt):
                        mention = str(mt)
            except Exception:
                pass

        surfaces, _cards = hydrate_search_surfaces(
            mention=None
            if pure_curie or ":" in mention
            else (mention if mention != query.strip() else None),
            external_ids=list(dict.fromkeys(external_ids)),
            bios_concepts=list(dict.fromkeys(bios_concepts)),
            client=self.graphdb,
            max_surfaces=self.max_surfaces,
        )
        return surfaces


def _curie_tokens(text: str) -> list[str]:
    import re

    return re.findall(r"\b[A-Za-z][A-Za-z0-9]+(?:_[A-Za-z0-9]+)?:[A-Za-z0-9_.:\-]+\b", text or "")
