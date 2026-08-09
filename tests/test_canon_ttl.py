"""生成物 TTL 的规范化序列化。

rdflib 默认会重编空白节点标签，导致 `task gen` 在内容未变时产生巨量噪声 diff。
规范化后序列化须稳定，真实 schema 变更才可审查。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from rdflib import Graph
from rdflib.compare import isomorphic

GENERATED = Path(__file__).resolve().parents[1] / "schema" / "generated"
_SPEC = importlib.util.spec_from_file_location(
    "canon_ttl", Path(__file__).resolve().parents[1] / "scripts" / "canon_ttl.py"
)
assert _SPEC and _SPEC.loader
canon_ttl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(canon_ttl)

# 只取每类最小的一份。`to_canonical_graph` 是图同构算法，随空白节点数急剧变慢：
# 全部 10 份跑一遍要 4 分半，而 hmd_tools.owl.ttl 一份就占 79 秒。
# 把它塞进默认套件的真实后果不是"慢一点"，是大家不再跑 task check。
#
# 全量覆盖挂在 `task nightly`（= `task canon-check`，只判定不落盘）。
# 这里守的是**机制没坏**：规范化确实消除重排，且不改变图语义。
TTL_FILES = [
    GENERATED / "hmd_concept.shacl.ttl",
    GENERATED / "hmd_concept.owl.ttl",
]


def test_representative_files_exist():
    """写死文件名，改名后必须在这里显式跟进 —— 否则参数化会静默变空，
    31 条断言一条不剩地"通过"。"""
    for ttl in TTL_FILES:
        assert ttl.is_file(), f"{ttl.name} 不存在，请更新 TTL_FILES"


@pytest.mark.parametrize("ttl", TTL_FILES, ids=lambda p: p.name)
def test_committed_output_is_already_canonical(ttl: Path, tmp_path: Path):
    """已提交的生成物必须是规范形式，否则下一次 `task gen` 又会刷出巨型 diff。"""
    copy = tmp_path / ttl.name
    copy.write_text(ttl.read_text(encoding="utf-8"), encoding="utf-8")
    assert not canon_ttl.canonicalize(copy), f"{ttl.name} 未规范化，请跑 task canon-ttl"


@pytest.mark.parametrize("ttl", TTL_FILES, ids=lambda p: p.name)
def test_reserialising_then_canonicalising_returns_the_same_bytes(ttl: Path, tmp_path: Path):
    """模拟"重新生成"：rdflib 重新序列化会换一批空白节点标签、换一种排列，
    这正是 `task gen` 每次发生的事。规范化后必须回到同一份字节。"""
    graph = Graph()
    graph.parse(ttl, format="turtle")
    shuffled = tmp_path / ttl.name
    shuffled.write_text(graph.serialize(format="turtle"), encoding="utf-8")

    canon_ttl.canonicalize(shuffled)
    assert shuffled.read_text(encoding="utf-8") == ttl.read_text(encoding="utf-8")


@pytest.mark.parametrize("ttl", TTL_FILES, ids=lambda p: p.name)
def test_canonicalisation_preserves_the_graph(ttl: Path, tmp_path: Path):
    """规范化只准动表示，不准动图。

    这条是前两条的安全带：一个把文件清空的实现同样"幂等"，
    没有这条断言，规范化可以用删内容的方式作弊通过。
    """
    before = Graph()
    before.parse(ttl, format="turtle")

    copy = tmp_path / ttl.name
    copy.write_text(before.serialize(format="turtle"), encoding="utf-8")
    canon_ttl.canonicalize(copy)

    after = Graph()
    after.parse(copy, format="turtle")
    assert isomorphic(before, after), f"{ttl.name} 规范化改变了图语义"
