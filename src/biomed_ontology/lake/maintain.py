"""观测湖维护：先 pause Connect，再 expire snapshots，可选 Trino optimize。"""

from __future__ import annotations

import logging
from typing import Any

from biomed_ontology.config import Settings, settings
from biomed_ontology.lake.catalog import expire_lake_snapshots
from biomed_ontology.lake.connect_admin import paused_iceberg_sinks

__all__ = ["compact_obs_tables", "lake_maintain"]

_LOG = logging.getLogger(__name__)
_OBS_TABLES = ("obs_tool_io", "obs_decision", "obs_span", "er_observations")


def compact_obs_tables(cfg: Settings | None = None) -> dict[str, Any]:
    """Trino ``ALTER TABLE … EXECUTE optimize``。失败记 warning，不整次 Failed。"""
    cfg = cfg or settings
    tables: dict[str, str] = {}
    try:
        from trino.dbapi import connect
    except Exception as exc:
        _LOG.warning("trino import failed: %s", exc)
        return {"ok": False, "error": f"import: {exc}", "tables": tables}

    try:
        conn = connect(
            host=cfg.trino_host,
            port=cfg.trino_port,
            user="hmd",
            catalog=cfg.trino_catalog,
            schema=cfg.trino_schema,
        )
        cur = conn.cursor()
        for name in _OBS_TABLES:
            ident = f"{cfg.trino_catalog}.{cfg.trino_schema}.{name}"
            try:
                cur.execute(f"ALTER TABLE {ident} EXECUTE optimize")
                tables[name] = "ok"
            except Exception as exc:
                _LOG.warning("trino optimize %s: %s", name, exc)
                tables[name] = f"error: {exc}"
        conn.close()
    except Exception as exc:
        _LOG.warning("trino compact failed: %s", exc)
        return {"ok": False, "error": str(exc), "tables": tables}
    return {"ok": all(v == "ok" for v in tables.values()) if tables else False, "tables": tables}


def lake_maintain(
    *,
    older_than_days: int | None = None,
    compact: bool = True,
    dry_run: bool = False,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    if dry_run:
        return {
            "dry_run": True,
            "would_expire": True,
            "would_compact": compact,
            "older_than_days": older_than_days,
        }
    expired: dict[str, Any] = {}
    compact_result: dict[str, Any] | None = None
    with paused_iceberg_sinks(cfg):
        expired = expire_lake_snapshots(older_than_days=older_than_days)
        if compact:
            compact_result = compact_obs_tables(cfg)
    return {
        "dry_run": False,
        "expired": expired,
        "compact": compact_result,
    }
