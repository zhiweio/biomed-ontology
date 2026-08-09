"""端到端装配：企业目录（ENT）→ 术语层 → 语料 → 标引 → 抽取 → 事实层。

运行时文献面入口：``build_literature_base()``（默认 ``HMD:ENT:*``）。
``build_knowledge_base()`` 为薄别名；``legacy_seed_ids=True`` 仅供单测 ledger 对照。
``with_graph=True`` 时把 KB 投影同步进 GraphDB；默认关闭。
"""

from __future__ import annotations

import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.corpus import Chunk, Document, chunk_document, load_corpus
from biomed_ontology.corpus.classify import (
    DocumentLabel,
    TaxonomyClassifier,
    load_taxonomy,
)
from biomed_ontology.corpus.extract import ExtractedFact, TriModalPipeline
from biomed_ontology.foundation.graphdb import GraphDbClient
from biomed_ontology.ingest import build_from_seed, load_ambiguity_registry
from biomed_ontology.ingest.seed import BuiltConcept, BuiltSynonym
from biomed_ontology.normalize import Normalizer
from biomed_ontology.observability import ObservabilityHub, TraceContext
from biomed_ontology.ontology.ids import IdLedger, SequenceLedger
from biomed_ontology.ontology.rdf import GraphStore
from biomed_ontology.registry import SourceRegistry, load_registry

__all__ = [
    "DATA_ROOT",
    "ONTOLOGY_CATALOG",
    "KnowledgeBase",
    "build_knowledge_base",
    "build_literature_base",
    "catalog_files",
    "ensure_catalog_graphs",
]

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
REPO_ROOT = DATA_ROOT.parent
ONTOLOGY_CATALOG = REPO_ROOT / "ontology" / "catalog"
DEFAULT_RELEASE = "0.3.0-ent"

_CATALOG_SOURCE = "SEED_INTERNAL"
_CATALOG_LINKS_SOURCE = "SEED_LINKS"


def ensure_catalog_graphs(
    graph: GraphStore,
    concepts: list[BuiltConcept],
    synonyms: list[BuiltSynonym],
) -> None:
    """把企业目录与类型化链接写入 GraphDB（search-around 边权威）。"""
    if not graph.client.health():
        raise RuntimeError("GraphDB 不可达；GRAPH 通道需要 task foundation:up")
    graph.load_concepts(
        concepts, synonyms, source_id=_CATALOG_SOURCE, tier=LicenseTierEnum.TIER_0
    )
    graph.load_concept_links(
        concepts, source_id=_CATALOG_LINKS_SOURCE, tier=LicenseTierEnum.TIER_0
    )


@dataclass
class KnowledgeBase:
    release_id: str
    registry: SourceRegistry
    concepts: list[BuiltConcept]
    synonyms: list[BuiltSynonym]
    normalizer: Normalizer
    documents: list[Document]
    chunks: list[Chunk]
    labels: dict[str, list[DocumentLabel]]
    facts: list[ExtractedFact]
    graph: GraphStore
    hub: ObservabilityHub
    taxonomy_version: str = "0.1.0"
    warnings: list[str] = field(default_factory=list)

    def concept(self, concept_id: str) -> BuiltConcept | None:
        return self.normalizer.concept(concept_id)

    def document(self, doc_id: str) -> Document | None:
        return next((d for d in self.documents if d.doc_id == doc_id), None)

    def chunk(self, chunk_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.chunk_id == chunk_id), None)

    def doc_tier(self, doc_id: str) -> LicenseTierEnum:
        d = self.document(doc_id)
        return d.license_tier if d else LicenseTierEnum.TIER_0

    def stats(self) -> dict[str, int | float]:
        return {
            "concepts": len(self.concepts),
            "synonyms": len(self.synonyms),
            "documents": len(self.documents),
            "chunks": len(self.chunks),
            "facts": len(self.facts),
            "triples": self.graph.count_triples(),
            "label_coverage": round(
                sum(1 for v in self.labels.values() if v) / max(1, len(self.labels)), 4
            ),
        }


