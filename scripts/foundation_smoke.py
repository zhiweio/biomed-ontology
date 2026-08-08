#!/usr/bin/env python3
"""Foundation 栈探活：Milvus（必选）+ GraphDB + OpenMetadata。"""

from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    milvus = os.environ.get("HMD_MILVUS_URI", "http://localhost:19530")
    graphdb = os.environ.get("HMD_GRAPHDB_URL", "http://localhost:7200")
    om = os.environ.get("HMD_OPENMETADATA_URL", "http://localhost:8585")
    errors: list[str] = []

    # Milvus 必选
    try:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=milvus)
        _ = client.list_collections()
        print(f"OK milvus {milvus}")
    except Exception as exc:
        errors.append(f"milvus FAILED: {exc}")

    try:
        r = httpx.get(f"{graphdb.rstrip('/')}/rest/repositories", timeout=10.0)
        # 必须 2xx：容器端口在听但 Workbench 因 license 挂掉时常见 404，不能当 OK
        if 200 <= r.status_code < 300:
            print(f"OK graphdb {graphdb} status={r.status_code}")
        else:
            errors.append(
                f"graphdb HTTP {r.status_code} (需可读的 docker/secrets/graphdb.license 文件，"
                "勿让 Docker 建成空目录)"
            )
    except Exception as exc:
        errors.append(f"graphdb FAILED: {exc}")

    try:
        r = httpx.get(f"{om.rstrip('/')}/api/v1/system/version", timeout=15.0)
        if r.status_code >= 500:
            errors.append(f"openmetadata HTTP {r.status_code}")
        else:
            print(f"OK openmetadata {om} status={r.status_code}")
    except Exception as exc:
        # OM 启动慢：smoke 记警告但不阻断 milvus+graphdb 已绿时的本地开发
        print(f"WARN openmetadata: {exc}")

    if errors:
        print("SMOKE FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "提示：task milvus:up 或 task foundation:up；GraphDB 需 docker/secrets/graphdb.license",
            file=sys.stderr,
        )
        return 1
    print("foundation smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
