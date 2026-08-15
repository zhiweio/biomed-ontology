"""Prefect 生产编排：flow/task 只调 steps，不 mint ENT、不自动 validated。"""

from __future__ import annotations

__all__ = [
    "catalog_publish",
    "claim_promote",
    "data_loop_apply",
    "data_loop_enrich",
    "data_loop_mine",
    "document_batch_ingest",
    "document_ingest",
    "identity_match",
    "identity_match_dev",
    "literature_refresh",
    "literature_reindex_full",
    "ontology_eval",
    "ops_snapshot",
    "replay_quarantine",
    "slo_gate",
    "world_model_sync",
]


def __getattr__(name: str):
    if name in {"document_ingest", "document_batch_ingest"}:
        from biomed_ontology.lake import flows as _flows

        return getattr(_flows, name)
    if name in {"literature_refresh", "literature_reindex_full"}:
        from biomed_ontology.pipelines import literature as _m

        return getattr(_m, name)
    if name in {"world_model_sync", "catalog_publish"}:
        from biomed_ontology.pipelines import world_model as _m

        return getattr(_m, name)
    if name in {"identity_match", "identity_match_dev"}:
        from biomed_ontology.pipelines import identity_match as _m

        return getattr(_m, name)
    if name in {"data_loop_mine", "data_loop_enrich", "data_loop_apply"}:
        from biomed_ontology.pipelines import data_loop as _m

        return getattr(_m, name)
    if name == "ontology_eval":
        from biomed_ontology.pipelines.ontology_eval import ontology_eval

        return ontology_eval
    if name == "replay_quarantine":
        from biomed_ontology.pipelines.replay import replay_quarantine

        return replay_quarantine
    if name in {"ops_snapshot", "slo_gate"}:
        from biomed_ontology.pipelines import ops as _m

        return getattr(_m, name)
    if name == "claim_promote":
        from biomed_ontology.pipelines.claims import claim_promote

        return claim_promote
    raise AttributeError(name)
