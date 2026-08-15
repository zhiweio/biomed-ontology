"""Connect pause/resume 与 lake_maintain dry-run（不依赖真 Connect）。"""

from __future__ import annotations

from biomed_ontology.lake.connect_admin import connectors_healthy, paused_iceberg_sinks
from biomed_ontology.lake.maintain import lake_maintain


def test_connectors_healthy_requires_running_tasks() -> None:
    ok = {
        "hmd-obs-tool-io": {
            "status": {
                "connector": {"state": "RUNNING"},
                "tasks": [{"id": 0, "state": "RUNNING"}],
            }
        },
        "hmd-er-observations": {
            "status": {
                "connector": {"state": "RUNNING"},
                "tasks": [{"id": 0, "state": "RUNNING"}],
            }
        },
    }
    assert connectors_healthy(ok) is True
    assert connectors_healthy({"_error": "down"}) is False
    missing_task = {
        "hmd-obs-tool-io": {"status": {"connector": {"state": "RUNNING"}, "tasks": []}},
        "hmd-er-observations": ok["hmd-er-observations"],
    }
    assert connectors_healthy(missing_task) is False


def test_paused_iceberg_sinks_is_reentrant(monkeypatch) -> None:
    from biomed_ontology.lake import connect_admin

    connect_admin._pause_depth = 0
    calls: list[str] = []
    monkeypatch.setattr(connect_admin, "pause", lambda cfg=None: calls.append("pause") or {})
    monkeypatch.setattr(connect_admin, "resume", lambda cfg=None: calls.append("resume") or {})
    with paused_iceberg_sinks(), paused_iceberg_sinks():
        pass
    assert calls == ["pause", "resume"]


def test_lake_maintain_dry_run() -> None:
    result = lake_maintain(dry_run=True, compact=True)
    assert result["dry_run"] is True
    assert result["would_expire"] is True
    assert result["would_compact"] is True
