"""BIOS / 公开 ID → 表面词与概念卡（只读，不 mint ENT）。

供 ``resolve_entity.search_surfaces``、PublicLexicalExpand、``lookup_bios_concept`` 共用。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from biomed_ontology.foundation.bios import BiosConcept, load_bios_subset_jsonl
from biomed_ontology.foundation.graphdb import GraphDbClient
from biomed_ontology.foundation.graphs import BIOS_NS, GRAPH_BIOMEDICAL, HMD_NS
from biomed_ontology.foundation.models import EnterpriseEntity
from biomed_ontology.foundation.paths import REPO_ROOT

__all__ = [
    "DEFAULT_BIOS_INDEX",
    "BiosCard",
    "bios_curie_from_iri",
    "enterprise_bridges_for_ids",
    "fetch_bios_card",
    "fetch_bios_cards",
    "hydrate_search_surfaces",
    "lookup_bios_curies",
    "surfaces_from_card",
]

DEFAULT_BIOS_INDEX = REPO_ROOT / "data" / "cache" / "bios_ext_index.sqlite"
DEFAULT_SUBSET = REPO_ROOT / "data" / "foundation" / "bios_subset.jsonl"


@dataclass
class BiosCard:
    bios_curie: str
    pref_label: str | None = None
    alt_labels: list[str] = field(default_factory=list)
    external_ids: list[str] = field(default_factory=list)
    semtypes: list[str] = field(default_factory=list)
    iri: str | None = None
    backend: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bios_curie": self.bios_curie,
            "pref_label": self.pref_label,
            "alt_labels": list(self.alt_labels),
            "external_ids": list(self.external_ids),
            "semtypes": list(self.semtypes),
            "iri": self.iri,
            "graph": GRAPH_BIOMEDICAL,
            "backend": self.backend,
        }


def bios_curie_from_iri(iri_or_curie: str) -> str | None:
    s = (iri_or_curie or "").strip()
    if not s:
        return None
    if s.upper().startswith("BIOS:"):
        return f"BIOS:{s.split(':', 1)[1]}"
    if s.startswith(BIOS_NS):
        return f"BIOS:{s[len(BIOS_NS) :]}"
    return None


def _index_path(path: Path | None = None) -> Path:
    return path or DEFAULT_BIOS_INDEX


def lookup_bios_curies(
    *,
    external_id: str | None = None,
    term: str | None = None,
    index_path: Path | None = None,
    limit: int = 20,
) -> list[str]:
    """sqlite ext/term → BIOS CURIE（去重保序）。"""
    p = _index_path(index_path)
    out: list[str] = []
    seen: set[str] = set()

    def _add(curie: str) -> None:
        c = bios_curie_from_iri(curie) or curie
        if c and c not in seen:
            seen.add(c)
            out.append(c)

    if p.is_file():
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            if external_id:
                for key in (external_id, external_id.lower()):
                    rows = conn.execute(
                        "SELECT bios_curie FROM ext WHERE external_id = ? LIMIT ?",
                        (key, limit),
                    ).fetchall()
                    for (c,) in rows:
                        _add(str(c))
            if term and len(out) < limit:
                key = term.strip().lower()
                if key:
                    rows = conn.execute(
                        "SELECT bios_curie FROM term WHERE term = ? LIMIT ?",
                        (key, limit),
                    ).fetchall()
                    for (c,) in rows:
                        _add(str(c))
        finally:
            conn.close()
        if out:
            return out[:limit]

    # fall back: subset jsonl（CI / 未灌索引）
    if DEFAULT_SUBSET.is_file():
        for c in load_bios_subset_jsonl(DEFAULT_SUBSET):
            if (external_id and external_id in c.external_ids) or (
                external_id and external_id.lower() in {x.lower() for x in c.external_ids}
            ):
                _add(c.uri_curie)
            elif term:
                surfaces = {*(c.terms or []), c.preferred_term or ""}
                if term.strip().lower() in {s.strip().lower() for s in surfaces if s}:
                    _add(c.uri_curie)
    return out[:limit]


def _subset_concept(bios_curie: str) -> BiosConcept | None:
    if not DEFAULT_SUBSET.is_file():
        return None
    want = bios_curie_from_iri(bios_curie) or bios_curie
    for c in load_bios_subset_jsonl(DEFAULT_SUBSET):
        if c.uri_curie == want:
            return c
    return None


def fetch_bios_card(
    client: GraphDbClient | None,
    bios_curie: str,
    *,
    include_alts: bool = True,
) -> BiosCard | None:
    """从 biomedical 图取卡；图不可用或空则回落 subset。"""
    curie = bios_curie_from_iri(bios_curie)
    if not curie:
        return None
    bios_id = curie.split(":", 1)[1]
    iri = f"{BIOS_NS}{bios_id}"

    if client is not None:
        try:
            q = f"""
            PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
            PREFIX hmd: <{HMD_NS}>
            SELECT ?lab ?alt ?biosId WHERE {{
              GRAPH <{GRAPH_BIOMEDICAL}> {{
                <{iri}> a skos:Concept .
                OPTIONAL {{ <{iri}> skos:prefLabel ?lab }}
                OPTIONAL {{ <{iri}> skos:altLabel ?alt }}
                OPTIONAL {{ <{iri}> hmd:biosId ?biosId }}
              }}
            }}
            """
            rows = client.query(q)
            if rows:
                pref = None
                alts: list[str] = []
                for row in rows:
                    if row.get("lab") and not pref:
                        pref = str(row["lab"])
                    alt = row.get("alt")
                    if include_alts and alt and str(alt) not in alts and str(alt) != pref:
                        alts.append(str(alt))
                card = BiosCard(
                    bios_curie=curie,
                    pref_label=pref,
                    alt_labels=alts,
                    iri=iri,
                    backend="graphdb_biomedical",
                )
                # 补 external_ids / semtypes 自 subset（图未存 xref）
                sub = _subset_concept(curie)
                if sub:
                    card.external_ids = list(sub.external_ids)
                    card.semtypes = list(sub.semtypes)
                    if not card.pref_label:
                        card.pref_label = sub.preferred_term
                    if include_alts and not card.alt_labels:
                        card.alt_labels = [t for t in sub.terms if t and t != card.pref_label][:8]
                return card
        except Exception:
            pass

    sub = _subset_concept(curie)
    if sub is None:
        return None
    return BiosCard(
        bios_curie=curie,
        pref_label=sub.preferred_term,
        alt_labels=[t for t in sub.terms if t and t != sub.preferred_term][:8],
        external_ids=list(sub.external_ids),
        semtypes=list(sub.semtypes),
        iri=iri,
        backend="bios_subset",
    )


def fetch_bios_cards(
    client: GraphDbClient | None,
    curies: list[str],
    *,
    include_alts: bool = True,
) -> list[BiosCard]:
    out: list[BiosCard] = []
    seen: set[str] = set()
    for c in curies:
        card = fetch_bios_card(client, c, include_alts=include_alts)
        if card and card.bios_curie not in seen:
            seen.add(card.bios_curie)
            out.append(card)
    return out


def surfaces_from_card(card: BiosCard, *, max_surfaces: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in [card.pref_label, *card.alt_labels]:
        if not s:
            continue
        key = s.strip().casefold()
        if not key or key in seen:
            continue
        # 压过短噪声
        if len(key) < 2:
            continue
        seen.add(key)
        out.append(s.strip())
        if len(out) >= max_surfaces:
            break
    return out


def hydrate_search_surfaces(
    *,
    mention: str | None = None,
    external_ids: list[str] | None = None,
    bios_concepts: list[str] | None = None,
    client: GraphDbClient | None = None,
    max_surfaces: int = 8,
    index_path: Path | None = None,
) -> tuple[list[str], list[BiosCard]]:
    """公开 ID / BIOS CURIE / 自由文本 mention → 检索用表面词 + 卡片。

    无 ENT 路径：mention 可走 term→BIOS（不 mint ``HMD:ENT:*``）。
    """
    from biomed_ontology.foundation.ids import is_external_id

    curies: list[str] = []
    for b in bios_concepts or []:
        c = bios_curie_from_iri(b)
        if c:
            curies.append(c)
    for xid in external_ids or []:
        if str(xid).upper().startswith("BIOS:"):
            c = bios_curie_from_iri(str(xid))
            if c:
                curies.append(c)
            continue
        curies.extend(lookup_bios_curies(external_id=str(xid), index_path=index_path, limit=5))

    m = (mention or "").strip()
    # 自由文本：term → BIOS（CURIE 已由 external_ids / 短路路径覆盖）
    if m and not is_external_id(m) and ":" not in m:
        curies.extend(lookup_bios_curies(term=m, index_path=index_path, limit=5))
        # sqlite 索引未覆盖 / 空结果时，再扫 PoC subset（含中文别名）
        if DEFAULT_SUBSET.is_file():
            key = m.casefold()
            for c in load_bios_subset_jsonl(DEFAULT_SUBSET):
                surfaces_c = {*(c.terms or []), c.preferred_term or ""}
                if key in {s.strip().casefold() for s in surfaces_c if s}:
                    curies.append(c.uri_curie)

    # 去重保序
    uniq: list[str] = []
    seen_c: set[str] = set()
    for c in curies:
        if c not in seen_c:
            seen_c.add(c)
            uniq.append(c)

    cards = fetch_bios_cards(client, uniq, include_alts=True)
    surfaces: list[str] = []
    seen_s: set[str] = set()
    # 跳过纯 CURIE 作为表面词
    if m and (":" not in m or " " in m):
        seen_s.add(m.casefold())
        surfaces.append(m)
    for card in cards:
        for s in surfaces_from_card(card, max_surfaces=max_surfaces):
            k = s.casefold()
            if k not in seen_s:
                seen_s.add(k)
                surfaces.append(s)
            if len(surfaces) >= max_surfaces:
                break
        if len(surfaces) >= max_surfaces:
            break
    return surfaces[:max_surfaces], cards


def enterprise_bridges_for_ids(
    entities: dict[str, EnterpriseEntity] | list[EnterpriseEntity],
    *,
    bios_curie: str | None = None,
    external_ids: list[str] | None = None,
) -> list[dict[str, str]]:
    """反向：企业 exact_match_xrefs 命中公开 ID / BIOS → bridge。"""
    pool = set()
    if bios_curie:
        c = bios_curie_from_iri(bios_curie)
        if c:
            pool.add(c)
            pool.add(c.lower())
    for x in external_ids or []:
        if x:
            pool.add(x)
            pool.add(x.lower())
    if not pool:
        return []
    ents = entities.values() if isinstance(entities, dict) else entities
    out: list[dict[str, str]] = []
    for e in ents:
        for xref in e.exact_match_xrefs:
            if xref in pool or xref.lower() in pool:
                out.append(
                    {
                        "kind": "enterprise_bridge",
                        "enterprise_id": e.enterprise_id,
                        "predicate": "skos:exactMatch",
                        "external_id": xref,
                    }
                )
                break
    return out


def load_subset_as_jsonl_hint() -> Path:
    return DEFAULT_SUBSET
