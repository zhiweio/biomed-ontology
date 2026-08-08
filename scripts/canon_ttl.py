"""把 gen-shacl / gen-owl 产出的 TTL 规范化后原地重写。

LinkML 经 rdflib 序列化时，空白节点的标签是每次新生成的，
于是 `sh:property [ ... ]` 这类匿名块的排列每次都不同：
内容一字未改，diff 却有几千行（上一次实测 6341 增 = 6341 删，纯 ordering）。

后果不是"难看"，而是**生成物失去可审查性** —— 真正的 schema 变更被淹没在噪声里，
而且每跑一次 `task gen` 工作区就变脏，久而久之大家习惯性 checkout 掉生成物，
连带真实变更也一起丢。

`to_canonical_graph()` 按图同构给空白节点算出确定性标签（URDNA2015 思路），
之后 turtle 序列化就稳定了。N-Triples 不行：它按集合迭代序直接倾倒，不排序。
"""

from __future__ import annotations

import sys
from pathlib import Path

from rdflib import Graph
from rdflib.collection import Collection
from rdflib.compare import to_canonical_graph
from rdflib.namespace import SH

# 语义上是集合、但被 LinkML 写成 RDF list 的谓词。
# RDF list 是有序结构，图同构规范化会如实保留顺序 —— 而这里的顺序来自
# Python set 的遍历序，每次都不同。只有确认"顺序不承载语义"才能排序：
# sh:ignoredProperties 是 sh:closed 校验时的豁免名单，重排不改变任何判定。
_UNORDERED_LISTS = (SH.ignoredProperties,)


def _sort_set_valued_lists(graph: Graph) -> None:
    for predicate in _UNORDERED_LISTS:
        for _, head in graph.subject_objects(predicate):
            collection = Collection(graph, head)
            for index, member in enumerate(sorted(collection, key=str)):
                collection[index] = member


def canonicalize(path: Path, *, check: bool = False) -> bool:
    """重写为规范形式，返回内容是否发生变化。`check=True` 时只判定、不落盘。"""
    original = path.read_text(encoding="utf-8")
    graph = Graph()
    graph.parse(data=original, format="turtle")
    _sort_set_valued_lists(graph)

    # to_canonical_graph 返回只读聚合图：既不能 bind 前缀，序列化也会退化成全 URI。
    # 因此把三元组倒进一张新图，再把原前缀绑回去，否则生成物没法读。
    canonical = Graph()
    canonical += to_canonical_graph(graph)
    for prefix, uri in graph.namespaces():
        canonical.bind(prefix, uri, replace=True)

    out = canonical.serialize(format="turtle")
    if out == original:
        return False
    if not check:
        path.write_text(out, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    check = "--check" in argv
    paths = [Path(a) for a in argv if a != "--check"]
    if not paths:
        print("用法: canon_ttl.py [--check] <file.ttl>...", file=sys.stderr)
        return 2

    drifted = [p for p in paths if canonicalize(p, check=check)]
    if not check:
        for p in drifted:
            print(f"canon {p}")
        return 0

    for p in drifted:
        print(f"非规范形式: {p}", file=sys.stderr)
    if drifted:
        print(f"\n{len(drifted)}/{len(paths)} 份生成物不是规范形式，请跑 task gen", file=sys.stderr)
    return 1 if drifted else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
