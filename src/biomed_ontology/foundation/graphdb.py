"""GraphDB / RDF4J SPARQL 客户端。"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from biomed_ontology.config import Settings, settings
from biomed_ontology.foundation.graphs import GRAPH_BIOMEDICAL, NAMED_GRAPHS

__all__ = ["GraphDbClient", "ensure_repository"]


@dataclass
class GraphDbClient:
    base_url: str = "http://localhost:7200"
    repository: str = "hmd"
    timeout: float = 120.0

    @classmethod
    def from_settings(cls, cfg: Settings | None = None) -> GraphDbClient:
        cfg = cfg or settings
        return cls(base_url=cfg.graphdb_url, repository=cfg.graphdb_repository)

    @property
    def sparql_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/repositories/{self.repository}"

    @property
    def statements_url(self) -> str:
        return f"{self.sparql_url}/statements"

    def health(self) -> bool:
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{self.base_url.rstrip('/')}/rest/repositories")
                # 404 常见于 license 无效/不可读导致 Workbench 未真正起来
                return 200 <= r.status_code < 300
        except httpx.HTTPError:
            return False

    def query(self, sparql: str) -> list[dict[str, str]]:
        headers = {"Accept": "application/sparql-results+json"}
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(
                self.sparql_url,
                data={"query": sparql},
                headers=headers,
            )
            r.raise_for_status()
            payload = r.json()
        bindings = payload.get("results", {}).get("bindings", [])
        out: list[dict[str, str]] = []
        for row in bindings:
            out.append({k: v.get("value", "") for k, v in row.items()})
        return out

    def update(self, sparql: str) -> None:
        url = f"{self.sparql_url}/statements"
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(url, data={"update": sparql})
            r.raise_for_status()

    def load_turtle(self, turtle: str, *, graph_uri: str, retries: int = 3) -> None:
        """向指定 named graph 追加 Turtle（大批次可重试）。"""
        params = {"context": f"<{graph_uri}>"}
        headers = {"Content-Type": "text/turtle"}
        last: Exception | None = None
        for attempt in range(max(1, retries)):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    r = client.post(
                        self.statements_url,
                        params=params,
                        content=turtle.encode("utf-8"),
                        headers=headers,
                    )
                    r.raise_for_status()
                return
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last = exc
                if attempt + 1 >= retries:
                    break
                import time

                time.sleep(2**attempt)
        assert last is not None
        raise last

    def clear_graph(self, graph_uri: str) -> None:
        self.update(f"CLEAR GRAPH <{graph_uri}>")


def ensure_repository(client: GraphDbClient) -> None:
    """若不存在则创建 repo（Free 友好的 owl-horst-optimized）。"""
    url = f"{client.base_url.rstrip('/')}/rest/repositories"
    with httpx.Client(timeout=30.0) as http:
        r = http.get(url)
        r.raise_for_status()
        repos = r.json() if r.content else []
        ids = {x.get("id") for x in repos} if isinstance(repos, list) else set()
        if client.repository in ids:
            return
        # GraphDB REST 创建仓库：TTL config
        config = f"""
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>.
@prefix rep: <http://www.openrdf.org/config/repository#>.
@prefix sr: <http://www.openrdf.org/config/repository/sail#>.
@prefix sail: <http://www.openrdf.org/config/sail#>.
@prefix graphdb: <http://www.ontotext.com/config/graphdb#>.

[] a rep:Repository ;
  rep:repositoryID "{client.repository}" ;
  rdfs:label "HMD World Model" ;
  rep:repositoryImpl [
    rep:repositoryType "graphdb:SailRepository" ;
    sr:sailImpl [
      sail:sailType "graphdb:Sail" ;
      graphdb:ruleset "rdfsplus-optimized" ;
      graphdb:disable-sameAs "true" ;
    ]
  ].
"""
        files = {"config": ("config.ttl", config, "application/x-turtle")}
        cr = http.post(url, files=files)
        if cr.status_code >= 400:
            # 部分版本用 JSON；失败时留给调用方看 body
            cr.raise_for_status()

    # 预热 named graphs（空 INSERT）
    for g in NAMED_GRAPHS:
        try:
            client.update(
                f"INSERT DATA {{ GRAPH <{g}> {{ "
                f"<{g}> a <http://www.w3.org/2000/01/rdf-schema#Resource> }} }}"
            )
        except httpx.HTTPError:
            if g == GRAPH_BIOMEDICAL:
                raise
