"""RDF 三元组库装载与许可感知查询（L2，设计决策 D10 / D11）。

后端为 GraphDB（``GraphDbClient``）。``with_graph=True`` 时把 KB 投影同步进
命名图；运行时默认 ``with_graph=False``，术语/检索不依赖本地灌库。

命名图布局是许可隔离的执行点：
    https://w3id.org/asliva/biomed-ontology/graph/{tier}/{source}

tier 编在图 URI 里，于是"过滤掉调用方无权访问的源"退化成一次 `FROM NAMED` 集合运算，
不需要逐三元组判权限。这是把合规约束下沉到存储布局、而不是留在应用层 if 判断的关键。

事实溯源使用 RDF 1.1 标准 reification（``rdf:subject|predicate|object``），
以兼容 GraphDB / RDF4J。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from biomed_ontology._generated.hmd_concept import LicenseTierEnum
from biomed_ontology.foundation.graphdb import GraphDbClient, ensure_repository
from biomed_ontology.licensing import named_graph_uri, tier_rank

if TYPE_CHECKING:
    from biomed_ontology.ingest.seed import BuiltConcept, BuiltSynonym

__all__ = [
    "HMD",
    "SPARQL_TEMPLATES",
    "GraphStore",
    "NamedGraphInfo",
    "ShaclReport",
    "curie_to_iri",
]

HMD = "https://w3id.org/asliva/biomed-ontology/"
SKOS = "http://www.w3.org/2004/02/skos/core#"
PROV = "http://www.w3.org/ns/prov#"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"

_META_GRAPH = f"{HMD}graph/meta"

_PREFIXES = f"""@prefix hmd: <{HMD}> .
@prefix skos: <{SKOS}> .
@prefix prov: <{PROV}> .
@prefix rdf: <{RDF_NS}> .
@prefix rdfs: <{RDFS}> .
@prefix xsd: <{XSD}> .
"""

_META_LIST_SPARQL = f"""
PREFIX hmd: <{HMD}>
SELECT ?g ?tier ?source
WHERE {{
  GRAPH <{_META_GRAPH}> {{
    ?g hmd:licenseTier ?tier ; hmd:sourceId ?source .
  }}
}}
ORDER BY ?g
"""


def _curie_iri(curie: str) -> str:
    """CURIE → IRI（装载路径，不做注入校验）。"""
    if curie.startswith("HMD:"):
        return HMD + curie.replace(":", "_", 2).replace(":", "_")
    prefix, _, local = curie.partition(":")
    return f"https://bioregistry.io/{prefix.lower()}:{local}"


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
    return _curie_iri(value)


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _iri(uri: str) -> str:
    return f"<{uri}>"


def _lit_ttl(value: Any, lang: str | None = None, datatype: str | None = None) -> str:
    body = f'"{_esc(str(value))}"'
    if lang:
        return f"{body}@{lang}"
    if datatype:
        return f"{body}^^{_iri(datatype)}"
    return body


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


@dataclass
class GraphStore:
    """带许可命名图隔离的三元组库（GraphDB）。"""

    client: GraphDbClient = field(default_factory=GraphDbClient.from_settings)
    _graph_tier: dict[str, LicenseTierEnum] = field(default_factory=dict, init=False, repr=False)
    _graph_source: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _ensured: bool = field(default=False, init=False, repr=False)

    def _ensure(self) -> None:
        if self._ensured:
            return
        if not self.client.health():
            raise RuntimeError("GraphDB 为必选后端，请先 task foundation:up")
        ensure_repository(self.client)
        self._ensured = True

    # -------------------------------------------------- 装载

    def register_graph(self, source_id: str, tier: LicenseTierEnum) -> str:
        uri = named_graph_uri(source_id, tier)
        self._graph_tier[uri] = tier
        self._graph_source[uri] = source_id
        self._ensure()
        self.client.update(
            f"DELETE WHERE {{ GRAPH <{_META_GRAPH}> {{ {_iri(uri)} ?p ?o }} }}"
        )
        meta = (
            _PREFIXES
            + f"{_iri(uri)} hmd:licenseTier {_lit_ttl(tier.value)} ;\n"
            + f"  hmd:sourceId {_lit_ttl(source_id)} .\n"
        )
        self.client.load_turtle(meta, graph_uri=_META_GRAPH)
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
        by_concept: dict[str, list[BuiltSynonym]] = {}
        for s in synonyms:
            by_concept.setdefault(s.concept_id, []).append(s)

        lines = [_PREFIXES]
        for c in concepts:
            subj = _iri(_curie_iri(c.concept_id))
            lines.append(f"{subj} a skos:Concept ;")
            lines.append(f"  hmd:entityType {_lit_ttl(c.entity_type.value)} ;")
            lines.append(f"  skos:prefLabel {_lit_ttl(c.preferred_label_en, 'en')} ;")
            if c.preferred_label_zh:
                lines.append(f"  skos:prefLabel {_lit_ttl(c.preferred_label_zh, 'zh')} ;")
            if c.definition:
                lines.append(f"  skos:definition {_lit_ttl(c.definition)} ;")
            lines.append(f"  hmd:licenseTier {_lit_ttl(c.license_tier.value)} ;")
            for p in c.parents:
                lines.append(f"  skos:broader {_iri(_curie_iri(p))} ;")
            # 收束概念语句：末尾改用 `.` —— 用临时缓冲更清晰
            # 上面用分号链；最后一条改句号
            if lines[-1].endswith(" ;"):
                lines[-1] = lines[-1][:-2] + " ."
            for s in by_concept.get(c.concept_id, []):
                pred = _SCOPE_PREDICATE.get(s.scope.value, "skos:altLabel")
                lines.append(f"{subj} {pred} {_lit_ttl(s.alias_raw, s.lang.value)} .")
                a = _iri(f"{HMD}alias/{s.alias_id.replace(':', '_')}")
                lines.append(f"{a} a hmd:Synonym ;")
                lines.append(f"  hmd:ofConcept {subj} ;")
                lines.append(f"  hmd:aliasRaw {_lit_ttl(s.alias_raw)} ;")
                lines.append(f"  hmd:aliasNorm {_lit_ttl(s.alias_norm)} ;")
                lines.append(f"  hmd:scope {_lit_ttl(s.scope.value)} ;")
                if s.is_ambiguous:
                    amb = _lit_ttl("true", datatype=XSD + "boolean")
                    lines.append(f"  hmd:isAmbiguous {amb} ;")
                if lines[-1].endswith(" ;"):
                    lines[-1] = lines[-1][:-2] + " ."
        self.client.replace_graph(graph_uri, "\n".join(lines))
        return graph_uri

    def load_concept_links(
        self,
        concepts: list[BuiltConcept],
        *,
        source_id: str,
        tier: LicenseTierEnum,
    ) -> str:
        """把种子断言的类型化链接写入独立命名图。"""
        graph_uri = self.register_graph(source_id, tier)
        lines = [_PREFIXES]
        for c in concepts:
            for link in c.links:
                lines.append(
                    f"{_iri(_curie_iri(c.concept_id))} hmd:{link.predicate} "
                    f"{_iri(_curie_iri(link.object_id))} ."
                )
        self.client.replace_graph(graph_uri, "\n".join(lines))
        return graph_uri

    def load_facts(self, facts: list[Any], *, source_id: str, tier: LicenseTierEnum) -> str:
        """写入事实层。用 RDF 1.1 标准 reification 承载语句级溯源。"""
        graph_uri = self.register_graph(source_id, tier)
        lines = [_PREFIXES]
        for f in facts:
            s = _iri(_curie_iri(f.subject_id))
            p = f"hmd:{f.predicate.value}"
            o = (
                _iri(_curie_iri(f.object_id))
                if f.object_id
                else _lit_ttl(f.object_value or "")
            )
            lines.append(f"{s} {p} {o} .")

            reifier = _iri(f"{HMD}fact/{f.fact_id.replace(':', '_')}")
            lines.append(f"{reifier} a hmd:Fact ;")
            lines.append(f"  rdf:subject {s} ;")
            lines.append(f"  rdf:predicate {p} ;")
            lines.append(f"  rdf:object {o} ;")
            lines.append(f"  hmd:factId {_lit_ttl(f.fact_id)} ;")
            lines.append(f"  hmd:confidence {_lit_ttl(f.confidence, datatype=XSD + 'double')} ;")
            lines.append(f"  hmd:extractor {_lit_ttl(f.extractor_id)} ;")
            lines.append(f"  hmd:licenseTier {_lit_ttl(f.license_tier.value)} ;")
            lines.append(f"  hmd:modality {_lit_ttl(f.modality.value)} ;")
            if f.object_unit:
                lines.append(f"  hmd:unit {_lit_ttl(f.object_unit)} ;")
            for q in f.qualifiers:
                lines.append(f"  hmd:qualifier {_lit_ttl(q)} ;")
            for ev in f.evidence:
                lines.append(
                    f"  prov:wasDerivedFrom {_iri(f'{HMD}chunk/{ev.chunk_id}')} ;"
                )
                if ev.quote:
                    lines.append(f"  hmd:quote {_lit_ttl(ev.quote)} ;")
            if lines[-1].endswith(" ;"):
                lines[-1] = lines[-1][:-2] + " ."
        self.client.replace_graph(graph_uri, "\n".join(lines))
        return graph_uri

    def load_corpus(
        self,
        documents: list[Any],
        chunks: list[Any],
        *,
        source_id: str,
        tier: LicenseTierEnum,
    ) -> str:
        """把文档与切片作为 PROV 实体入库。"""
        graph_uri = self.register_graph(source_id, tier)
        lines = [_PREFIXES]
        for d in documents:
            subj = _iri(f"{HMD}doc/{d.doc_id.replace(':', '_')}")
            lines.append(f"{subj} a prov:Entity ;")
            lines.append(f"  hmd:docId {_lit_ttl(d.doc_id)} ;")
            lines.append(f"  rdfs:label {_lit_ttl(d.title)} ;")
            lines.append(f"  hmd:docType {_lit_ttl(d.doc_type.value)} ;")
            lines.append(f"  hmd:licenseTier {_lit_ttl(d.license_tier.value)} ;")
            lines.append(f"  hmd:sourceId {_lit_ttl(d.source_id)} ;")
            if d.published_on:
                lines.append(f"  hmd:publishedOn {_lit_ttl(str(d.published_on))} ;")
            if lines[-1].endswith(" ;"):
                lines[-1] = lines[-1][:-2] + " ."
        for c in chunks:
            subj = _iri(f"{HMD}chunk/{c.chunk_id}")
            doc_iri = _iri(f"{HMD}doc/{c.doc_id.replace(':', '_')}")
            lines.append(f"{subj} a prov:Entity ;")
            lines.append(f"  prov:wasDerivedFrom {doc_iri} ;")
            lines.append(f"  hmd:section {_lit_ttl(c.section)} ;")
            lines.append(f"  hmd:modality {_lit_ttl(c.modality.value)} ;")
            lines.append(f"  hmd:page {_lit_ttl(str(c.page), datatype=XSD + 'integer')} ;")
            for cid in c.concept_ids:
                lines.append(f"  hmd:mentions {_iri(_curie_iri(cid))} ;")
            if lines[-1].endswith(" ;"):
                lines[-1] = lines[-1][:-2] + " ."
        self.client.replace_graph(graph_uri, "\n".join(lines))
        return graph_uri

    def load_turtle(self, path: Path, *, graph_uri: str | None = None) -> None:
        self._ensure()
        text = path.read_text(encoding="utf-8")
        target = graph_uri or _META_GRAPH
        self.client.load_turtle(text, graph_uri=target)

    # -------------------------------------------------- 查询

    def _meta_entries(self) -> list[tuple[str, LicenseTierEnum, str]]:
        """(uri, tier, source_id)。优先 GraphDB meta；失败时回落进程内缓存。"""
        try:
            rows = self.client.query(_META_LIST_SPARQL)
        except httpx.HTTPError:
            rows = []
        out: list[tuple[str, LicenseTierEnum, str]] = []
        seen: set[str] = set()
        for row in rows:
            uri = row.get("g") or ""
            if not uri:
                continue
            try:
                tier = LicenseTierEnum(row.get("tier") or "TIER_0")
            except ValueError:
                tier = LicenseTierEnum.TIER_0
            source = row.get("source") or self._graph_source.get(uri, "")
            out.append((uri, tier, source))
            seen.add(uri)
            self._graph_tier[uri] = tier
            self._graph_source[uri] = source
        for uri, tier in self._graph_tier.items():
            if uri not in seen:
                out.append((uri, tier, self._graph_source.get(uri, "")))
        return out

    def graphs(self) -> list[NamedGraphInfo]:
        out = []
        for uri, tier, source in self._meta_entries():
            out.append(NamedGraphInfo(uri, source, tier, self.count_triples(uri)))
        return sorted(out, key=lambda i: i.uri)

    def visible_graphs(self, entitlements: frozenset[str]) -> list[str]:
        """调用方可见的命名图。TIER_0/1 一律可见，TIER_2/3 需持有该源凭据。"""
        unrestricted = tier_rank(LicenseTierEnum.TIER_1)
        return [
            uri
            for uri, tier, source in self._meta_entries()
            if tier_rank(tier) <= unrestricted or source in entitlements
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
        stripped = effective.lstrip()
        if stripped.upper().startswith("ASK"):
            return [{"result": str(self.client.ask(effective))}]
        return self.client.query(effective)

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
        """计数。无 ``graph_uri`` 时只合计 KB 许可命名图（不含 Foundation 固定图）。"""
        if graph_uri is None:
            uris = [u for u, _, _ in self._meta_entries()]
            if not uris:
                return 0
            return sum(self.count_triples(u) for u in uris)
        sparql = f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}"
        try:
            rows = self.client.query(sparql)
        except httpx.HTTPError:
            return 0
        if not rows:
            return 0
        try:
            return int(float(rows[0].get("c") or "0"))
        except ValueError:
            return 0

    def dump_turtle(self) -> bytes:
        """导出全部 statements（N-Quads）；供调试。"""
        try:
            return self.client.export_graph(accept="application/n-quads")
        except httpx.HTTPError:
            return b""

    # -------------------------------------------------- 校验

    def validate_shacl(self, shapes_path: Path, *, graph_uri: str | None = None) -> ShaclReport:
        """SHACL 校验：从 GraphDB 导出到 rdflib，再跑 pyshacl。"""
        try:
            import pyshacl
            import rdflib
        except ImportError:
            return ShaclReport(True, ["pyshacl 未安装，跳过图侧校验"])

        try:
            raw = self.client.export_graph(
                graph_uri, accept="application/n-quads" if graph_uri is None else "text/turtle"
            )
        except httpx.HTTPError as exc:
            return ShaclReport(False, [f"GraphDB 导出失败：{exc}"])

        data = rdflib.Graph()
        if not raw.strip():
            return ShaclReport(True, [])
        fmt = "nquads" if graph_uri is None else "turtle"
        try:
            data.parse(data=raw, format=fmt)
        except Exception:
            # 部分 GraphDB 版本 export 默认 turtle
            data = rdflib.Graph()
            data.parse(data=raw, format="turtle")

        shapes = rdflib.Graph().parse(str(shapes_path), format="turtle")
        conforms, _, text = pyshacl.validate(
            data, shacl_graph=shapes, inference="none", advanced=False
        )
        violations = [] if conforms else [ln for ln in text.splitlines() if "Message:" in ln]
        return ShaclReport(conforms, violations)


# 别名字面量一律落到 label 谓词。不能用 skos:broader/narrower 承载别名 ——
# 它们的值域是 skos:Concept，写入字符串会让层级查询拿到一堆无法继续展开的字面量。
# 作用域信息不会丢：它存在别名节点的 hmd:scope 上，且那才是它应待的位置。
_SCOPE_PREDICATE = {
    "EXACT": "skos:altLabel",
    # 下位词仍是指向本概念的有效检索串，保留为 altLabel。
    "NARROW": "skos:altLabel",
    # 上位词与相关词不参与检索（SCOPE_WEIGHTS 为 0），入 hiddenLabel 留存溯源。
    "BROAD": "skos:hiddenLabel",
    "RELATED": "skos:hiddenLabel",
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
