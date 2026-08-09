"""Tree Chunk 引擎：Document → Section → Paragraph → Sentence (+ Table/Figure)。

产出正式 Evidence Object（带 parent_id / section_path / node_kind），
而非临时 RAG 切片。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from biomed_ontology._generated.hmd_fact import ModalityChannelEnum
from biomed_ontology.parse.skeleton import SectionSkeleton

# 避免与 corpus.__init__ 循环导入：运行时再取 Document/Chunk

__all__ = [
    "TreeNode",
    "build_document_tree",
    "iter_evidence_nodes",
    "tree_to_chunks",
]

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？；;])\s+")
_PARA_SPLIT = re.compile(r"\n\s*\n+")
_EVIDENCE_KINDS = frozenset({"sentence", "table", "figure", "caption", "paragraph"})


@dataclass
class TreeNode:
    node_id: str
    parent_id: str
    document_id: str
    node_kind: str
    section_path: str
    content: str
    page: int = 1
    bbox: list[float] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    modality: ModalityChannelEnum = ModalityChannelEnum.TEXT
    asset_path: str | None = None
    source_ref: str | None = None
    children: list[TreeNode] = field(default_factory=list)

    def walk(self) -> Iterable[TreeNode]:
        yield self
        for c in self.children:
            yield from c.walk()


def _nid(doc_id: str, kind: str, seed: Any) -> str:
    h = hashlib.sha1(f"{doc_id}|{kind}|{seed}".encode()).hexdigest()[:12]
    return f"TN:{kind}.{h}"


def build_document_tree(
    doc: Any,
    skeleton: list[SectionSkeleton] | None = None,
) -> TreeNode:
    """从 Document（+ 可选 SectionSkeleton）构建完整证据树。"""
    root = TreeNode(
        node_id=_nid(doc.doc_id, "document", doc.doc_id),
        parent_id="",
        document_id=doc.doc_id,
        node_kind="document",
        section_path=doc.title or doc.doc_id,
        content=doc.title or "",
        page=1,
    )

    path_to_node: dict[str, TreeNode] = {root.section_path: root}

    if skeleton:
        for sk in skeleton:
            parent = path_to_node.get(sk.parent_path or root.section_path, root)
            kind = "section" if sk.level <= 1 else "subsection"
            node = TreeNode(
                node_id=_nid(doc.doc_id, kind, sk.section_path),
                parent_id=parent.node_id,
                document_id=doc.doc_id,
                node_kind=kind,
                section_path=sk.section_path,
                content=sk.title,
                page=sk.start_page,
            )
            parent.children.append(node)
            path_to_node[sk.section_path] = node
    else:
        # 无 skeleton：按 Document.sections 名建一层 section
        for sec in doc.sections:
            node = TreeNode(
                node_id=_nid(doc.doc_id, "section", sec.name),
                parent_id=root.node_id,
                document_id=doc.doc_id,
                node_kind="section",
                section_path=sec.name,
                content=sec.name,
                page=sec.page,
            )
            root.children.append(node)
            path_to_node[sec.name] = node

    # 正文 → paragraph → sentence
    for sec in doc.sections:
        parent = path_to_node.get(sec.name) or _closest_section(path_to_node, sec.name, root)
        _attach_text(parent, doc.doc_id, sec.name, sec.text, sec.page)

    for t in doc.tables:
        parent = _pick_section_for_page(path_to_node, root, t.page)
        tbl = TreeNode(
            node_id=_nid(doc.doc_id, "table", t.table_id),
            parent_id=parent.node_id,
            document_id=doc.doc_id,
            node_kind="table",
            section_path=f"{parent.section_path} / table:{t.table_id}",
            content=t.as_text(),
            page=t.page,
            bbox=list(t.bbox),
            modality=ModalityChannelEnum.TABLE,
            asset_path=t.asset_path,
            source_ref=t.table_id,
        )
        parent.children.append(tbl)
        if t.caption:
            cap = TreeNode(
                node_id=_nid(doc.doc_id, "caption", f"tbl:{t.table_id}"),
                parent_id=tbl.node_id,
                document_id=doc.doc_id,
                node_kind="caption",
                section_path=f"{tbl.section_path} / caption",
                content=t.caption,
                page=t.page,
                modality=ModalityChannelEnum.TABLE,
                source_ref=t.table_id,
            )
            tbl.children.append(cap)

    for im in doc.images:
        parent = _pick_section_for_page(path_to_node, root, im.page)
        fig = TreeNode(
            node_id=_nid(doc.doc_id, "figure", im.image_id),
            parent_id=parent.node_id,
            document_id=doc.doc_id,
            node_kind="figure",
            section_path=f"{parent.section_path} / image:{im.image_id}",
            content=" ".join(filter(None, [im.caption, im.vision_summary])) or im.image_id,
            page=im.page,
            bbox=list(im.bbox),
            modality=ModalityChannelEnum.IMAGE,
            asset_path=im.asset_path,
            source_ref=im.image_id,
        )
        parent.children.append(fig)
        if im.caption:
            cap = TreeNode(
                node_id=_nid(doc.doc_id, "caption", f"img:{im.image_id}"),
                parent_id=fig.node_id,
                document_id=doc.doc_id,
                node_kind="caption",
                section_path=f"{fig.section_path} / caption",
                content=im.caption,
                page=im.page,
                modality=ModalityChannelEnum.IMAGE,
                source_ref=im.image_id,
            )
            fig.children.append(cap)

    return root


def _closest_section(
    path_to_node: dict[str, TreeNode], name: str, root: TreeNode
) -> TreeNode:
    if name in path_to_node:
        return path_to_node[name]
    for path, node in path_to_node.items():
        if path.endswith(name) or name in path:
            return node
    return root


def _pick_section_for_page(
    path_to_node: dict[str, TreeNode], root: TreeNode, page: int
) -> TreeNode:
    candidates = [
        n
        for n in path_to_node.values()
        if n.node_kind in {"section", "subsection"} and n.page <= page
    ]
    if not candidates:
        return root
    return max(candidates, key=lambda n: n.page)


def _attach_text(
    parent: TreeNode, doc_id: str, section_path: str, text: str, page: int
) -> None:
    if not text or not text.strip():
        return
    paragraphs = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    for pi, para in enumerate(paragraphs):
        pnode = TreeNode(
            node_id=_nid(doc_id, "paragraph", f"{section_path}|p{pi}|{para[:40]}"),
            parent_id=parent.node_id,
            document_id=doc_id,
            node_kind="paragraph",
            section_path=f"{section_path} / p{pi}",
            content=para,
            page=page,
        )
        parent.children.append(pnode)
        offset = 0
        for sent in _SENT_SPLIT.split(para):
            sent = sent.strip()
            if not sent:
                continue
            snode = TreeNode(
                node_id=_nid(doc_id, "sentence", f"{section_path}|{offset}|{sent[:48]}"),
                parent_id=pnode.node_id,
                document_id=doc_id,
                node_kind="sentence",
                section_path=f"{pnode.section_path} / s{offset}",
                content=sent,
                page=page,
            )
            pnode.children.append(snode)
            offset += len(sent)


def iter_evidence_nodes(
    tree: TreeNode,
    *,
    kinds: frozenset[str] | None = None,
) -> list[TreeNode]:
    """默认索引叶证据：sentence / table / figure / caption。"""
    wanted = kinds or _EVIDENCE_KINDS - {"paragraph"}
    return [n for n in tree.walk() if n.node_kind in wanted and n.content.strip()]


def tree_to_chunks(tree: TreeNode, *, include_paragraph: bool = False) -> list[Any]:
    from biomed_ontology.corpus import Chunk

    kinds = set(_EVIDENCE_KINDS)
    if not include_paragraph:
        kinds.discard("paragraph")
    chunks: list[Any] = []
    for n in iter_evidence_nodes(tree, kinds=frozenset(kinds)):
        chunks.append(
            Chunk(
                chunk_id=n.node_id.replace("TN:", "CHK:", 1),
                doc_id=n.document_id,
                text=n.content,
                section=n.section_path,
                char_start=0,
                char_end=len(n.content),
                modality=n.modality,
                page=n.page,
                bbox=list(n.bbox),
                source_ref=n.source_ref,
                asset_path=n.asset_path,
                parent_id=n.parent_id,
                section_path=n.section_path,
                node_kind=n.node_kind,
                entity_ids=list(n.entity_ids),
                concept_ids=list(n.entity_ids),
            )
        )
    return chunks
