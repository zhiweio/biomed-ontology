"""IdentityService 收口目录 Normalizer 与 Resolver。"""

from __future__ import annotations

from biomed_ontology.identity import IdentityService


def test_from_catalog_resolves_enterprise_label() -> None:
    ident = IdentityService.from_catalog()
    hit = ident.concept("HMD:ENT:DC:savolitinib")
    assert hit is not None
    result = ident.normalize("savolitinib", min_confidence=0.6)
    assert result.matched
    assert any(m.concept_id.startswith("HMD:ENT:") for m in result.matched)


def test_resolve_text_without_resolver_uses_catalog() -> None:
    ident = IdentityService.from_catalog()
    out = ident.resolve_text("savolitinib")
    assert getattr(out, "matched", None) or out
