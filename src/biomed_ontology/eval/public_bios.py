"""公开 BIOS / 无 ENT 路径评测（lookup · resolve surfaces · lexical expand）。

数据：``data/gold/public_bios.yaml``。不进 Identity I1 / Bridge B1 硬门禁；
由 ``hmd eval --suite public_bios`` 与 CI 测试守住。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from biomed_ontology.eval.retrieval import load_gold

__all__ = ["PublicBiosEval", "eval_public_bios"]


@dataclass
class PublicBiosEval:
    total: int = 0
    passed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.passed == self.total

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0


def _surfaces_ok(got: list[str] | None, expect_any: list[str] | None) -> bool:
    if not expect_any:
        return True
    lowered = {str(s).casefold() for s in (got or [])}
    return any(
        str(e).casefold() in lowered or any(str(e).casefold() in s for s in lowered)
        for e in expect_any
    )


def _record(ev: PublicBiosEval, row: dict[str, Any]) -> None:
    ev.rows.append(row)
    ev.total += 1
    ev.passed += int(bool(row.get("ok")))
    if not row.get("ok"):
        ev.failures.append(row)


def eval_public_bios(
    surface: Any,
    *,
    gold: dict[str, Any] | None = None,
) -> PublicBiosEval:
    """对 ``public_bios.yaml`` 跑 lookup / resolve / normalize / lexical。"""
    gold = gold or load_gold("public_bios")
    foundation = surface.foundation
    tools = surface.tools
    kb = surface.kb
    ev = PublicBiosEval()

    for case in gold.get("curie_to_ent") or []:
        external_id = case.get("external_id")
        text = str(case.get("text") or external_id or "")
        expect_ent = case.get("expect_ent")
        expect_bios = case.get("expect_bios")
        if external_id:
            card = foundation.lookup_bios_concept(external_id=str(external_id))
            bios_ok = (not expect_bios) or (
                card.get("found") and card.get("bios_curie") == expect_bios
            )
            bridges = card.get("enterprise_bridges") or []
            bridge_ok = (not expect_ent) or (expect_ent in bridges)
            surfaces_ok = _surfaces_ok(card.get("search_surfaces"), case.get("expect_surfaces_any"))
        else:
            bios_ok = bridge_ok = surfaces_ok = True
        out = foundation.resolve_entity(text)
        got = next(
            (
                h.get("canonical_entity")
                for h in out.get("resolved") or []
                if h.get("canonical_entity")
            ),
            None,
        )
        ok = got == expect_ent and bios_ok and bridge_ok and surfaces_ok
        _record(
            ev,
            {
                "kind": "curie_to_ent",
                "text": text,
                "expect_ent": expect_ent,
                "got": got,
                "bios_ok": bios_ok,
                "bridge_ok": bridge_ok,
                "surfaces_ok": surfaces_ok,
                "ok": ok,
            },
        )

    for case in gold.get("lookup_no_ent") or []:
        kwargs: dict[str, Any] = {}
        if case.get("external_id"):
            kwargs["external_id"] = str(case["external_id"])
        if case.get("bios_curie"):
            kwargs["bios_curie"] = str(case["bios_curie"])
        card = foundation.lookup_bios_concept(**kwargs)
        expect_bios = case.get("expect_bios")
        expect_bridges = case.get("expect_ent_bridges")
        bios_ok = bool(card.get("found")) and (
            not expect_bios or card.get("bios_curie") == expect_bios
        )
        bridges = list(card.get("enterprise_bridges") or [])
        if expect_bridges is not None:
            bridge_ok = bridges == list(expect_bridges) or (not expect_bridges and not bridges)
        else:
            bridge_ok = True
        surfaces_ok = _surfaces_ok(card.get("search_surfaces"), case.get("expect_surfaces_any"))
        ok = bios_ok and bridge_ok and surfaces_ok
        _record(
            ev,
            {
                "kind": "lookup_no_ent",
                "query": kwargs,
                "expect_bios": expect_bios,
                "got_bios": card.get("bios_curie"),
                "bridges": bridges,
                "surfaces": card.get("search_surfaces"),
                "ok": ok,
            },
        )

    for case in gold.get("resolve_no_ent") or []:
        text = str(case["text"])
        out = foundation.resolve_entity(text)
        hits = out.get("resolved") or []
        got = next((h.get("canonical_entity") for h in hits if h.get("canonical_entity")), None)
        surfaces: list[str] = []
        for h in hits:
            surfaces.extend(h.get("search_surfaces") or [])
        expect_ent = case.get("expect_ent")
        ok = got == expect_ent and _surfaces_ok(surfaces, case.get("expect_surfaces_any"))
        _record(
            ev,
            {
                "kind": "resolve_no_ent",
                "text": text,
                "expect_ent": expect_ent,
                "got": got,
                "surfaces": surfaces,
                "ok": ok,
            },
        )

    for case in gold.get("normalize_abstain") or []:
        text = str(case["text"])
        expect = case.get("expect")
        norm = tools.normalize_entity(text)
        got = (norm.get("matched_concepts") or [{}])[0].get("concept_id")
        ok = not got if expect is None else got == expect
        _record(
            ev,
            {
                "kind": "normalize_abstain",
                "text": text,
                "expect": expect,
                "got": got,
                "ok": ok,
            },
        )

    searcher = getattr(tools, "searcher", None)
    for case in gold.get("lexical_rewrite") or []:
        query = str(case["query"])
        out = tools.search_documents(query, top_k=3)
        source = out.get("expansion_source") or "none"
        terms = [str(t) for t in (out.get("expansion_terms") or [])]
        source_ok = source == case.get("expect_expansion_source")
        terms_ok = _surfaces_ok(terms, case.get("expect_terms_any"))
        ok = source_ok and terms_ok
        _record(
            ev,
            {
                "kind": "lexical_rewrite",
                "query": query,
                "source": source,
                "terms": terms,
                "ok": ok,
            },
        )

    for case in gold.get("lexical_with_ent") or []:
        query = str(case["query"])
        out = tools.search_documents(query, top_k=3)
        source = out.get("expansion_source") or "none"
        expect_source = case.get("expect_expansion_source")
        expect_seed = case.get("expect_ent_seed")
        seed_ok = True
        if expect_seed and searcher is not None and hasattr(searcher, "_seed_concepts"):
            from biomed_ontology.observability import TraceContext

            seeds = searcher._seed_concepts(
                query, TraceContext(trace_id="eval-public", ontology_release_id="eval")
            )
            seed_ok = expect_seed in seeds
        source_ok = (not expect_source) or source == expect_source
        ok = source_ok and seed_ok
        _record(
            ev,
            {
                "kind": "lexical_with_ent",
                "query": query,
                "source": source,
                "seed_ok": seed_ok,
                "ok": ok,
            },
        )

    _ = kb
    return ev
