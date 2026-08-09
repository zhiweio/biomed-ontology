"""S1 Identity：Foundation resolve_entity 金标。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from biomed_ontology.eval.retrieval import load_gold

__all__ = ["IdentityEval", "eval_identity"]


@dataclass
class IdentityEval:
    total: int
    correct: int
    gate_total: int
    gate_correct: int
    failures: list[dict[str, Any]] = field(default_factory=list)
    cases: list[dict[str, Any]] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def gate_accuracy(self) -> float:
        return self.gate_correct / self.gate_total if self.gate_total else 0.0

    @property
    def gate_ok(self) -> bool:
        return self.gate_total > 0 and self.gate_correct == self.gate_total


def eval_identity(
    foundation: Any,
    *,
    gold: dict[str, Any] | None = None,
) -> IdentityEval:
    """对 ``data/gold/resolve.yaml`` 跑 ``FoundationApi.resolve_entity``。"""
    gold = gold or load_gold("resolve")
    ev = IdentityEval(total=0, correct=0, gate_total=0, gate_correct=0)
    for case in gold.get("cases") or []:
        text = str(case["text"])
        expect = case.get("expect")
        gate = bool(case.get("gate"))
        out = foundation.resolve_entity(text)
        got = next(
            (
                h.get("canonical_entity")
                for h in out.get("resolved") or []
                if h.get("canonical_entity")
            ),
            None,
        )
        ok = got == expect
        row = {
            "text": text,
            "expect": expect,
            "got": got,
            "ok": ok,
            "gate": gate,
            "method": next(
                (h.get("resolution_method") for h in (out.get("resolved") or [])),
                None,
            ),
        }
        ev.cases.append(row)
        ev.total += 1
        ev.correct += int(ok)
        if gate:
            ev.gate_total += 1
            ev.gate_correct += int(ok)
        if not ok:
            ev.failures.append(row)
    return ev
