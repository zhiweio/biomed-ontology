"""图存储客户端契约。运行时只认 GraphDB，本 Protocol 切断 ontology → foundation 的急切 import。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["GraphClient"]


@runtime_checkable
class GraphClient(Protocol):
    """GraphStore 实际调用的方法面。实现类是 ``GraphDbClient``。"""

    def health(self) -> bool: ...

    def query(self, sparql: str) -> list[dict[str, str]]: ...

    def ask(self, sparql: str) -> bool: ...

    def update(self, sparql: str) -> None: ...

    def load_turtle(self, turtle: str, *, graph_uri: str, retries: int = 3) -> None: ...

    def clear_graph(self, graph_uri: str) -> None: ...

    def replace_graph(self, graph_uri: str, turtle: str) -> None: ...

    def export_graph(
        self,
        graph_uri: str | None = None,
        *,
        accept: str = "application/n-quads",
    ) -> bytes: ...
