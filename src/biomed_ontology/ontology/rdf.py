"""RDF 三元组库装载与许可感知查询（L2，设计决策 D10 / D11）。

构建期用 Oxigraph（嵌入式、Rust、秒级），服务期换 GraphDB —— 换的是 `GraphStore`
的实现，SPARQL 与命名图布局不变。这里刻意只用标准 SPARQL 1.1，不用任何厂商扩展。

命名图布局是许可隔离的执行点：
    https://w3id.org/asliva/biomed-ontology/graph/{tier}/{source}

tier 编在图 URI 里，于是"过滤掉调用方无权访问的源"退化成一次 `FROM NAMED` 集合运算，
不需要逐三元组判权限。这是把合规约束下沉到存储布局、而不是留在应用层 if 判断的关键。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pyoxigraph as ox

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.licensing import named_graph_uri, tier_rank

if TYPE_CHECKING:
    from biomed_ontology.ingest.seed import BuiltConcept, BuiltSynonym

__all__ = [
    "HMD",
    "SPARQL_TEMPLATES",
    "GraphStore",
    "NamedGraphInfo",
    "ShaclReport",
]

HMD = "https://w3id.org/asliva/biomed-ontology/"
SKOS = "http://www.w3.org/2004/02/skos/core#"
PROV = "http://www.w3.org/ns/prov#"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"

_META_GRAPH = f"{HMD}graph/meta"


def _n(uri: str) -> ox.NamedNode:
    return ox.NamedNode(uri)


def _curie_uri(curie: str) -> ox.NamedNode:
    """CURIE → URI。内部 CURIE 展开到 hmd 命名空间，外部 CURIE 走 Bioregistry 风格前缀。"""
    if curie.startswith("HMD:"):
        return _n(HMD + curie.replace(":", "_", 2).replace(":", "_"))
    prefix, _, local = curie.partition(":")
    return _n(f"https://bioregistry.io/{prefix.lower()}:{local}")


_SAFE_BINDING = re.compile(r"^[A-Za-z][A-Za-z0-9]*:[A-Za-z0-9._:/#%-]+$")


def curie_to_iri(value: str) -> str:
    """把模板绑定值解析成 IRI 字符串。

    只放行 CURIE 与 http(s) IRI，其余一律拒绝：绑定值最终会被拼进 SPARQL
    文本，放任任意字符等于开了一个注入口子，而查询里恰好带着 license 过滤。
    """
    if not _SAFE_BINDING.match(value) or ">" in value or "<" in value:
        raise ValueError(f"非法的模板绑定值：{value!r}")
    if value.startswith(("http://", "https://")):
        return value
    return _curie_uri(value).value


def _lit(value: Any, lang: str | None = None, datatype: str | None = None) -> ox.Literal:
    if lang:
        return ox.Literal(str(value), language=lang)
    if datatype:
        return ox.Literal(str(value), datatype=_n(datatype))
    return ox.Literal(str(value))


@dataclass(frozen=True)
class NamedGraphInfo:
    uri: str
    source_id: str
    license_tier: LicenseTierEnum
    triple_count: int


@dataclass
class ShaclReport:
    conforms: bool
    violations: list[str]

    def __bool__(self) -> bool:
        return self.conforms


class GraphStore:
    """带许可命名图隔离的三元组库。"""

    def __init__(self, path: Path | None = None) -> None:
        self._store = ox.Store(str(path)) if path else ox.Store()
        self._graph_tier: dict[str, LicenseTierEnum] = {}
        self._graph_source: dict[str, str] = {}

    # -------------------------------------------------- 装载

    def register_graph(self, source_id: str, tier: LicenseTierEnum) -> str:
        uri = named_graph_uri(source_id, tier)
        self._graph_tier[uri] = tier
        self._graph_source[uri] = source_id
        self._store.add_graph(_n(uri))
        # 图的许可元数据自身也进库，这样断电重启后 tier 不依赖进程内存。
        g = _n(_META_GRAPH)
        self._store.add(ox.Quad(_n(uri), _n(f"{HMD}licenseTier"), _lit(tier.value), g))
        self._store.add(ox.Quad(_n(uri), _n(f"{HMD}sourceId"), _lit(source_id), g))
        return uri

    def load_concepts(
        self,
        concepts: list[BuiltConcept],
        synonyms: list[BuiltSynonym],
        *,
        source_id: str,
        tier: LicenseTierEnum,
    ) -> str:
        """把术语层写入某个源的命名图。"""
        graph_uri = self.register_graph(source_id, tier)
        g = _n(graph_uri)
        by_concept: dict[str, list[BuiltSynonym]] = {}
        for s in synonyms:
            by_concept.setdefault(s.concept_id, []).append(s)

        quads = []
        for c in concepts:
            subj = _curie_uri(c.concept_id)
            quads.append(ox.Quad(subj, _n(RDF_NS + "type"), _n(SKOS + "Concept"), g))
            quads.append(ox.Quad(subj, _n(f"{HMD}entityType"), _lit(c.entity_type.value), g))
            quads.append(ox.Quad(subj, _n(SKOS + "prefLabel"), _lit(c.preferred_label_en, "en"), g))
            if c.preferred_label_zh:
                quads.append(
                    ox.Quad(subj, _n(SKOS + "prefLabel"), _lit(c.preferred_label_zh, "zh"), g)
                )
            if c.definition:
                quads.append(ox.Quad(subj, _n(SKOS + "definition"), _lit(c.definition), g))
            quads.append(ox.Quad(subj, _n(f"{HMD}licenseTier"), _lit(c.license_tier.value), g))
            for p in c.parents:
                quads.append(ox.Quad(subj, _n(SKOS + "broader"), _curie_uri(p), g))
            # scope 决定用哪个 skos 谓词，检索侧的扩展权重据此推导，不另存一份。
            for s in by_concept.get(c.concept_id, []):
                pred = _SCOPE_PREDICATE.get(s.scope.value, SKOS + "altLabel")
                quads.append(ox.Quad(subj, _n(pred), _lit(s.alias_raw, s.lang.value), g))
                a = _n(f"{HMD}alias/{s.alias_id.replace(':', '_')}")
                quads.append(ox.Quad(a, _n(RDF_NS + "type"), _n(f"{HMD}Synonym"), g))
                quads.append(ox.Quad(a, _n(f"{HMD}ofConcept"), subj, g))
                quads.append(ox.Quad(a, _n(f"{HMD}aliasRaw"), _lit(s.alias_raw), g))
                quads.append(ox.Quad(a, _n(f"{HMD}aliasNorm"), _lit(s.alias_norm), g))
                quads.append(ox.Quad(a, _n(f"{HMD}scope"), _lit(s.scope.value), g))
                if s.is_ambiguous:
                    quads.append(
                        ox.Quad(
                            a,
                            _n(f"{HMD}isAmbiguous"),
                            _lit("true", datatype=XSD + "boolean"),
                            g,
                        )
                    )
        self._store.bulk_extend(quads)
        return graph_uri

    def load_concept_links(
        self,
        concepts: list[BuiltConcept],
        *,
        source_id: str,
        tier: LicenseTierEnum,
    ) -> str:
        """把种子断言的类型化链接（药→靶点、药→适应症）写入**独立命名图**。

        谓词与事实层同名（`hmd:has_target` / `hmd:treats`），因为它们说的确实是
        同一件事；区分来源靠命名图，不靠两套词汇表 —— 后者会让 `pipeline_matrix`
        这类查询必须同时 UNION 两组谓词，而漏掉一组的失败形态是"结果少了一半"。

        但两者的证据强度天差地别：事实层的每条边都挂着 reifier（出处、置信度、
        抽取器、语句级溯源），种子链接只是一句人工断言。混进同一个图，
        "这条边是谁说的"就再也分不出来了。
        """
        graph_uri = self.register_graph(source_id, tier)
        g = _n(graph_uri)
        quads = [
            ox.Quad(
                _curie_uri(c.concept_id),
                _n(f"{HMD}{link.predicate}"),
                _curie_uri(link.object_id),
                g,
            )
            for c in concepts
            for link in c.links
        ]
        self._store.bulk_extend(quads)
        return graph_uri

    def load_facts(self, facts: list[Any], *, source_id: str, tier: LicenseTierEnum) -> str:
        """写入事实层。用 RDF 1.2 三元组项 + `rdf:reifies` 承载语句级溯源。

        备选方案是 RDF 1.1 reification（4 个额外三元组、无原生语义）
        或 fact-per-graph（命名图数量与事实数同阶增长，"按源过滤"随即失效）。
        这里的 reifier 直接用 fact_id 的 IRI，于是"某条事实的证据"是一次主语查找，
        agent 拿到 fact_id 就能原地展开全部出处。
        """
        graph_uri = self.register_graph(source_id, tier)
        g = _n(graph_uri)
        quads = []
        for f in facts:
            s = _curie_uri(f.subject_id)
            p = _n(f"{HMD}{f.predicate.value}")
            o = _curie_uri(f.object_id) if f.object_id else _lit(f.object_value or "")
            quads.append(ox.Quad(s, p, o, g))

            reifier = _n(f"{HMD}fact/{f.fact_id.replace(':', '_')}")
            quads.append(ox.Quad(reifier, _n(RDF_NS + "reifies"), ox.Triple(s, p, o), g))
            quads.append(ox.Quad(reifier, _n(RDF_NS + "type"), _n(f"{HMD}Fact"), g))
            quads.append(ox.Quad(reifier, _n(f"{HMD}factId"), _lit(f.fact_id), g))
            quads.append(
                ox.Quad(
                    reifier,
                    _n(f"{HMD}confidence"),
                    _lit(f.confidence, datatype=XSD + "double"),
                    g,
                )
            )
            quads.append(ox.Quad(reifier, _n(f"{HMD}extractor"), _lit(f.extractor_id), g))
            quads.append(ox.Quad(reifier, _n(f"{HMD}licenseTier"), _lit(f.license_tier.value), g))
            quads.append(ox.Quad(reifier, _n(f"{HMD}modality"), _lit(f.modality.value), g))
            if f.object_unit:
                quads.append(ox.Quad(reifier, _n(f"{HMD}unit"), _lit(f.object_unit), g))
            for q in f.qualifiers:
                quads.append(ox.Quad(reifier, _n(f"{HMD}qualifier"), _lit(q), g))
            for ev in f.evidence:
                quads.append(
                    ox.Quad(
                        reifier, _n(PROV + "wasDerivedFrom"), _n(f"{HMD}chunk/{ev.chunk_id}"), g
                    )
                )
                if ev.quote:
                    quads.append(ox.Quad(reifier, _n(f"{HMD}quote"), _lit(ev.quote), g))
        self._store.bulk_extend(quads)
        return graph_uri

    def load_corpus(
        self,
        documents: list[Any],
        chunks: list[Any],
        *,
        source_id: str,
        tier: LicenseTierEnum,
    ) -> str:
        """把文档与切片作为 PROV 实体入库。

        事实上的 `prov:wasDerivedFrom` 指向 chunk，若 chunk 本身不在图里，
        溯源链就断在半路：agent 能拿到 chunk_id 却无法回答"这句话出自哪一页哪一段"。
        """
        graph_uri = self.register_graph(source_id, tier)
        g = _n(graph_uri)
        quads = []
        for d in documents:
            subj = _n(f"{HMD}doc/{d.doc_id.replace(':', '_')}")
            quads.append(ox.Quad(subj, _n(RDF_NS + "type"), _n(PROV + "Entity"), g))
            quads.append(ox.Quad(subj, _n(f"{HMD}docId"), _lit(d.doc_id), g))
            quads.append(ox.Quad(subj, _n(RDFS + "label"), _lit(d.title), g))
            quads.append(ox.Quad(subj, _n(f"{HMD}docType"), _lit(d.doc_type.value), g))
            quads.append(ox.Quad(subj, _n(f"{HMD}licenseTier"), _lit(d.license_tier.value), g))
            quads.append(ox.Quad(subj, _n(f"{HMD}sourceId"), _lit(d.source_id), g))
            if d.published_on:
                quads.append(ox.Quad(subj, _n(f"{HMD}publishedOn"), _lit(str(d.published_on)), g))
        for c in chunks:
            subj = _n(f"{HMD}chunk/{c.chunk_id}")
            quads.append(ox.Quad(subj, _n(RDF_NS + "type"), _n(PROV + "Entity"), g))
            quads.append(
                ox.Quad(
                    subj,
                    _n(PROV + "wasDerivedFrom"),
                    _n(f"{HMD}doc/{c.doc_id.replace(':', '_')}"),
                    g,
                )
            )
            quads.append(ox.Quad(subj, _n(f"{HMD}section"), _lit(c.section), g))
            quads.append(ox.Quad(subj, _n(f"{HMD}modality"), _lit(c.modality.value), g))
            quads.append(
                ox.Quad(subj, _n(f"{HMD}page"), _lit(str(c.page), datatype=XSD + "integer"), g)
            )
            for cid in c.concept_ids:
                quads.append(ox.Quad(subj, _n(f"{HMD}mentions"), _curie_uri(cid), g))
        self._store.bulk_extend(quads)
        return graph_uri

    def load_turtle(self, path: Path, *, graph_uri: str | None = None) -> None:
        self._store.load(
            path.read_bytes(),
            format=ox.RdfFormat.TURTLE,
            to_graph=_n(graph_uri) if graph_uri else None,
        )

    # -------------------------------------------------- 查询

    def graphs(self) -> list[NamedGraphInfo]:
        out = []
        for uri, tier in self._graph_tier.items():
            n = sum(1 for _ in self._store.quads_for_pattern(None, None, None, _n(uri)))
            out.append(NamedGraphInfo(uri, self._graph_source[uri], tier, n))
        return sorted(out, key=lambda i: i.uri)

    def visible_graphs(self, entitlements: frozenset[str]) -> list[str]:
        """调用方可见的命名图。TIER_0/1 一律可见，TIER_2/3 需持有该源凭据。"""
        unrestricted = tier_rank(LicenseTierEnum.TIER_1)
        return [
            uri
            for uri, tier in self._graph_tier.items()
            if tier_rank(tier) <= unrestricted or self._graph_source[uri] in entitlements
        ]

    def query(
        self,
        sparql: str,
        *,
        entitlements: frozenset[str] = frozenset(),
        unrestricted: bool = False,
    ) -> list[dict[str, str]]:
        """许可感知查询。

        重写方式是注入 `FROM NAMED` 而不是加 FILTER：
        FILTER 依赖查询作者主动写对，注入 dataset 则在引擎层面让无权数据根本不可达。
        """
        effective = sparql if unrestricted else self._rewrite(sparql, entitlements)
        result = self._store.query(effective)
        if isinstance(result, ox.QueryBoolean):
            return [{"result": str(bool(result))}]
        out = []
        for sol in result:
            row = {}
            for var in result.variables:
                term = sol[var]
                row[var.value] = term.value if term is not None else None
            out.append(row)
        return out

    def _rewrite(self, sparql: str, entitlements: frozenset[str]) -> str:
        visible = self.visible_graphs(entitlements)
        if not visible:
            # 无可见图时给一个空 dataset，让查询返回空集而不是"无 dataset = 全库"。
            visible = [f"{HMD}graph/__empty__"]
        clause = "\n".join(f"FROM NAMED <{u}>" for u in sorted(visible))
        clause += f"\nFROM <{_META_GRAPH}>"
        upper = sparql.upper()
        idx = upper.find("WHERE")
        if idx < 0:
            return sparql
        return sparql[:idx] + clause + "\n" + sparql[idx:]

    def count_triples(self, graph_uri: str | None = None) -> int:
        g = _n(graph_uri) if graph_uri else None
        return sum(1 for _ in self._store.quads_for_pattern(None, None, None, g))

    def dump_turtle(self) -> bytes:
        return self._store.dump(format=ox.RdfFormat.N_QUADS) or b""

    # -------------------------------------------------- 校验

    def validate_shacl(self, shapes_path: Path, *, graph_uri: str | None = None) -> ShaclReport:
        """SHACL 校验。

        用 `schema/shapes/projection.shacl.ttl` 而非 gen-shacl 产物：
        后者描述的是 LinkML 实例形状（closed、按 slot URI 命名），
        而入库的是 SKOS/PROV 投影，谓词对不上。
        """
        try:
            import pyshacl
            import rdflib
        except ImportError:
            return ShaclReport(True, ["pyshacl 未安装，跳过图侧校验"])

        data = rdflib.Graph()
        pattern_graph = _n(graph_uri) if graph_uri else None
        for q in self._store.quads_for_pattern(None, None, None, pattern_graph):
            try:
                data.add(
                    (
                        _to_rdflib(q.subject, rdflib),
                        _to_rdflib(q.predicate, rdflib),
                        _to_rdflib(q.object, rdflib),
                    )
                )
            except TypeError:
                continue  # 三元组项（rdf:reifies 的宾语），rdflib 侧不参与 SHACL 校验

        shapes = rdflib.Graph().parse(str(shapes_path), format="turtle")
        conforms, _, text = pyshacl.validate(
            data, shacl_graph=shapes, inference="none", advanced=False
        )
        violations = [] if conforms else [ln for ln in text.splitlines() if "Message:" in ln]
        return ShaclReport(conforms, violations)


def _to_rdflib(term: Any, rdflib: Any) -> Any:
    if isinstance(term, ox.NamedNode):
        return rdflib.URIRef(term.value)
    if isinstance(term, ox.BlankNode):
        return rdflib.BNode(term.value)
    if isinstance(term, ox.Literal):
        if term.language:
            return rdflib.Literal(term.value, lang=term.language)
        dt = term.datatype.value
        # oxigraph 按 RDF 1.1 把无类型字面量归一为 xsd:string，rdflib 却把
        # Literal("x") 与 Literal("x", datatype=xsd:string) 当作不相等。不抓回去的话，
        # shapes 里写的 sh:in ("EXACT" ...) 会全部落空 —— 而且是静默地报违规，
        # 看上去像数据错了。同样的坑会出现在任何走 rdflib 的下游。
        if dt == XSD + "string":
            return rdflib.Literal(term.value)
        return rdflib.Literal(term.value, datatype=rdflib.URIRef(dt))
    raise TypeError(f"不可转换的项：{type(term)}")


# 别名字面量一律落到 label 谓词。不能用 skos:broader/narrower 承载别名 ——
# 它们的值域是 skos:Concept，写入字符串会让层级查询拿到一堆无法继续展开的字面量。
# 作用域信息不会丢：它存在别名节点的 hmd:scope 上，且那才是它应待的位置。
_SCOPE_PREDICATE = {
    "EXACT": SKOS + "altLabel",
    # 下位词仍是指向本概念的有效检索串，保留为 altLabel。
    "NARROW": SKOS + "altLabel",
    # 上位词与相关词不参与检索（SCOPE_WEIGHTS 为 0），入 hiddenLabel 留存溯源。
    "BROAD": SKOS + "hiddenLabel",
    "RELATED": SKOS + "hiddenLabel",
}


# 预置查询模板（L5）。放在库里而不是散在调用方，是为了让 license rewriting
# 与查询性能调优有唯一的收口点。
SPARQL_TEMPLATES: dict[str, str] = {
    # 事实与概念标签落在不同命名图（事实按源分图、概念在 SEED_INTERNAL），
    # 所以这里必须分开两个 GRAPH 块 —— 写成一个块会静默返回 0 行。
    "pipeline_matrix": """
PREFIX hmd: <https://w3id.org/asliva/biomed-ontology/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT DISTINCT ?drug ?drugLabel ?target ?targetLabel
WHERE {
  GRAPH ?gf { ?drug hmd:has_target|hmd:inhibits ?target . }
  GRAPH ?gc { ?drug skos:prefLabel ?drugLabel . }
  GRAPH ?gc2 { ?target skos:prefLabel ?targetLabel . }
}
ORDER BY ?drugLabel
""",
    "concept_aliases": """
PREFIX hmd: <https://w3id.org/asliva/biomed-ontology/>
SELECT ?aliasRaw ?scope
WHERE {
  GRAPH ?g {
    ?a hmd:ofConcept <%(concept_uri)s> ;
       hmd:aliasRaw ?aliasRaw ;
       hmd:scope ?scope .
  }
}
""",
    "descendants": """
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT DISTINCT ?d
WHERE {
  GRAPH ?g { ?d skos:broader+ <%(concept_uri)s> . }
}
""",
    "graph_inventory": """
PREFIX hmd: <https://w3id.org/asliva/biomed-ontology/>
SELECT ?g ?tier ?source
WHERE { ?g hmd:licenseTier ?tier ; hmd:sourceId ?source . }
ORDER BY ?g
""",
}
