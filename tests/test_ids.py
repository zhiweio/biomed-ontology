"""ID 分配的三条不变量（D1）：单调递增、重建稳定、废弃不删。"""

from __future__ import annotations

from pathlib import Path

import pytest

from biomed_ontology._generated.hmd_concept import EntityTypeEnum
from biomed_ontology.ontology.ids import IdLedger, MintAction, SequenceLedger


@pytest.fixture
def ledger(tmp_path: Path) -> IdLedger:
    return IdLedger(tmp_path / "ids.json", release="0.1.0")


def test_minted_id_matches_schema_pattern(ledger: IdLedger):
    result = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:ABC123"})
    assert result.concept_id == "HMD:SUB:0000001"
    assert result.action is MintAction.CREATED


def test_segments_have_independent_counters(ledger: IdLedger):
    sub = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A"}).concept_id
    tgt = ledger.mint(EntityTypeEnum.TARGET, {"hgnc:1"}).concept_id
    assert sub == "HMD:SUB:0000001"
    assert tgt == "HMD:TGT:0000001"


def test_same_members_reuse_id(ledger: IdLedger):
    first = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A", "chembl:1"})
    second = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A", "chembl:1"})
    assert second.concept_id == first.concept_id
    assert second.action is MintAction.REUSED


def test_partial_member_overlap_reuses_and_extends(ledger: IdLedger):
    """外部源新增等价 ID 时必须挂到已有概念上，不能新建。"""
    first = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A"})
    second = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A", "drugbank:DB1"})
    assert second.concept_id == first.concept_id
    assert second.action is MintAction.EXTENDED
    assert ledger.lookup("drugbank:DB1") == first.concept_id


def test_rebuild_from_persisted_ledger_is_stable(tmp_path: Path):
    """离线可重放构建的前提：重建产出相同 ID。"""
    path = tmp_path / "ids.json"
    first = IdLedger(path, release="0.1.0")
    ids = [first.mint(EntityTypeEnum.SUBSTANCE, {f"unii:{i}"}).concept_id for i in range(5)]
    first.save()

    rebuilt = IdLedger(path, release="0.2.0")
    ids_again = [rebuilt.mint(EntityTypeEnum.SUBSTANCE, {f"unii:{i}"}).concept_id for i in range(5)]
    assert ids_again == ids


def test_merge_keeps_oldest_id_and_obsoletes_rest(ledger: IdLedger):
    a = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A"}).concept_id
    b = ledger.mint(EntityTypeEnum.SUBSTANCE, {"chembl:B"}).concept_id
    merged = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A", "chembl:B"})

    assert merged.action is MintAction.MERGED
    assert merged.concept_id == a, "存活者应是更早分配的 ID"
    assert merged.obsoleted == (b,)
    assert ledger.get(b).is_obsolete
    assert ledger.get(b).replaced_by == a


def test_obsoleted_id_resolves_to_successor(ledger: IdLedger):
    """历史报告里的旧 ID 必须仍能解析，否则溯源链会断。"""
    a = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A"}).concept_id
    b = ledger.mint(EntityTypeEnum.SUBSTANCE, {"chembl:B"}).concept_id
    ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A", "chembl:B"})
    assert ledger.resolve(b) == a


def test_obsoleted_id_is_never_reissued(ledger: IdLedger):
    a = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A"}).concept_id
    ledger.obsolete(a)
    fresh = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:Z"}).concept_id
    assert fresh != a
    assert ledger.get(a) is not None, "废弃的 ID 仍需留在账本中"


def test_replaced_by_cycle_is_detected(ledger: IdLedger):
    a = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:A"}).concept_id
    b = ledger.mint(EntityTypeEnum.SUBSTANCE, {"unii:B"}).concept_id
    ledger.get(a).is_obsolete = True
    ledger.get(a).replaced_by = b
    ledger.get(b).is_obsolete = True
    ledger.get(b).replaced_by = a
    with pytest.raises(ValueError, match="成环"):
        ledger.resolve(a)


def test_empty_clique_rejected(ledger: IdLedger):
    with pytest.raises(ValueError):
        ledger.mint(EntityTypeEnum.SUBSTANCE, set())


def test_sequence_ledger_is_stable_across_reload(tmp_path: Path):
    path = tmp_path / "alias.json"
    first = SequenceLedger(path, prefix="HMDA")
    assigned = [first.assign(f"k{i}") for i in range(3)]
    first.save()

    reloaded = SequenceLedger(path, prefix="HMDA")
    assert [reloaded.assign(f"k{i}") for i in range(3)] == assigned
    assert reloaded.assign("k99") == "HMDA:000000004", "新键应接着计数器继续分配"
