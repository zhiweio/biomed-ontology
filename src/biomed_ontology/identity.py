"""统一身份入口：文献 Normalizer 与 Foundation EntityResolver 共用同一目录。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from biomed_ontology.normalize import Normalizer

__all__ = ["IdentityService"]


@dataclass
class IdentityService:
    """双面身份的单一句柄。词典只装配一次。

    - ``concept`` / ``normalize``：文献面目录级联（``Normalizer``）
    - ``resolve_text``：有 Resolver 时走企业级联；否则回落到目录 normalize
    """

    normalizer: Normalizer
    resolver: Any | None = None

    def concept(self, concept_id: str) -> Any:
        return self.normalizer.concept(concept_id)

    def normalize(self, text: str, **kwargs: Any) -> Any:
        if "ctx" not in kwargs:
            from biomed_ontology.observability import TraceContext

            kwargs["ctx"] = TraceContext(trace_id="identity", ontology_release_id="identity")
        return self.normalizer.normalize(text, **kwargs)

    def resolve_text(self, text: str, *, type_hint: str | None = None) -> Any:
        if self.resolver is not None:
            if type_hint:
                return [self.resolver.resolve_mention(text, type_hint=type_hint)]
            return self.resolver.resolve_text(text)
        return self.normalize(text, detect=True, min_confidence=0.6)

    @classmethod
    def from_catalog(cls, resolver: Any | None = None) -> IdentityService:
        from biomed_ontology.ingest.catalog import load_catalog_normalizer

        return cls(normalizer=load_catalog_normalizer(), resolver=resolver)

    @classmethod
    def from_world(cls, world: Any) -> IdentityService:
        return cls.from_catalog(resolver=getattr(world, "resolver", None))