def catalog_files(data_root: Path | None = None) -> list[Path]:
    """优先 ``ontology/catalog/*.yaml``；缺失时回落 ``data/seed``（测试对照）。"""
    root = data_root or DATA_ROOT
    catalog = ONTOLOGY_CATALOG
    if catalog.is_dir():
        files = sorted(
            p for p in catalog.glob("*.yaml") if p.name not in {"ambiguity.yaml", "DEPRECATED.md"}
        )
        if files:
            return files
    seed = root / "seed"
    return sorted(p for p in seed.glob("*.yaml") if p.name != "ambiguity.yaml")


def build_literature_base(
    *,
    data_root: Path | None = None,
    release_id: str = DEFAULT_RELEASE,
    ledger_dir: Path | None = None,
    hub: ObservabilityHub | None = None,
    with_corpus: bool = True,
    with_graph: bool = False,
    id_mode: str = "enterprise",
    graph_client: GraphDbClient | None = None,
) -> KnowledgeBase:
    """装配文献面：ENT 目录 + corpus（身份不经 HMD:SUB 铸造）。

    ``with_graph=True`` 时把概念/链接/语料/事实同步进 GraphDB（需可达）；
    默认关闭。可注入 ``graph_client``（单测 respx mock）。
    """
    root = data_root or DATA_ROOT
    hub = hub or ObservabilityHub()
    ledgers = ledger_dir or Path(tempfile.mkdtemp(prefix="hmd-ledger-"))
    ledgers.mkdir(parents=True, exist_ok=True)

    registry = load_registry(
        root / "registry" / "sources.yaml"
        if (root / "registry" / "sources.yaml").exists()
        else None
    )
    amb_path = ONTOLOGY_CATALOG / "ambiguity.yaml"
    if not amb_path.exists():
        amb_path = root / "seed" / "ambiguity.yaml"
    ambiguity = load_ambiguity_registry(amb_path) if amb_path.exists() else None

    id_ledger = None
    if id_mode == "ledger":
        id_ledger = IdLedger(ledgers / "concept_ids.json", release=release_id)

    built = build_from_seed(
        catalog_files(root),
        registry=registry,
        id_ledger=id_ledger,
        alias_ledger=SequenceLedger(ledgers / "alias_ids.json", prefix="HMDA"),
        ambiguity=ambiguity,
        id_mode=id_mode,
    )

    normalizer = Normalizer(
        concepts=built.concepts,
        synonyms=built.synonyms,
        ambiguity_index=ambiguity.norm_index() if ambiguity else {},
        release_id=release_id,
    )

    graph = GraphStore(client=graph_client) if graph_client is not None else GraphStore()
    if with_graph:
        graph.load_concepts(
            built.concepts, built.synonyms, source_id=_CATALOG_SOURCE, tier=LicenseTierEnum.TIER_0
        )
        graph.load_concept_links(
            built.concepts, source_id=_CATALOG_LINKS_SOURCE, tier=LicenseTierEnum.TIER_0
        )

    kb = KnowledgeBase(
        release_id=release_id,
        registry=registry,
        concepts=built.concepts,
        synonyms=built.synonyms,
        normalizer=normalizer,
        documents=[],
        chunks=[],
        labels={},
        facts=[],
        graph=graph,
        hub=hub,
        warnings=[
            f"未登记歧义别名：{n} → {ids}" for n, ids in built.unregistered_collisions.items()
        ]
        + [f"父节点无法解析：{cid} → {ps}" for cid, ps in built.unresolved_parents.items()]
        + [f"类型化链接无法解析：{cid} → {ls}" for cid, ls in built.unresolved_links.items()],
    )
    if not with_corpus:
        return kb

    taxonomy = load_taxonomy(root / "taxonomy" / "labels.yaml")
    classifier = TaxonomyClassifier(taxonomy)
    kb.taxonomy_version = taxonomy.taxonomy_version

    corpus_files = sorted((root / "corpus").glob("*.yaml")) + sorted(
        (root / "corpus" / "parsed").glob("*.yaml")
    )
    documents: list[Document] = []
    for f in corpus_files:
        documents.extend(load_corpus(f))

    ctx = hub.start_trace(release_id=release_id, agent_id="pipeline")
    chunks: list[Chunk] = []
    labels: dict[str, list[DocumentLabel]] = {}
    with ctx.span("ingest_corpus", **{"hmd.doc_count": len(documents)}):
        for doc in documents:
            doc_chunks = chunk_document(doc)
            labels[doc.doc_id] = classifier.classify(doc.doc_id, doc.full_text(), ctx=ctx)
            for ch in doc_chunks:
                res = normalizer.normalize(ch.text, ctx=ctx, detect=True, min_confidence=0.6)
                ch.concept_ids = sorted(set(res.concept_ids))
                ch.concept_ids_expanded = _expand_all(normalizer, ch.concept_ids, ctx)
                ch.labels = [ln.label_id for ln in labels[doc.doc_id]]
            chunks.extend(doc_chunks)

    facts = TriModalPipeline().run(documents, chunks, normalizer=normalizer, ctx=ctx)
    for f in facts:
        tiers = [kb_doc_tier(documents, e.doc_id) for e in f.evidence]
        f.license_tier = max(tiers, key=_tier_rank) if tiers else LicenseTierEnum.TIER_0

    kb.documents = documents
    kb.chunks = chunks
    kb.labels = labels
    kb.facts = facts

    if with_graph:
        for source_id, tier in _partition(documents):
            docs = [d for d in documents if d.source_id == source_id]
            doc_ids = {d.doc_id for d in docs}
            graph.load_corpus(
                docs,
                [c for c in chunks if c.doc_id in doc_ids],
                source_id=source_id,
                tier=tier,
            )
            graph.load_facts(
                [f for f in facts if any(e.doc_id in doc_ids for e in f.evidence)],
                source_id=source_id,
                tier=tier,
            )

    hub.commit(ctx, _pipeline_io(ctx, release_id, kb))
    return kb


