"""OpenMetadata 客户端脚手架 — Enterprise Data Context Layer。

PoC 可用本地 assets.yaml；配置 OPENMETADATA_HOST 后走 REST。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from biomed_ontology.foundation.models import AssetHit

__all__ = ["OpenMetadataClient"]


@dataclass
class OpenMetadataClient:
    base_url: str | None = None
    token: str | None = None
    timeout: float = 30.0

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @classmethod
    def from_env(cls) -> OpenMetadataClient:
        import os

        return cls(
            base_url=os.environ.get("HMD_OPENMETADATA_URL", "http://localhost:8585"),
            token=os.environ.get("HMD_OPENMETADATA_TOKEN") or None,
        )

    def login(self, email: str, password: str) -> str:
        """Basic 登录；密码按 OM 约定做 base64。返回 accessToken 并写入 self.token。"""
        import base64

        if not self.base_url:
            raise RuntimeError("openmetadata base_url 未配置")
        pw_b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
        url = f"{self.base_url.rstrip('/')}/api/v1/users/login"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json={"email": email, "password": pw_b64})
            resp.raise_for_status()
            payload = resp.json()
        token = payload.get("accessToken")
        if not token:
            raise RuntimeError(f"OpenMetadata 登录未返回 accessToken: {payload}")
        self.token = str(token)
        return self.token

    def ping(self) -> dict[str, Any]:
        """公开探活：/api/v1/system/version（无需 JWT）。"""
        if not self.enabled:
            return {}
        url = f"{self.base_url.rstrip('/')}/api/v1/system/version"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
        return payload if isinstance(payload, dict) else {"raw": payload}

    def search_assets(
        self, *, query: str | None = None, entity_ids: list[str] | None = None
    ) -> list[AssetHit]:
        if not self.enabled:
            return []
        # 搜索需 JWT；联调无 token 时由调用方改走 ping()。
        params: dict[str, Any] = {"q": query or "*", "from": 0, "size": 20}
        url = f"{self.base_url.rstrip('/')}/api/v1/search/query"
        with httpx.Client(timeout=self.timeout, headers=self._headers()) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
        hits: list[AssetHit] = []
        for hit in payload.get("hits", {}).get("hits", []):
            src = hit.get("_source") or {}
            fqn = src.get("fullyQualifiedName") or src.get("name") or ""
            hits.append(
                AssetHit(
                    asset_fqn=str(fqn),
                    name=str(src.get("displayName") or src.get("name") or fqn),
                    entity_ids=list(entity_ids or []),
                    description=src.get("description"),
                    asset_type=str(src.get("entityType") or "table"),
                    url=f"{self.base_url.rstrip('/')}/table/{fqn}" if fqn else None,
                )
            )
        return hits
