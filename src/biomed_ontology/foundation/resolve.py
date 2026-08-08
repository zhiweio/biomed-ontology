"""Biomedical Entity Resolution Service。

BERN2 = Recognition + Candidate Normalization
Resolver = Enterprise Identity Resolution（词典 / xref / 表面形）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from biomed_ontology.foundation.bern2 import Bern2Client, Bern2Mention
from biomed_ontology.foundation.ids import is_enterprise_id, normalize_alias_key
from biomed_ontology.foundation.models import EnterpriseEntity, ResolveHit

__all__ = ["EntityResolver", "ResolutionIndex", "load_zingg_matches"]

_REPO = Path(__file__).resolve().parents[3]
_DEFAULT_ZINGG = _REPO / "data" / "foundation" / "zingg_matches.jsonl"


def _bios_ids(xrefs: list[str]) -> list[str]:
    return [x for x in xrefs if x.upper().startswith("BIOS:")]


def load_zingg_matches(path: Path | None = None) -> dict[str, str]:
    import json

    p = path or _DEFAULT_ZINGG
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        mention = normalize_alias_key(str(row.get("mention", "")))
        eid = row.get("enterprise_id")
        if mention and eid and float(row.get("score") or 0) > 0:
            out[mention] = str(eid)
    return out


@dataclass
class ResolutionIndex:
    """Enterprise Entity ↔ aliases / external ids 倒排。"""

    by_id: dict[str, EnterpriseEntity] = field(default_factory=dict)
    by_alias: dict[str, list[str]] = field(default_factory=dict)
    by_external: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_entities(cls, entities: list[EnterpriseEntity]) -> ResolutionIndex:
        idx = cls()
        for e in entities:
            idx.by_id[e.enterprise_id] = e
            surfaces = {e.preferred_label_en, e.preferred_label_zh, *e.aliases}
            for s in surfaces:
                if not s:
                    continue
                idx.by_alias.setdefault(normalize_alias_key(s), []).append(e.enterprise_id)
            for xref in e.exact_match_xrefs + e.related_xrefs:
                idx.by_external.setdefault(xref, []).append(e.enterprise_id)
                # 兼容大小写前缀变体
                idx.by_external.setdefault(xref.lower(), []).append(e.enterprise_id)
        return idx


class EntityResolver:
    def __init__(
        self,
        index: ResolutionIndex,
        bern2: Bern2Client | None = None,
        zingg_matches: dict[str, str] | None = None,
    ) -> None:
        self.index = index
        self.bern2 = bern2 or Bern2Client()
        self.zingg_matches = zingg_matches if zingg_matches is not None else load_zingg_matches()

    def resolve_mention(
        self,
        mention: str,
        *,
        type_hint: str | None = None,
        external_ids: list[str] | None = None,
    ) -> ResolveHit:
        # 1) 已是企业 ID
        if is_enterprise_id(mention):
            ent = self.index.by_id.get(mention)
            return ResolveHit(
                canonical_entity=mention if ent else None,
                mention=mention,
                external_ids=list(ent.exact_match_xrefs) if ent else [],
                bios_concepts=_bios_ids(ent.exact_match_xrefs if ent else []),
                confidence=1.0 if ent else 0.0,
                resolution_method="enterprise_id",
                entity_kind=ent.entity_kind if ent else None,
            )

        # 2) 外部 ID 直接命中
        for xid in external_ids or []:
            hit = self._from_external(xid, mention)
            if hit.canonical_entity:
                return hit

        # 3) 企业词典 / 别名
        alias_ids = self.index.by_alias.get(normalize_alias_key(mention), [])
        if alias_ids:
            eid = alias_ids[0]
            ent = self.index.by_id[eid]
            alts = [
                {"enterprise_id": i, "preferred_label_en": self.index.by_id[i].preferred_label_en}
                for i in alias_ids[1:]
            ]
            return ResolveHit(
                canonical_entity=eid,
                mention=mention,
                external_ids=list(ent.exact_match_xrefs),
                bios_concepts=_bios_ids(ent.exact_match_xrefs),
                confidence=1.0 if len(alias_ids) == 1 else 0.7,
                resolution_method="dictionary",
                entity_kind=ent.entity_kind,
                alternatives=alts,
            )

        # 4) Zingg 预计算表
        zid = self.zingg_matches.get(normalize_alias_key(mention))
        if zid and zid in self.index.by_id:
            ent = self.index.by_id[zid]
            return ResolveHit(
                canonical_entity=zid,
                mention=mention,
                external_ids=list(ent.exact_match_xrefs),
                bios_concepts=_bios_ids(ent.exact_match_xrefs),
                confidence=0.9,
                resolution_method="zingg",
                entity_kind=ent.entity_kind,
            )

        # 5) 词典条目里的 external_ids（经 BERN2.scan 注入）
        dict_entry = self.bern2.dictionary.lookup(mention)
        if dict_entry:
            if dict_entry.get("enterprise_id"):
                return self.resolve_mention(dict_entry["enterprise_id"])
            for xid in dict_entry.get("external_ids", []):
                hit = self._from_external(str(xid), mention)
                if hit.canonical_entity:
                    hit.resolution_method = "dictionary"
                    hit.confidence = 1.0
                    return hit

        _ = type_hint  # 预留类型约束
        return ResolveHit(
            canonical_entity=None,
            mention=mention,
            external_ids=list(external_ids or []),
            confidence=0.0,
            resolution_method="unmapped",
        )

    def resolve_text(self, text: str) -> list[ResolveHit]:
        mentions = self.bern2.annotate(text)
        if not mentions:
            # 整句当一个 mention 再试一次（短查询）
            return [self.resolve_mention(text.strip())] if text.strip() else []
        out: list[ResolveHit] = []
        for m in mentions:
            out.append(self._resolve_bern2_mention(m))
        return out

    def _resolve_bern2_mention(self, m: Bern2Mention) -> ResolveHit:
        hit = self.resolve_mention(m.mention, type_hint=m.obj_type, external_ids=m.ids)
        if hit.canonical_entity:
            return hit
        # 仅外部 ID、尚未映射到企业实体
        bios = [i for i in m.ids if i.upper().startswith("BIOS:")]
        return ResolveHit(
            canonical_entity=None,
            mention=m.mention,
            external_ids=list(m.ids),
            bios_concepts=bios,
            confidence=m.prob,
            resolution_method="bern2_candidate",
        )

    def _from_external(self, xid: str, mention: str) -> ResolveHit:
        ids = self.index.by_external.get(xid) or self.index.by_external.get(xid.lower()) or []
        if not ids:
            return ResolveHit(
                canonical_entity=None,
                mention=mention,
                external_ids=[xid],
                bios_concepts=[xid] if xid.upper().startswith("BIOS:") else [],
                confidence=0.0,
                resolution_method="unmapped",
            )
        eid = ids[0]
        ent = self.index.by_id[eid]
        return ResolveHit(
            canonical_entity=eid,
            mention=mention,
            external_ids=list(ent.exact_match_xrefs),
            bios_concepts=_bios_ids(ent.exact_match_xrefs),
            confidence=1.0 if len(ids) == 1 else 0.75,
            resolution_method="xref",
            entity_kind=ent.entity_kind,
        )
