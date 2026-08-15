"""观测总线批处理：WAL 回放与湖维护。不替代 Connect Sink。"""

from __future__ import annotations

from typing import Any

from prefect import flow, task

__all__ = ["lake_maintain", "obs_wal_replay"]


@task
def task_obs_wal_replay(*, max_lines: int | None, dry_run: bool) -> dict[str, Any]:
    from biomed_ontology.lake.obs_events import replay_obs_wal

    return replay_obs_wal(max_lines=max_lines, dry_run=dry_run)


@flow(name="obs_wal_replay")
def obs_wal_replay(
    *,
    max_lines: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Jsonl WAL → 原 topic。不直写 Iceberg。"""
    return task_obs_wal_replay(max_lines=max_lines, dry_run=dry_run)


@task
def task_lake_maintain(
    *,
    older_than_days: int | None,
    compact: bool,
    dry_run: bool,
) -> dict[str, Any]:
    from biomed_ontology.lake.maintain import lake_maintain as _maintain

    return _maintain(older_than_days=older_than_days, compact=compact, dry_run=dry_run)


@flow(name="lake_maintain")
def lake_maintain(
    *,
    older_than_days: int | None = None,
    compact: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """pause Connect → expire snapshots → 可选 Trino optimize。"""
    return task_lake_maintain(
        older_than_days=older_than_days,
        compact=compact,
        dry_run=dry_run,
    )
