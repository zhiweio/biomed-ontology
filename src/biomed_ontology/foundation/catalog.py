"""OpenMetadata — Enterprise Data Context Layer。

sync 将 assets 写入 Glossary Terms；运行时按 entity_id / query 检索。
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from biomed_ontology.config import Settings, settings
from biomed_ontology.foundation.models import AssetHit

__all__ = ["HMD_ASSET_GLOSSARY", "OpenMetadataClient"]

HMD_ASSET_GLOSSARY = "HMDEnterpriseAssets"
_HMD_META_RE = re.compile(r"<!--hmd\s*(\{.*?\})\s*-->", re.DOTALL)


@dataclass
class OpenMetadataClient:
    base_url: str | None = None
    token: str | None = None
    timeout: float = 30.0
    email: str | None = None
    password: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    @classmethod
    def from_settings(cls, cfg: Settings | None = None) -> OpenMetadataClient:
        cfg = cfg or settings
        token = cfg.openmetadata_token.get_secret_value().strip()
        return cls(
            base_url=cfg.openmetadata_url or None,
            token=token or None,
            email=cfg.openmetadata_email or None,
            password=cfg.openmetadata_password.get_secret_value() or None,
        )

    @classmethod
    def from_env(cls) -> OpenMetadataClient:
        """兼容别名 → ``from_settings``（读 pydantic-settings）。"""
        return cls.from_settings()

    def ensure_auth(self, *, force: bool = False) -> None:
        if not self.enabled:
            return
        if self.token and not force:
            return
        if not self.email or not self.password:
            raise RuntimeError("OpenMetadata 需要 token 或 email/password")
        self.login(self.email, self.password)

    def login(self, email: str, password: str) -> str:
        """Basic 登录；密码按 OM 约定做 base64。"""
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
        if not self.enabled:
            return {}
        url = f"{self.base_url.rstrip('/')}/api/v1/system/version"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url)
            resp.raise_for_status()
            payload = resp.json()
        return payload if isinstance(payload, dict) else {"raw": payload}

    def require_admin(self) -> None:
        """当前配置账号必须是 OM Admin（唯一业务账号，不做 bootstrap 提权）。"""
        self.ensure_auth(force=True)
        assert self.base_url and self.token
        with httpx.Client(timeout=self.timeout) as client:
            me = client.get(
                f"{self.base_url.rstrip('/')}/api/v1/users/loggedInUser",
                headers=self._headers(),
            )
            if me.status_code == 401:
                self.ensure_auth(force=True)
                me = client.get(
                    f"{self.base_url.rstrip('/')}/api/v1/users/loggedInUser",
                    headers=self._headers(),
                )
            me.raise_for_status()
            user = me.json()
            if user.get("isAdmin"):
                return
        raise RuntimeError(
            f"OpenMetadata 账号 {self.email!r} 不是 Admin。"
            "请在 OM 中将该用户设为 Admin 后重试（Settings 只配置一个 OM 账号）。"
        )

    def ensure_glossary(self) -> dict[str, Any]:
        """幂等：glossary 已存在则返回，否则创建。"""
        self.require_admin()
        assert self.base_url and self.token
        name_url = (
            f"{self.base_url.rstrip('/')}/api/v1/glossaries/name/{HMD_ASSET_GLOSSARY}"
        )
        with httpx.Client(timeout=self.timeout) as client:
            got = client.get(name_url, headers=self._headers())
            if got.status_code == 401:
                self.ensure_auth(force=True)
                got = client.get(name_url, headers=self._headers())
            if got.status_code == 200:
                return got.json()
            if (
                got.status_code not in {404, 400}
                and got.status_code >= 400
                and "not found" not in got.text.lower()
            ):
                got.raise_for_status()
            body = {
                "name": HMD_ASSET_GLOSSARY,
                "displayName": "HMD Enterprise Assets",
                "description": "Foundation sync: ELN/LIMS/dataset assets (YAML→OM)",
            }
            created = client.put(
                f"{self.base_url.rstrip('/')}/api/v1/glossaries",
                headers=self._headers(),
                json=body,
            )
            if created.status_code == 401:
                self.ensure_auth(force=True)
                created = client.put(
                    f"{self.base_url.rstrip('/')}/api/v1/glossaries",
                    headers=self._headers(),
                    json=body,
                )
            # 并发创建冲突 → 再 GET（幂等）
            if created.status_code in {409, 400}:
                got2 = client.get(name_url, headers=self._headers())
                if got2.status_code == 200:
                    return got2.json()
            created.raise_for_status()
            return created.json()

    def upsert_assets(self, assets: list[AssetHit]) -> int:
        """幂等写入 Glossary Terms：内容相同则跳过；否则 PATCH / PUT。"""
        self.ensure_glossary()
        assert self.base_url
        n = 0
        with httpx.Client(timeout=self.timeout) as client:
            for a in assets:
                term_name = _term_name(a.asset_fqn)
                meta = {
                    "asset_fqn": a.asset_fqn,
                    "asset_type": a.asset_type,
                    "entity_ids": list(a.entity_ids),
                    "url": a.url,
                }
                desc = (a.description or a.name or "").strip()
                desc = f"{desc}\n\n<!--hmd\n{json.dumps(meta, ensure_ascii=False)}\n-->"
                synonyms = [a.asset_fqn, *list(a.entity_ids)][:32]
                display = a.name or a.asset_fqn
                body = {
                    "name": term_name,
                    "displayName": display,
                    "description": desc,
                    "glossary": HMD_ASSET_GLOSSARY,
                    "synonyms": synonyms,
                }
                fqn = f"{HMD_ASSET_GLOSSARY}.{term_name}"
                existing = client.get(
                    f"{self.base_url.rstrip('/')}/api/v1/glossaryTerms/name/{fqn}",
                    headers=self._headers(),
                )
                if existing.status_code == 401:
                    self.ensure_auth(force=True)
                    existing = client.get(
                        f"{self.base_url.rstrip('/')}/api/v1/glossaryTerms/name/{fqn}",
                        headers=self._headers(),
                    )
                if existing.status_code == 200:
                    payload = existing.json()
                    if _term_content_matches(
                        payload, description=desc, display_name=display, synonyms=synonyms
                    ):
                        n += 1
                        continue
                    self._patch_glossary_term(
                        client,
                        term_id=str(payload["id"]),
                        description=desc,
                        display_name=display,
                        synonyms=synonyms,
                    )
                else:
                    cr = client.put(
                        f"{self.base_url.rstrip('/')}/api/v1/glossaryTerms",
                        headers=self._headers(),
                        json=body,
                    )
                    if cr.status_code in {409, 400}:
                        existing2 = client.get(
                            f"{self.base_url.rstrip('/')}/api/v1/glossaryTerms/name/{fqn}",
                            headers=self._headers(),
                        )
                        if existing2.status_code != 200:
                            cr.raise_for_status()
                        payload = existing2.json()
                        if not _term_content_matches(
                            payload,
                            description=desc,
                            display_name=display,
                            synonyms=synonyms,
                        ):
                            self._patch_glossary_term(
                                client,
                                term_id=str(payload["id"]),
                                description=desc,
                                display_name=display,
                                synonyms=synonyms,
                            )
                    else:
                        cr.raise_for_status()
                n += 1
        return n

    def _patch_glossary_term(
        self,
        client: httpx.Client,
        *,
        term_id: str,
        description: str,
        display_name: str,
        synonyms: list[str],
    ) -> None:
        assert self.base_url
        patch = [
            {"op": "replace", "path": "/description", "value": description},
            {"op": "replace", "path": "/displayName", "value": display_name},
            {"op": "replace", "path": "/synonyms", "value": synonyms},
        ]
        headers = {
            **self._headers(),
            "Content-Type": "application/json-patch+json",
        }
        url = f"{self.base_url.rstrip('/')}/api/v1/glossaryTerms/{term_id}"
        pr = client.patch(url, headers=headers, json=patch)
        if pr.status_code == 401:
            self.ensure_auth(force=True)
            headers = {
                **self._headers(),
                "Content-Type": "application/json-patch+json",
            }
            pr = client.patch(url, headers=headers, json=patch)
        if pr.status_code == 403:
            raise RuntimeError(
                f"OpenMetadata PATCH glossaryTerm/{term_id} 被拒绝 (403)；"
                f"请确认 Settings 中的 OM 账号 {self.email!r} 为 Admin"
            )
        pr.raise_for_status()

    def search_assets(
        self, *, query: str | None = None, entity_ids: list[str] | None = None
    ) -> list[AssetHit]:
        self.ensure_auth(force=not bool(self.token))
        if not self.enabled:
            return []
        # ES 对含冒号的 CURIE 查询易 500；按 entity 过滤时用 glossary 名检索再本地筛
        if query and ":" in query and (entity_ids or query.startswith("HMD:")):
            q = HMD_ASSET_GLOSSARY
        else:
            q = query or HMD_ASSET_GLOSSARY
        params: dict[str, Any] = {
            "q": q,
            "from": 0,
            "size": 50,
            "index": "glossary_term_search_index",
        }
        url = f"{self.base_url.rstrip('/')}/api/v1/search/query"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, params=params, headers=self._headers())
            if resp.status_code == 401:
                self.ensure_auth(force=True)
                resp = client.get(url, params=params, headers=self._headers())
            if resp.status_code >= 500 and entity_ids:
                # 回退：拉 glossary 下全部 term
                return self._list_glossary_assets(entity_ids=entity_ids)
            resp.raise_for_status()
            payload = resp.json()

        hits: list[AssetHit] = []
        wanted = set(entity_ids or [])
        for hit in payload.get("hits", {}).get("hits", []):
            src = hit.get("_source") or {}
            gloss = src.get("glossary") or {}
            gname = gloss.get("name") if isinstance(gloss, dict) else None
            fqn = str(src.get("fullyQualifiedName") or "")
            if gname and gname != HMD_ASSET_GLOSSARY and not fqn.startswith(
                HMD_ASSET_GLOSSARY
            ):
                continue
            asset = _asset_from_term(src, base_url=self.base_url or "")
            if wanted and not (wanted & set(asset.entity_ids)):
                blob = json.dumps(src, ensure_ascii=False)
                if not any(e in blob for e in wanted):
                    continue
            hits.append(asset)

        if wanted:
            exact = [h for h in hits if wanted & set(h.entity_ids)]
            if exact:
                return exact
            # search 索引未及时刷新时，REST list 兜底
            listed = self._list_glossary_assets(entity_ids=list(wanted))
            if listed:
                return listed
        return hits

    def _list_glossary_assets(
        self, *, entity_ids: list[str] | None = None
    ) -> list[AssetHit]:
        """GET glossaryTerms 全量后按 entity_ids 过滤（避免 ES CURIE 500）。"""
        self.ensure_auth()
        assert self.base_url
        wanted = set(entity_ids or [])
        url = f"{self.base_url.rstrip('/')}/api/v1/glossaryTerms"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(
                url,
                headers=self._headers(),
                params={"limit": 100, "fields": "synonyms"},
            )
            if resp.status_code == 401:
                self.ensure_auth(force=True)
                resp = client.get(
                    url,
                    headers=self._headers(),
                    params={"limit": 100, "fields": "synonyms"},
                )
            resp.raise_for_status()
            payload = resp.json()
        hits: list[AssetHit] = []
        for src in payload.get("data") or []:
            fqn = str(src.get("fullyQualifiedName") or "")
            if not fqn.startswith(HMD_ASSET_GLOSSARY):
                continue
            asset = _asset_from_term(src, base_url=self.base_url or "")
            if wanted and not (wanted & set(asset.entity_ids)):
                continue
            hits.append(asset)
        return hits


def _term_content_matches(
    payload: dict[str, Any],
    *,
    description: str,
    display_name: str,
    synonyms: list[str],
) -> bool:
    """用于幂等：目标字段已一致则无需 PATCH。"""
    cur_syn = [str(s) for s in (payload.get("synonyms") or [])]
    return (
        str(payload.get("description") or "") == description
        and str(payload.get("displayName") or "") == display_name
        and cur_syn == list(synonyms)
    )


def _term_name(asset_fqn: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", asset_fqn).strip("_")
    return slug[:128] or "asset"


def _asset_from_term(src: dict[str, Any], *, base_url: str) -> AssetHit:
    desc = str(src.get("description") or "")
    meta: dict[str, Any] = {}
    m = _HMD_META_RE.search(desc)
    if m:
        try:
            meta = json.loads(m.group(1))
        except json.JSONDecodeError:
            meta = {}
    fqn = str(meta.get("asset_fqn") or src.get("fullyQualifiedName") or src.get("name") or "")
    entity_ids = list(meta.get("entity_ids") or [])
    if not entity_ids:
        for syn in src.get("synonyms") or []:
            if str(syn).startswith("HMD:ENT:"):
                entity_ids.append(str(syn))
    clean_desc = _HMD_META_RE.sub("", desc).strip()
    return AssetHit(
        asset_fqn=fqn,
        name=str(src.get("displayName") or src.get("name") or fqn),
        entity_ids=entity_ids,
        description=clean_desc or None,
        asset_type=str(meta.get("asset_type") or "glossary_term"),
        url=str(meta.get("url") or f"{base_url.rstrip('/')}/glossaryTerm/{fqn}"),
    )
