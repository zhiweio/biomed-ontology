"""Biomedical Entity Resolution Service。

BERN2 = Recognition + Candidate Normalization
Resolver = Enterprise Identity Resolution（词典 / xref / 表面形）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from biomed_ontology.foundation.bern2 import Bern2Client, Bern2Mention
from biomed_ontology.foundation.ids import is_enterprise_id, is_external_id, normalize_alias_key
from biomed_ontology.foundation.models import EnterpriseEntity, ResolveHit
from biomed_ontology.foundation.paths import ZINGG_MATCHES_PATH

__all__ = ["EntityResolver", "ResolutionIndex", "load_zingg_matches"]

_DEFAULT_ZINGG = ZINGG_MATCHES_PATH


def _bios_ids(xrefs: list[str]) -> list[str]:
    return [x for x in xrefs if x.upper().startswith("BIOS:")]


def load_zingg_matches(
    path: Path | None = None,
    *,
    min_score: float | None = None,
) -> dict[str, tuple[str, float]]:
    """加载 mention → (enterprise_id, score)。

    - 默认 ``min_score`` 来自 ``settings.zingg_min_score``（0.8）
    - 同 mention 多行取最高分；并列且 ENT 不同则丢弃（歧义）
    """
    import json

    from biomed_ontology.config import settings

    threshold = settings.zingg_min_score if min_score is None else float(min_score)
    p = path or _DEFAULT_ZINGG
    best: dict[str, tuple[str, float]] = {}
    ambiguous: set[str] = set()
    if not p.exists():
        return {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        mention = normalize_alias_key(str(row.get("mention", "")))
        eid = row.get("enterprise_id")
        score = float(row.get("score") or 0)
        if not mention or not eid or score < threshold:
            continue
        eid_s = str(eid)
        prev = best.get(mention)
        if prev is None:
            best[mention] = (eid_s, score)
            continue
        prev_eid, prev_score = prev
        if score > prev_score:
            best[mention] = (eid_s, score)
            ambiguous.discard(mention)
        elif score == prev_score and prev_eid != eid_s:
            ambiguous.add(mention)
    for m in ambiguous:
        best.pop(m, None)
    return best


@dataclass
class ResolutionIndex:
    """Enterprise Entity ↔ aliases / external ids 倒排。"""

    by_id: dict[str, EnterpriseEntity] = field(default_factory=dict)
    by_alias: dict[str, list[str]] = field(default_factory=dict)
    by_external: dict[str, list[str]] = field(default_factory=dict)
    by_exact_external: dict[str, list[str]] = field(default_factory=dict)

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
            for xref in e.exact_match_xrefs:
                idx.by_external.setdefault(xref, []).append(e.enterprise_id)
                idx.by_external.setdefault(xref.lower(), []).append(e.enterprise_id)
                idx.by_exact_external.setdefault(xref, []).append(e.enterprise_id)
                idx.by_exact_external.setdefault(xref.lower(), []).append(e.enterprise_id)
            for xref in e.related_xrefs:
                idx.by_external.setdefault(xref, []).append(e.enterprise_id)
                idx.by_external.setdefault(xref.lower(), []).append(e.enterprise_id)
        return idx

    def lookup_exact_external(self, xid: str) -> list[str]:
        """仅 exact_match_xrefs（轴 C / PublicNenAssist）。"""
        return list(
            dict.fromkeys(
                self.by_exact_external.get(xid, []) + self.by_exact_external.get(xid.lower(), [])
            )
        )


class EntityResolver:
    def __init__(
        self,
        index: ResolutionIndex,
        bern2: Bern2Client | None = None,
        zingg_matches: dict[str, tuple[str, float]] | dict[str, str] | None = None,
    ) -> None:
        self.index = index
        self.bern2 = bern2 or Bern2Client()
        raw = zingg_matches if zingg_matches is not None else load_zingg_matches()
        # 兼容旧测试注入的 mention→eid 扁平表
        self.zingg_matches: dict[str, tuple[str, float]] = {
            k: (v if isinstance(v, tuple) else (str(v), 0.9)) for k, v in raw.items()
        }

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

        # 2) 外部 ID 直接命中（含「mention 本身就是 CURIE」）
        xids = list(external_ids or [])
        if ":" in mention and not is_enterprise_id(mention) and mention not in xids:
            xids.insert(0, mention)
        for xid in xids:
            if not xid or str(xid).upper() in {"CUI-LESS", "CUILESS"}:
                continue
            hit = self._from_external(str(xid), mention)
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
        zentry = self.zingg_matches.get(normalize_alias_key(mention))
        if zentry:
            zid, zscore = zentry
            if zid in self.index.by_id:
                ent = self.index.by_id[zid]
                return ResolveHit(
                    canonical_entity=zid,
                    mention=mention,
                    external_ids=list(ent.exact_match_xrefs),
                    bios_concepts=_bios_ids(ent.exact_match_xrefs),
                    confidence=max(0.5, min(0.95, float(zscore))),
                    resolution_method="zingg",
                    entity_kind=ent.entity_kind,
                )

        # 5) 词典条目里的 enterprise_id / external_ids（经 BERN2.scan 注入）
        dict_entry = self.bern2.dictionary.lookup(mention)
        if dict_entry:
            if dict_entry.get("enterprise_id"):
                eid = str(dict_entry["enterprise_id"])
                if is_enterprise_id(eid):
                    ent = self.index.by_id.get(eid)
                    if ent:
                        return ResolveHit(
                            canonical_entity=eid,
                            mention=mention,
                            external_ids=list(ent.exact_match_xrefs),
                            bios_concepts=_bios_ids(ent.exact_match_xrefs),
                            confidence=1.0,
                            resolution_method="dictionary",
                            entity_kind=ent.entity_kind,
                        )
                    # 策展词典可指向 catalog 派生 ENT（尚未写入 entities YAML）
                    return ResolveHit(
                        canonical_entity=eid,
                        mention=mention,
                        external_ids=[
                            x
                            for x in list(dict_entry.get("external_ids") or [])
                            if x and not str(x).startswith("HMD:ENT:")
                        ],
                        bios_concepts=[],
                        confidence=1.0,
                        resolution_method="dictionary",
                        entity_kind=str(dict_entry.get("type") or "") or None,
                    )
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
        stripped = (text or "").strip()
        # 整段即公开 CURIE / 外部 ID：先走词典 xref，避免 BERN2 把 DEMO_* 等
        # 局部片段误标成无关基因（无 ENT 公开路径依赖此短路）。
        if stripped and is_external_id(stripped):
            return [self.resolve_mention(stripped)]
        # 整串已是词典 / zingg / xref 命中时优先于 BERN2 切词，避免子串误吸
        if stripped:
            exact = self.resolve_mention(stripped)
            if exact.canonical_entity and exact.resolution_method in {
                "dictionary",
                "zingg",
                "enterprise_id",
                "xref",
            }:
                return [exact]
        mentions = self.bern2.annotate(text)
        if not mentions:
            # 整句当一个 mention 再试一次（短查询）
            return [self.resolve_mention(stripped)] if stripped else []
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
