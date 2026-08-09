"""S3 Bridge：KB normalize ∧ WM resolve；resolve→search；entitlement restore。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from biomed_ontology.eval.retrieval import load_gold

__all__ = ["BridgeEval", "eval_bridge"]


@dataclass
class BridgeEval:
    alias_total: int = 0
    alias_passed: int = 0
    literature_total: int = 0
    literature_passed: int = 0
    entitlement_ok: bool | None = None
    failures: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def alias_ok(self) -> bool:
        return self.alias_total > 0 and self.alias_passed == self.alias_total

    @property
    def literature_ok(self) -> bool:
        return self.literature_total > 0 and self.literature_passed == self.literature_total

    @property
    def ok(self) -> bool:
        ent = True if self.entitlement_ok is None else self.entitlement_ok
        return self.alias_ok and self.literature_ok and ent


def eval_bridge(
    surface: Any,
    *,
    entitlements: frozenset[str] | None = None,
    gold: dict[str, Any] | None = None,
) -> BridgeEval:
    """跨面桥接：B1 同 ENT、B2 resolve→search、许可还原纪律。"""
    from biomed_ontology._generated.hmd_concept import LicenseTierEnum

    gold = gold or load_gold("bridge")
    foundation = surface.foundation
    tools = surface.tools
    kb = surface.kb
    ev = BridgeEval()

    for case in gold.get("alias_bridge") or []:
        mention = str(case["mention"])
        expect = case.get("expect")
        types = list(case.get("entity_types") or [])
        norm = tools.normalize_entity(mention, entity_types=types or None)
        kb_id = (norm.get("matched_concepts") or [{}])[0].get("concept_id")
        out = foundation.resolve_entity(mention)
        wm_id = next(
            (
                h.get("canonical_entity")
                for h in out.get("resolved") or []
                if h.get("canonical_entity")
            ),
            None,
        )
        ok = bool(kb_id) and wm_id == expect and kb_id == expect
        row = {
            "kind": "alias_bridge",
            "mention": mention,
            "expect": expect,
            "kb_id": kb_id,
            "wm_id": wm_id,
            "ok": ok,
        }
        ev.rows.append(row)
        ev.alias_total += 1
        ev.alias_passed += int(ok)
        if not ok:
            ev.failures.append(row)

    for case in gold.get("literature_bridge") or []:
        mention = str(case["mention"])
        expect = case.get("expect")
        top_k = int(case.get("top_k") or 5)
        out = foundation.resolve_entity(mention)
        wm_id = next(
            (
                h.get("canonical_entity")
                for h in out.get("resolved") or []
                if h.get("canonical_entity")
            ),
            None,
        )
        hits = tools.search_documents(mention, top_k=top_k).get("results") or []
        ok = wm_id == expect and len(hits) >= 1
        row = {
            "kind": "literature_bridge",
            "mention": mention,
            "expect": expect,
            "wm_id": wm_id,
            "hit_count": len(hits),
            "ok": ok,
        }
        ev.rows.append(row)
        ev.literature_total += 1
        ev.literature_passed += int(ok)
        if not ok:
            ev.failures.append(row)

    ent_cfg = gold.get("entitlement") or {}
    licensed_id = str(ent_cfg.get("licensed_id") or "MOCK_LICENSED")
    # 优先挑「源 = licensed_id」且正文非空的切片；Iceberg 与内存库 ID 漂移时
    # 单拿 first non-TIER_0 容易落到空正文 / 缺 source_id，误报 allowed=False。
    candidates = []
    for c in kb.chunks:
        doc = kb.document(c.doc_id)
        if doc is None or doc.license_tier is LicenseTierEnum.TIER_0:
            continue
        if not (c.text or "").strip():
            continue
        candidates.append(c)
    candidates.sort(
        key=lambda c: (
            0
            if (d := kb.document(c.doc_id)) is not None and d.source_id == licensed_id
            else 1,
            c.chunk_id,
        )
    )
    if not candidates:
        ev.entitlement_ok = None
        ev.rows.append(
            {
                "kind": "entitlement",
                "ok": True,
                "note": "no non-TIER_0 chunk in corpus; skipped",
            }
        )
    else:
        ents = frozenset({licensed_id}) | (entitlements or frozenset())
        row: dict[str, Any] | None = None
        for restricted in candidates[:12]:
            denied = tools.restore_context(restricted.chunk_id)
            allowed = tools.restore_context(restricted.chunk_id, entitlements=ents)
            ok = (not denied.get("full_text")) and bool(allowed.get("full_text"))
            row = {
                "kind": "entitlement",
                "chunk_id": restricted.chunk_id,
                "doc_id": restricted.doc_id,
                "denied_empty": not bool(denied.get("full_text")),
                "allowed_nonempty": bool(allowed.get("full_text")),
                "ok": ok,
            }
            if ok:
                break
        assert row is not None
        ev.rows.append(row)
        ev.entitlement_ok = bool(row["ok"])
        if not row["ok"]:
            ev.failures.append(row)

    return ev