def build_knowledge_base(
    *,
    data_root: Path | None = None,
    release_id: str | None = None,
    ledger_dir: Path | None = None,
    hub: ObservabilityHub | None = None,
    with_corpus: bool = True,
    with_graph: bool | None = None,
    legacy_seed_ids: bool = False,
) -> KnowledgeBase:
    """``build_literature_base`` 的薄别名。

    ``legacy_seed_ids=True`` → ``id_mode=ledger``（单测对照）。
    新代码请直接调用 ``build_literature_base``。
    """
    if not legacy_seed_ids:
        warnings.warn(
            "build_knowledge_base() 是文献装配别名；请用 "
            "runtime.open_dual_surface() / build_literature_base()",
            DeprecationWarning,
            stacklevel=2,
        )
    mode = "ledger" if legacy_seed_ids else "enterprise"
    graph = bool(with_graph) if with_graph is not None else False
    rid = release_id or ("0.1.0" if legacy_seed_ids else DEFAULT_RELEASE)
    return build_literature_base(
        data_root=data_root,
        release_id=rid,
        ledger_dir=ledger_dir,
        hub=hub,
        with_corpus=with_corpus,
        with_graph=graph,
        id_mode=mode,
    )


def kb_doc_tier(documents: list[Document], doc_id: str) -> LicenseTierEnum:
    d = next((x for x in documents if x.doc_id == doc_id), None)
    return d.license_tier if d else LicenseTierEnum.TIER_0


def _tier_rank(tier: LicenseTierEnum) -> int:
    return int(tier.value.rsplit("_", 1)[-1])


def _partition(documents: list[Document]) -> list[tuple[str, LicenseTierEnum]]:
    """按 (source, tier) 分组。同一源出现多个 tier 时按最严的建图。"""
    seen: dict[str, LicenseTierEnum] = {}
    for d in documents:
        cur = seen.get(d.source_id)
        if cur is None or _tier_rank(d.license_tier) > _tier_rank(cur):
            seen[d.source_id] = d.license_tier
    return sorted(seen.items())


def _expand_all(normalizer: Normalizer, concept_ids: list[str], ctx: TraceContext) -> list[str]:
    out: set[str] = set()
    for cid in concept_ids:
        out.update(normalizer.descendants(cid, max_depth=2))
    return sorted(out - set(concept_ids))


def _pipeline_io(ctx: TraceContext, release_id: str, kb: KnowledgeBase):
    from biomed_ontology.observability import ToolIoRecord

    total = sum(s.duration_ms or 0.0 for s in ctx.spans if s.parent_id is None)
    return ToolIoRecord(
        trace_id=ctx.trace_id,
        tool_name="build_literature_base",
        ontology_release_id=release_id,
        input_json="{}",
        output_json=str(kb.stats()),
        latency_ms=total,
        agent_id=ctx.agent_id,
    )
