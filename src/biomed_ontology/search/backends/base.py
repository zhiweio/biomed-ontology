"""检索后端协议：把"候选怎么召回"与"许可怎么隔离、结果怎么融合"分开。

本地实现与 Milvus 实现的差别只在召回方式（内存倒排 vs 向量库），
而许可判定必须**只有一份**。因此谓词写在 `LicenseScope.permits` 上，
两个后端各自应用同一个函数 —— 一个把它跑成 Python 循环，一个把它翻译成标量过滤
表达式下推给 Milvus。谓词一旦分叉，就会出现"本地测试通过、线上泄漏"的最坏情形。

融合刻意留在后端之外。Milvus 的 `hybrid_search(rerank=RRFRanker())` 能在库内融合，
但融合后的分数无法再分解回各通道名次，`SearchHit.explain` 与四支柱里的归因
就此断掉。逐通道召回、进程内融合，换来的是每一条命中都能说清"谁把它带进来的"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from biomed_ontology._generated.hmd_fact import RetrievalChannelEnum

__all__ = [
    "BackendResult",
    "ChunkMeta",
    "LicenseScope",
    "RetrievalRequest",
    "SearchBackend",
    "merge_best",
]


def merge_best(
    existing: list[tuple[str, float]], incoming: list[tuple[str, float]]
) -> list[tuple[str, float]]:
    """同一通道下多路结果取每个 chunk 的最高分，再按分排序。

    两处会用到：一个通道横跨多个向量列（Milvus 的三列稠密），
    以及一个通道横跨多个查询串（本体改写后的 multi-query）。
    取 max 而非求和，是因为"某一路特别匹配"才是信号，
    求和会奖励那些对每一路都平庸的候选。
    """
    best: dict[str, float] = dict(existing)
    for cid, score in incoming:
        if score > best.get(cid, float("-inf")):
            best[cid] = score
    return sorted(best.items(), key=lambda kv: -kv[1])


@dataclass(frozen=True)
class LicenseScope:
    """许可约束的后端无关表示。

    与 `HybridSearcher._allowed_chunks` 的原始谓词逐字等价：
    tier 超过调用方上限一律拒；超过公开档的还需要 source 级凭据。
    """

    max_rank: int
    open_rank: int
    entitled_sources: frozenset[str] = frozenset()

    def permits(self, license_rank: int, source_id: str) -> bool:
        if license_rank > self.max_rank:
            return False
        return license_rank <= self.open_rank or source_id in self.entitled_sources

    def milvus_expr(self, *, known_sources: frozenset[str] | None = None) -> str:
        """`permits` 的过滤表达式形态。**刻意与它相邻**，两者不能各自漂移。

        `known_sources` 是注册表里已登记的 source_id。凭据必须先与它求交集
        再拼进表达式 —— 原样拼接客户端自述的字符串就是表达式注入。
        """
        entitled = self.entitled_sources
        if known_sources is not None:
            entitled = frozenset(entitled & known_sources)
        base = f"license_rank <= {int(self.max_rank)}"
        if not entitled:
            return f"{base} and license_rank <= {int(self.open_rank)}"
        allow = ", ".join(f'"{_safe_source(s)}"' for s in sorted(entitled))
        return f"{base} and (license_rank <= {int(self.open_rank)} or source_id in [{allow}])"


_SOURCE_OK = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _safe_source(source_id: str) -> str:
    """source_id 只允许标识符字符。不合规就是配置错误，必须炸而不是转义。

    转义看起来更"友好"，但会让一个本该被发现的脏数据静静流进查询里。
    """
    if not _SOURCE_OK.match(source_id):
        raise ValueError(f"source_id 含非法字符，拒绝拼入过滤表达式：{source_id!r}")
    return source_id


@dataclass(frozen=True)
class ChunkMeta:
    """后端做许可、标签与模态过滤所需的最小元数据，不含正文。"""

    chunk_id: str
    doc_id: str
    source_id: str
    license_rank: int
    labels: tuple[str, ...] = ()
    modality: str = ""
    # 图型（RADIOLOGY / MICROSCOPY / CHART / ...），未分类为空串。
    figure_type: str = ""


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    scope: LicenseScope
    top_k: int = 10
    labels: tuple[str, ...] = ()
    channels: tuple[RetrievalChannelEnum, ...] = (
        RetrievalChannelEnum.BM25,
        RetrievalChannelEnum.DENSE,
    )
    # 逐向量列消融用（P13）。空元组表示后端默认全开；本地后端无向量列，忽略此项。
    vector_fields: tuple[str, ...] = ()
    # 只保留这些模态的候选。空元组 = 不限模态。
    #
    # 这是过滤而不是加权：文本-文本相似度系统性高于文本-图像，靠调分数让图浮上来
    # 需要一个说不清的跨模态偏置项，而"我要看那张图"本来就是个布尔条件。
    modalities: tuple[str, ...] = ()
    # 只保留这些图型的候选。空元组 = 不限图型。
    #
    # `modalities=[IMAGE]` 只保证返回的是图，不保证是**那类**图：README 里那个
    # "chest CT" 的查询加了模态过滤后，首位仍是一张信号强度柱状图。图型是下一格。
    figure_types: tuple[str, ...] = ()

    # 本体改写后的查询串。两个通道分开传，是因为它们吃扩展词的方式根本不同。
    #
    # 词法通道能直接受益于追加同义词：多一个词就多一条命中路径，BM25 自带的
    # IDF 会压住那些烂大街的扩展词。所以给它一个**拼接**后的长串。
    #
    # 稠密通道不行：把八个别名拼进一句话，编出来的是这八个词的质心，
    # 离原始查询的语义反而更远 —— 这是"查询漂移"，扩展越多漂得越厉害。
    # 所以给它一**组**串，各自编码后按 `merge_best` 取最高分。
    #
    # 两者都留空时，后端一律回落到 `query`，也就是不开改写时的原样行为。
    lexical_query: str | None = None
    dense_queries: tuple[str, ...] = ()

    def lexical_text(self) -> str:
        return self.lexical_query or self.query

    def dense_texts(self) -> tuple[str, ...]:
        return self.dense_queries or (self.query,)


@dataclass
class BackendResult:
    channels: dict[RetrievalChannelEnum, list[tuple[str, float]]] = field(default_factory=dict)
    # 被许可谓词挡掉的切片数。必须回传：只报"命中 0 条"会让无权调用方
    # 无法区分"没有这份资料"和"有但你看不到"，而后者本身就是要暴露的事实。
    filtered_count: int = 0


@runtime_checkable
class SearchBackend(Protocol):
    name: str

    def retrieve(self, request: RetrievalRequest) -> BackendResult:
        """按通道分别召回，不做跨通道融合。"""
        ...
