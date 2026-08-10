"""evolve-enrich / approve / apply / verify（合成 fixture，不依赖真实 stamp）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from biomed_ontology.foundation.evolve_apply import (
    apply_approved,
    approve_proposals,
    load_proposals,
    reject_proposals,
    verify_proposals,
)
from biomed_ontology.foundation.evolve_propose import (
    filter_candidates,
    load_candidates_files,
    load_filter_policy,
    load_gold_null_keys,
    run_enrich,
)
from biomed_ontology.foundation.ids import normalize_alias_key
from biomed_ontology.foundation.paths import DICTIONARY_PATH, REPO_ROOT
from biomed_ontology.foundation.world import load_world_model

FIXTURE = Path(__file__).parent / "fixtures" / "evolve" / "sample.candidates.json"


def test_policy_denies_patterns_and_gold_null(tmp_path: Path) -> None:
    policy = load_filter_policy()
    cands, _ = load_candidates_files([FIXTURE])
    gold = load_gold_null_keys(policy.gold_resolve_path)
    assert normalize_alias_key("CHEBI:DEMO_ASPIRIN") in gold
    keep, dismissed, _ = filter_candidates(cands, policy, gold_null_keys=gold)
    kept_keys = {c["mention_key"] for c in keep}
    dismissed_keys = {c["mention_key"] for c in dismissed}
    assert "er-alias-for-sclc" in kept_keys
    assert "tiny-cell lung ca" in kept_keys
    assert "e2e-obs-12345" in dismissed_keys
    assert "flush-check-999" in dismissed_keys
    assert "chebi:demo_aspirin" in dismissed_keys
    # query–mention drift
    assert "xyz" in dismissed_keys


def test_enrich_skip_tools_writes_proposals(tmp_path: Path) -> None:
    result = run_enrich(
        from_paths=[FIXTURE],
        out_dir=tmp_path,
        skip_tools=True,
    )
    assert result.proposals_path.exists()
    assert result.kgcl_path.exists()
    text = result.kgcl_path.read_text(encoding="utf-8")
    assert "create exact synonym" in text
    l1 = [p for p in result.proposals if p["risk_tier"] == "L1"]
    assert l1
    assert all(p["status"] == "pending_approval" for p in l1)
    assert any(p["mention"] == "er-alias-for-sclc" for p in l1)


def test_approve_apply_verify_dictionary_sandbox(tmp_path: Path) -> None:
    out = tmp_path / "props"
    out.mkdir()
    result = run_enrich(from_paths=[FIXTURE], out_dir=out, skip_tools=True)
    props_path = result.proposals_path

    path, approved = approve_proposals(
        props_path, tier="L1", min_confidence=0.8, by="test@hmd"
    )
    assert approved
    assert all(a["status"] == "approved" for a in approved)

    dict_path = tmp_path / "enterprise_dictionary.yaml"
    shutil.copy(DICTIONARY_PATH, dict_path)
    dry = apply_approved(path, dry_run=True, dictionary_path=dict_path)
    assert dry.written
    written = apply_approved(path, dry_run=False, dictionary_path=dict_path)
    assert any(w["action"] == "append_alias" for w in written.written)

    raw = yaml.safe_load(dict_path.read_text(encoding="utf-8"))
    aliases = []
    for e in raw["entries"]:
        if e.get("enterprise_id") == "HMD:ENT:IND:sclc":
            aliases.extend(e.get("aliases") or [])
    assert any(normalize_alias_key(a) == "er-alias-for-sclc" for a in aliases) or any(
        e.get("mention") == "er-alias-for-sclc" for e in raw["entries"]
    )

    # Reload world with sandboxed dictionary via monkeypatch of load path
    from biomed_ontology.foundation import bern2 as bern2_mod
    from biomed_ontology.foundation import world as world_mod

    original_load = bern2_mod.load_enterprise_dictionary

    def _load_sandbox(path: Path):
        if path == DICTIONARY_PATH or path.name == "enterprise_dictionary.yaml":
            return original_load(dict_path)
        return original_load(path)

    bern2_mod.load_enterprise_dictionary = _load_sandbox  # type: ignore[assignment]
    world_mod.load_enterprise_dictionary = _load_sandbox  # type: ignore[attr-defined]
    try:
        wm = load_world_model()
        # inject sandbox dict entries into resolver dictionary
        from biomed_ontology.foundation.bern2 import load_enterprise_dictionary

        wm.resolver.bern2.dictionary = load_enterprise_dictionary(dict_path)
        wm.resolver.bern2.dictionary.__post_init__()
        ver = verify_proposals(path, world=wm, statuses={"approved", "applied"})
        # At least the applied synonym mentions should pass
        assert ver.passed >= 1
        assert any(r["pass"] and r["mention"] == "er-alias-for-sclc" for r in ver.rows)
    finally:
        bern2_mod.load_enterprise_dictionary = original_load  # type: ignore[assignment]


def test_reject_marks_status(tmp_path: Path) -> None:
    result = run_enrich(from_paths=[FIXTURE], out_dir=tmp_path, skip_tools=True)
    l3 = [p for p in result.proposals if p["risk_tier"] == "L3"]
    pid = result.proposals[0]["proposal_id"] if not l3 else l3[0]["proposal_id"]
    _, rejected = reject_proposals(result.proposals_path, proposal_ids=[pid], reason="noise")
    assert rejected[0]["status"] == "rejected"
    _, rows = load_proposals(result.proposals_path)
    assert any(r["proposal_id"] == pid and r["status"] == "rejected" for r in rows)


def test_filter_is_config_driven_not_hardcoded(tmp_path: Path) -> None:
    """Deny patterns come from YAML; empty deny keeps e2e-looking strings."""
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        yaml.safe_dump(
            {
                "mention": {"min_chars": 2, "max_chars": 80, "deny_patterns": []},
                "query_mention": {"min_overlap": 0.0, "accept_substring": True},
                "occurrences": {"min": 1},
                "gold": {"dismiss_expect_null": False},
            }
        ),
        encoding="utf-8",
    )
    policy = load_filter_policy(policy_path)
    cands, _ = load_candidates_files([FIXTURE])
    keep, dismissed, _ = filter_candidates(cands, policy, gold_null_keys=set())
    kept = {c["mention_key"] for c in keep}
    assert "e2e-obs-12345" in kept
    _ = dismissed


def test_repo_policy_exists() -> None:
    assert (REPO_ROOT / "ontology" / "policies" / "evolve_filter.yaml").exists()
