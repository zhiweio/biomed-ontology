"""Zingg loader / materialize / export unit tests（无 Spark / Iceberg）。"""

from __future__ import annotations

import json
from pathlib import Path

from biomed_ontology.foundation.resolve import EntityResolver, ResolutionIndex, load_zingg_matches
from biomed_ontology.foundation.zingg_io import (
    export_matches,
    link_stub_from_materialized,
    materialize,
)


def test_load_zingg_matches_min_score_and_ambiguity(tmp_path: Path) -> None:
    p = tmp_path / "zingg.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "mention": "savolitinb",
                        "enterprise_id": "HMD:ENT:DC:savolitinib",
                        "score": 0.91,
                    }
                ),
                json.dumps(
                    {"mention": "noise", "enterprise_id": "HMD:ENT:DC:savolitinib", "score": 0.2}
                ),
                json.dumps(
                    {
                        "mention": "ambig",
                        "enterprise_id": "HMD:ENT:DC:savolitinib",
                        "score": 0.9,
                    }
                ),
                json.dumps({"mention": "ambig", "enterprise_id": "HMD:ENT:TGT:MET", "score": 0.9}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = load_zingg_matches(p, min_score=0.8)
    assert "savolitinb" in out
    assert out["savolitinb"][0] == "HMD:ENT:DC:savolitinib"
    assert out["savolitinb"][1] == 0.91
    assert "noise" not in out
    assert "ambig" not in out


def test_resolve_zingg_confidence_from_score() -> None:
    from biomed_ontology.foundation.models import EnterpriseEntity

    ent = EnterpriseEntity(
        enterprise_id="HMD:ENT:DC:savolitinib",
        entity_kind="DrugCandidate",
        preferred_label_en="savolitinib",
    )
    idx = ResolutionIndex.from_entities([ent])
    resolver = EntityResolver(
        idx,
        zingg_matches={"savolitinb": ("HMD:ENT:DC:savolitinib", 0.91)},
    )
    hit = resolver.resolve_mention("savolitinb")
    assert hit.canonical_entity == "HMD:ENT:DC:savolitinib"
    assert hit.resolution_method == "zingg"
    assert abs(hit.confidence - 0.91) < 1e-6


def test_materialize_bootstrap_and_export(tmp_path: Path, monkeypatch) -> None:
    from biomed_ontology.foundation import zingg_io

    monkeypatch.setattr(zingg_io, "INPUT_DIR", tmp_path / "input")
    monkeypatch.setattr(zingg_io, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(zingg_io, "ZINGG_DIR", tmp_path)
    # use repo bootstrap pairs
    result = materialize(observations="bootstrap", out_dir=tmp_path / "input")
    assert result.enterprise_rows > 0
    assert result.observation_rows > 0
    assert result.enterprise_path.exists()
    assert result.observation_path.exists()

    raw = link_stub_from_materialized(input_dir=tmp_path / "input", raw_out=tmp_path / "raw.jsonl")
    assert raw.exists()
    out = tmp_path / "matches.jsonl"
    summary = export_matches(source=raw, out_path=out, min_score=0.8)
    assert summary["written"] >= 1
    loaded = load_zingg_matches(out, min_score=0.8)
    assert any(eid.startswith("HMD:ENT:") for eid, _ in loaded.values())
