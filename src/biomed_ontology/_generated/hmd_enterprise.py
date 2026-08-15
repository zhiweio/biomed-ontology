from __future__ import annotations

import re
import sys
from datetime import (
    date,
    datetime,
    time
)
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    ClassVar,
    Literal,
    Optional,
    Union
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer
)


metamodel_version = "1.11.0"
version = "None"


class ConfiguredBaseModel(BaseModel):
    model_config = ConfigDict(
        serialize_by_alias = True,
        validate_by_name = True,
        validate_assignment = True,
        validate_default = True,
        extra = "forbid",
        arbitrary_types_allowed = True,
        use_enum_values = True,
        strict = False,
    )





class LinkMLMeta(RootModel):
    root: dict[str, Any] = {}
    model_config = ConfigDict(frozen=True)

    def __getattr__(self, key:str):
        return getattr(self.root, key)

    def __getitem__(self, key:str):
        return self.root[key]

    def __setitem__(self, key:str, value):
        self.root[key] = value

    def __contains__(self, key:str) -> bool:
        return key in self.root


linkml_meta = LinkMLMeta({'default_prefix': 'hmd',
     'default_range': 'string',
     'description': '企业内部创新药研发世界模型（Enterprise Ontology）。 企业实体主键是 HMD:ENT:* '
                    'CURIE，不是 BIOS / ChEBI 等外部概念 ID。 外部概念通过 exact_match_xrefs '
                    '挂接（skos:exactMatch / SSSOM）。',
     'id': 'https://w3id.org/asliva/biomed-ontology/enterprise',
     'imports': ['linkml:types', 'hmd_types'],
     'license': 'Proprietary',
     'name': 'hmd_enterprise',
     'prefixes': {'biolink': {'prefix_prefix': 'biolink',
                              'prefix_reference': 'https://w3id.org/biolink/vocab/'},
                  'hmd': {'prefix_prefix': 'hmd',
                          'prefix_reference': 'https://w3id.org/asliva/biomed-ontology/'},
                  'linkml': {'prefix_prefix': 'linkml',
                             'prefix_reference': 'https://w3id.org/linkml/'},
                  'owl': {'prefix_prefix': 'owl',
                          'prefix_reference': 'http://www.w3.org/2002/07/owl#'},
                  'prov': {'prefix_prefix': 'prov',
                           'prefix_reference': 'http://www.w3.org/ns/prov#'},
                  'skos': {'prefix_prefix': 'skos',
                           'prefix_reference': 'http://www.w3.org/2004/02/skos/core#'},
                  'xsd': {'prefix_prefix': 'xsd',
                          'prefix_reference': 'http://www.w3.org/2001/XMLSchema#'}},
     'source_file': 'schema/hmd_enterprise.yaml',
     'title': 'HMD Enterprise Ontology (World Model)',
     'types': {'EnterpriseCurie': {'description': '企业实体 CURIE',
                                   'from_schema': 'https://w3id.org/asliva/biomed-ontology/enterprise',
                                   'name': 'EnterpriseCurie',
                                   'pattern': '^HMD:ENT:(DC|PRG|TGT|IND|EXP|PUB|ASY|CMP|BMK):[A-Za-z0-9_-]+$',
                                   'repr': 'str',
                                   'typeof': 'uriorcurie',
                                   'uri': 'xsd:anyURI'}}} )

class EntityTypeEnum(str, Enum):
    """
    本次 scope 内的实体类型。每个类型对应一个 HMD ID 命名空间段。 AE（不良事件）依赖 MedDRA 采购，PoC 阶段为空占位。
    """
    TARGET = "TARGET"
    """
    靶点 / 基因 / 蛋白
    """
    SUBSTANCE = "SUBSTANCE"
    """
    药物 / 物质
    """
    DISEASE = "DISEASE"
    """
    适应症 / 疾病
    """
    MECHANISM = "MECHANISM"
    """
    作用机制 MoA
    """
    MODALITY = "MODALITY"
    """
    药物模态（小分子 / 单抗 / ADC / 细胞治疗 等）
    """
    TRIAL = "TRIAL"
    """
    临床试验
    """
    BIOMARKER = "BIOMARKER"
    """
    生物标志物 / 基因变异
    """
    ADVERSE_EVENT = "ADVERSE_EVENT"
    """
    不良事件（依赖 MedDRA 采购，Track B）
    """


class SynonymScopeEnum(str, Enum):
    """
    别名范围，直接决定检索行为（设计决策 D2）。 把 related 当作等价会摧毁精确率，因此 scope 是必填字段而非可选标注。
    """
    EXACT = "EXACT"
    """
    完全等价。全权重扩展 + 写入索引
    """
    NARROW = "NARROW"
    """
    更窄的说法。降权 0.8 扩展 + 写入索引
    """
    BROAD = "BROAD"
    """
    更宽的说法。默认不扩展、不入索引，避免召回泛化
    """
    RELATED = "RELATED"
    """
    相关但不等价。永不扩展，仅作 rerank 特征
    """


class AliasTypeEnum(str, Enum):
    """
    别名的来源性质，用于消歧打分与审校优先级排序
    """
    INN = "INN"
    """
    国际非专利药品名称
    """
    BRAND = "BRAND"
    """
    商品名
    """
    INTERNAL_CODE = "INTERNAL_CODE"
    """
    企业内部研发代号，如 HMPL-504
    """
    PARTNER_CODE = "PARTNER_CODE"
    """
    合作方研发代号，如 AZD6094
    """
    RESEARCH_CODE = "RESEARCH_CODE"
    """
    通用研究代号
    """
    ABBREV = "ABBREV"
    """
    缩写，歧义高发区
    """
    SYMBOL = "SYMBOL"
    """
    基因/蛋白符号
    """
    CHEMICAL_NAME = "CHEMICAL_NAME"
    """
    化学名
    """
    TRANSLATION = "TRANSLATION"
    """
    翻译名（中文层主力类型）
    """
    MISSPELLING = "MISSPELLING"
    """
    常见拼写错误，只入索引不对外展示
    """
    LEGACY = "LEGACY"
    """
    历史废弃名称
    """


class LanguageEnum(str, Enum):
    """
    别名语言。中英必须互通，拉丁名用于化学与物种
    """
    en = "en"
    """
    English
    """
    zh = "zh"
    """
    简体中文
    """
    la = "la"
    """
    Latin
    """


class LicenseTierEnum(str, Enum):
    """
    许可分层（设计决策 D10），借鉴 UMLS SAB license category 机制。 tier 决定 RDF named graph 隔离策略、查询重写、导出闸门与训练语料准入。
    """
    TIER_0 = "TIER_0"
    """
    完全开放（MONDO / HGNC / UNII / Wikidata）。无限制
    """
    TIER_1 = "TIER_1"
    """
    署名或同源共享（ChEMBL CC-BY-SA、DrugCentral）。分发需标注，衍生物受限
    """
    TIER_2 = "TIER_2"
    """
    需订阅、内部可用（UMLS 受限源、DrugBank）。不可外泄原文
    """
    TIER_3 = "TIER_3"
    """
    严格受限（MedDRA、智慧芽 / 医药魔方原始记录）。仅授权用户可见
    """


class ReviewStatusEnum(str, Enum):
    """
    审校状态。LLM 与规则生成的内容一律以 PENDING 入库， 未经人工审校不得进入 agent 返回体（设计决策 D5）。
    """
    PENDING = "PENDING"
    """
    待审校，默认不参与对外返回
    """
    APPROVED = "APPROVED"
    """
    人工审校通过
    """
    REJECTED = "REJECTED"
    """
    审校驳回，保留用于负样本训练
    """
    AUTO_TRUSTED = "AUTO_TRUSTED"
    """
    来自权威源，免审校
    """


class MappingJustificationEnum(str, Enum):
    """
    映射与决策的依据词表（设计决策 D7）。 semapv 为标准词表，hmd: 前缀为本项目扩展。
    """
    LexicalMatching = "LexicalMatching"
    """
    词形匹配
    """
    CompositeMatching = "CompositeMatching"
    """
    组合匹配
    """
    ManualMappingCuration = "ManualMappingCuration"
    """
    人工审校
    """
    MappingChaining = "MappingChaining"
    """
    映射链传递（A=B, B=C 推出 A=C）
    """
    UnspecifiedMatching = "UnspecifiedMatching"
    """
    来源未声明依据
    """
    SemanticSimilarityThresholdMatching = "SemanticSimilarityThresholdMatching"
    """
    向量相似度过阈
    """
    OntologyDescendantExpansion = "OntologyDescendantExpansion"
    """
    本体子树扩展（hmd 扩展，需记录 depth）
    """
    LLMDisambiguation = "LLMDisambiguation"
    """
    LLM 上下文消歧（hmd 扩展，需记录 model 与 context）
    """


class PredicateEnum(str, Enum):
    """
    关系谓词，取 Biolink Model 子集。 不自造谓词 —— 与 Biolink 对齐才能让外采数据与开源图谱无损融合。
    """
    exact_match = "exact_match"
    """
    概念等价，用于构建 clique
    """
    close_match = "close_match"
    broad_match = "broad_match"
    narrow_match = "narrow_match"
    subclass_of = "subclass_of"
    inhibits = "inhibits"
    """
    抑制。属性 qualifier 记录方向与强度
    """
    treats = "treats"
    has_target = "has_target"
    biomarker_for = "biomarker_for"
    has_adverse_event = "has_adverse_event"
    in_clinical_trial_for = "in_clinical_trial_for"


class DocTypeEnum(str, Enum):
    """
    文档类型，标引分类的第一个维度，也决定用哪套解析与抽取规则。
    """
    JOURNAL_ARTICLE = "JOURNAL_ARTICLE"
    """
    期刊论文
    """
    PREPRINT = "PREPRINT"
    """
    预印本
    """
    CONFERENCE_ABSTRACT = "CONFERENCE_ABSTRACT"
    """
    会议摘要（ASCO/ESMO，管线情报的高时效来源）
    """
    CLINICAL_TRIAL_RECORD = "CLINICAL_TRIAL_RECORD"
    """
    临床试验登记记录
    """
    PATENT = "PATENT"
    """
    专利
    """
    REGULATORY_DOCUMENT = "REGULATORY_DOCUMENT"
    """
    监管文件（NMPA/FDA 审评）
    """
    LABEL = "LABEL"
    """
    药品说明书
    """
    INTERNAL_REPORT = "INTERNAL_REPORT"
    """
    企业内部报告，PoC 不涉及
    """
    INVESTIGATOR_BROCHURE = "INVESTIGATOR_BROCHURE"
    """
    研究者手册（IB）；临床开发文档，不是 CTMS 替换
    """
    CLINICAL_STUDY_REPORT = "CLINICAL_STUDY_REPORT"
    """
    临床试验报告（CSR）；文档 Pipeline 入口
    """


class ModalityChannelEnum(str, Enum):
    """
    抽取通道。三模态各自的错误模式不同，混在一起就无法分模态评估准确率， 也无法定位是解析失败还是抽取失败。
    """
    TEXT = "TEXT"
    """
    正文文本
    """
    TABLE = "TABLE"
    """
    表格（视觉模型还原结构后逐单元格对齐本体）
    """
    IMAGE = "IMAGE"
    """
    图像（KM 曲线、剂量-反应、通路图）
    """
    STRUCTURE = "STRUCTURE"
    """
    化学结构图（OCSR）
    """
    METADATA = "METADATA"
    """
    文档元数据字段
    """


class RetrievalChannelEnum(str, Enum):
    """
    召回通道。融合前必须留存，否则无法归因是哪一路把结果带进来的。
    """
    BM25 = "BM25"
    """
    词法召回
    """
    DENSE = "DENSE"
    """
    向量召回
    """
    GRAPH = "GRAPH"
    """
    图查询召回
    """
    FUSED = "FUSED"
    """
    RRF 融合后的结果
    """


class RestoreScopeEnum(str, Enum):
    """
    引用还原的范围。碎片能证明"有这句话"，但证明不了"在什么语境下说的"， 而临床结论的语境（哪一组、哪个终点、哪次随访）恰恰决定它是否成立。
    """
    SECTION = "SECTION"
    """
    所属章节全文，默认档
    """
    SIBLINGS = "SIBLINGS"
    """
    同级相邻章节，用于对照组与终点的横向比较
    """
    DOCUMENT = "DOCUMENT"
    """
    整篇文档，通常超出 agent 上下文预算
    """


class HeadingSourceEnum(str, Enum):
    """
    章节标题的判定来源。语义树的层级不是从文档里"读"出来的，是多源候选竞争后 "判"出来的 —— 记录判据才能回答"这一级标题凭什么定成 H2"， 也才能在解析质量出问题时定位到是哪一路候选失准。
    """
    TOC_EXACT = "TOC_EXACT"
    """
    PDF 内嵌目录精确命中，最可信
    """
    TOC_FUZZY = "TOC_FUZZY"
    """
    目录项与正文行模糊匹配（页码偏移、断行）
    """
    HEADING_REGEX = "HEADING_REGEX"
    """
    正文行形态匹配（编号前缀、全大写、字号跃变）
    """
    VLM_SCAN = "VLM_SCAN"
    """
    视觉模型识别的版面标题，用于扫描件与无目录 PDF
    """
    LLM_REFINE = "LLM_REFINE"
    """
    过肥叶节点经 LLM 细分后补出的中间层级
    """
    SYNTHETIC = "SYNTHETIC"
    """
    系统合成的占位层级，用于补齐跳级（H1 直接到 H3）
    """


class NormalizationStageEnum(str, Enum):
    """
    归一化级联的阶段枚举（设计决策 D7 的执行点）。 每一级的代价与可信度都不同，记录命中在哪一级才能优化级联本身。
    """
    DICTIONARY = "DICTIONARY"
    """
    精确词典匹配，最快最可信
    """
    RULE = "RULE"
    """
    规则模糊匹配（代号变体、词形）
    """
    VECTOR = "VECTOR"
    """
    向量召回
    """
    LLM = "LLM"
    """
    LLM 上下文消歧，仅在候选分差小于阈值时触发
    """
    ABSTAIN = "ABSTAIN"
    """
    放弃，产出 unmapped 信号
    """


class SignalTypeEnum(str, Enum):
    """
    演进闭环的信号类型（L8）。 信号来自真实使用，比人工巡检更能反映本体的实际缺口。
    """
    unmapped_span = "unmapped_span"
    """
    有实体形态但归一化失败的片段
    """
    low_confidence_normalization = "low_confidence_normalization"
    """
    归一化成功但置信度低
    """
    ambiguous_unstable = "ambiguous_unstable"
    """
    同一别名在不同上下文反复给出不同概念
    """
    zero_result_query = "zero_result_query"
    """
    零结果查询
    """
    expansion_miss = "expansion_miss"
    """
    扩展词集未覆盖用户实际点击的文档
    """
    negative_feedback = "negative_feedback"
    """
    agent 或用户回传的负反馈
    """
    cooccurrence_anomaly = "cooccurrence_anomaly"
    """
    共现异常，提示缺失关系
    """
    multi_source_conflict = "multi_source_conflict"
    """
    多源事实冲突，Track B 接入后成为主力信号
    """


class SignalStatusEnum(str, Enum):
    """
    信号的处理状态，审校队列按此流转。
    """
    NEW = "NEW"
    """
    新浮现
    """
    TRIAGED = "TRIAGED"
    """
    已定级
    """
    CANDIDATE_GENERATED = "CANDIDATE_GENERATED"
    """
    已生成候选变更
    """
    CURATED = "CURATED"
    """
    已审校，产出 KGCL
    """
    RELEASED = "RELEASED"
    """
    已随 release 发版
    """
    DISMISSED = "DISMISSED"
    """
    判定无需处理
    """


class EnterpriseKindEnum(str, Enum):
    DrugCandidate = "DrugCandidate"
    """
    候选药物
    """
    Program = "Program"
    """
    研发项目
    """
    Target = "Target"
    """
    靶点
    """
    Indication = "Indication"
    """
    适应症
    """
    Experiment = "Experiment"
    """
    实验/ELN 批次
    """
    Publication = "Publication"
    """
    文献/专利登记
    """
    Assay = "Assay"
    """
    检测方法
    """
    Compound = "Compound"
    """
    化合物（内部编号）
    """
    Biomarker = "Biomarker"
    """
    生物标志物
    """


class EntityStatusEnum(str, Enum):
    active = "active"
    deprecated = "deprecated"
    draft = "draft"


class ClaimStatusEnum(str, Enum):
    extracted = "extracted"
    """
    自动抽取候选；仅写 provenance，不物化 knowledge 边
    """
    validated = "validated"
    """
    经策展/规则验证；可物化为 World Model knowledge 边
    """


class ClaimPredicateEnum(str, Enum):
    targets = "targets"
    """
    候选药/化合物作用于靶点
    """
    investigates = "investigates"
    """
    研究某适应症
    """
    treats = "treats"
    """
    治疗关系（声明级，须带出处）
    """
    belongsTo = "belongsTo"
    """
    属于某项目
    """
    testedIn = "testedIn"
    """
    候选药在某 ELN 实验中被测试
    """
    hasAssay = "hasAssay"
    """
    候选药关联某 LIMS / Assay
    """
    associatedWith = "associatedWith"
    """
    靶点与适应症等关联
    """
    mentions = "mentions"
    """
    文献提及实体
    """
    supportedBy = "supportedBy"
    """
    断言被某证据条目支持（object 宜为 Evidence ID，勿倒置主体）
    """
    sameAsExternal = "sameAsExternal"
    """
    映射到外部概念
    """
    inhibits = "inhibits"
    """
    抑制（如磷酸化/通路活性）
    """
    hasActivityIn = "hasActivityIn"
    """
    在模型或疾病场景中显示活性
    """
    hasMechanism = "hasMechanism"
    """
    作用机制
    """
    inPathway = "inPathway"
    """
    参与通路
    """
    hasBiomarker = "hasBiomarker"
    """
    关联生物标志物
    """
    hasResult = "hasResult"
    """
    实验结果类断言
    """


class ProvenanceSourceTypeEnum(str, Enum):
    literature = "literature"
    patent = "patent"
    eln = "eln"
    lims = "lims"
    assay = "assay"
    manual = "manual"
    derived = "derived"



class EnterpriseEntity(ConfiguredBaseModel):
    """
    企业世界模型实体基类。主键永不复用；废弃走 replaced_by。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'abstract': True,
         'from_schema': 'https://w3id.org/asliva/biomed-ontology/enterprise',
         'slot_usage': {'enterprise_id': {'identifier': True,
                                          'name': 'enterprise_id',
                                          'required': True},
                        'entity_kind': {'name': 'entity_kind', 'required': True},
                        'preferred_label_en': {'name': 'preferred_label_en',
                                               'required': True}}})

    enterprise_id: str = Field(default=..., description="""企业实体主键，形如 HMD:ENT:DC:savolitinib""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    entity_kind: EnterpriseKindEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    preferred_label_en: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    preferred_label_zh: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    definition: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    exact_match_xrefs: Optional[list[str]] = Field(default=None, description="""与外部标准概念的 exactMatch（BIOS/ChEBI/HGNC/DrugBank/…）""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    related_xrefs: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    aliases: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    status: Optional[EntityStatusEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    created_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    replaced_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })


class DrugCandidate(EnterpriseEntity):
    """
    企业候选药物 / 管线分子（可对应多个外部 Chemical 概念）。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'class_uri': 'biolink:Drug',
         'from_schema': 'https://w3id.org/asliva/biomed-ontology/enterprise',
         'slot_usage': {'entity_kind': {'equals_string': 'DrugCandidate',
                                        'name': 'entity_kind',
                                        'range': 'string'}}})

    active_ingredient_xrefs: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DrugCandidate']} })
    targets: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DrugCandidate']} })
    indications: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DrugCandidate']} })
    program_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DrugCandidate']} })
    modality: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DrugCandidate']} })
    enterprise_id: str = Field(default=..., description="""企业实体主键，形如 HMD:ENT:DC:savolitinib""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    entity_kind: Literal["DrugCandidate"] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity'], 'equals_string': 'DrugCandidate'} })
    preferred_label_en: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    preferred_label_zh: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    definition: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    exact_match_xrefs: Optional[list[str]] = Field(default=None, description="""与外部标准概念的 exactMatch（BIOS/ChEBI/HGNC/DrugBank/…）""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    related_xrefs: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    aliases: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    status: Optional[EntityStatusEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    created_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    replaced_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })


class Program(EnterpriseEntity):
    """
    研发项目 / 管线项目。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/enterprise',
         'slot_usage': {'entity_kind': {'equals_string': 'Program',
                                        'name': 'entity_kind',
                                        'range': 'string'}}})

    therapeutic_area: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Program']} })
    enterprise_id: str = Field(default=..., description="""企业实体主键，形如 HMD:ENT:DC:savolitinib""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    entity_kind: Literal["Program"] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity'], 'equals_string': 'Program'} })
    preferred_label_en: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    preferred_label_zh: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    definition: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    exact_match_xrefs: Optional[list[str]] = Field(default=None, description="""与外部标准概念的 exactMatch（BIOS/ChEBI/HGNC/DrugBank/…）""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    related_xrefs: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    aliases: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    status: Optional[EntityStatusEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    created_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    replaced_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })


class Target(EnterpriseEntity):
    """
    企业视角的靶点实体（可 exactMatch 到基因/蛋白外部概念）。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'class_uri': 'biolink:GeneOrGeneProduct',
         'from_schema': 'https://w3id.org/asliva/biomed-ontology/enterprise',
         'slot_usage': {'entity_kind': {'equals_string': 'Target',
                                        'name': 'entity_kind',
                                        'range': 'string'}}})

    enterprise_id: str = Field(default=..., description="""企业实体主键，形如 HMD:ENT:DC:savolitinib""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    entity_kind: Literal["Target"] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity'], 'equals_string': 'Target'} })
    preferred_label_en: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    preferred_label_zh: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    definition: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    exact_match_xrefs: Optional[list[str]] = Field(default=None, description="""与外部标准概念的 exactMatch（BIOS/ChEBI/HGNC/DrugBank/…）""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    related_xrefs: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    aliases: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    status: Optional[EntityStatusEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    created_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    replaced_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })


class Indication(EnterpriseEntity):
    """
    企业适应症实体。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'class_uri': 'biolink:Disease',
         'from_schema': 'https://w3id.org/asliva/biomed-ontology/enterprise',
         'slot_usage': {'entity_kind': {'equals_string': 'Indication',
                                        'name': 'entity_kind',
                                        'range': 'string'}}})

    enterprise_id: str = Field(default=..., description="""企业实体主键，形如 HMD:ENT:DC:savolitinib""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    entity_kind: Literal["Indication"] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity'], 'equals_string': 'Indication'} })
    preferred_label_en: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    preferred_label_zh: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    definition: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    exact_match_xrefs: Optional[list[str]] = Field(default=None, description="""与外部标准概念的 exactMatch（BIOS/ChEBI/HGNC/DrugBank/…）""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    related_xrefs: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    aliases: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    status: Optional[EntityStatusEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    created_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    replaced_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })


class Experiment(EnterpriseEntity):
    """
    ELN / 内部实验批次。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/enterprise',
         'slot_usage': {'entity_kind': {'equals_string': 'Experiment',
                                        'name': 'entity_kind',
                                        'range': 'string'}}})

    candidate_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Experiment']} })
    target_ids: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Experiment']} })
    indication_ids: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Experiment']} })
    asset_fqn: Optional[str] = Field(default=None, description="""OpenMetadata 资产 fullyQualifiedName""", json_schema_extra = { "linkml_meta": {'domain_of': ['Experiment']} })
    performed_on: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Experiment']} })
    enterprise_id: str = Field(default=..., description="""企业实体主键，形如 HMD:ENT:DC:savolitinib""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    entity_kind: Literal["Experiment"] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity'], 'equals_string': 'Experiment'} })
    preferred_label_en: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    preferred_label_zh: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    definition: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    exact_match_xrefs: Optional[list[str]] = Field(default=None, description="""与外部标准概念的 exactMatch（BIOS/ChEBI/HGNC/DrugBank/…）""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    related_xrefs: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    aliases: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    status: Optional[EntityStatusEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    created_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    replaced_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })


class Publication(EnterpriseEntity):
    """
    文献或专利在企业世界模型中的登记（证据入口，非全文）。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/enterprise',
         'slot_usage': {'entity_kind': {'equals_string': 'Publication',
                                        'name': 'entity_kind',
                                        'range': 'string'}}})

    pmid: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Publication']} })
    doi: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Publication']} })
    mentions: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Publication']} })
    enterprise_id: str = Field(default=..., description="""企业实体主键，形如 HMD:ENT:DC:savolitinib""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    entity_kind: Literal["Publication"] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity'], 'equals_string': 'Publication'} })
    preferred_label_en: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    preferred_label_zh: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    definition: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    exact_match_xrefs: Optional[list[str]] = Field(default=None, description="""与外部标准概念的 exactMatch（BIOS/ChEBI/HGNC/DrugBank/…）""", json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    related_xrefs: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    aliases: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    status: Optional[EntityStatusEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    created_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })
    replaced_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['EnterpriseEntity']} })


class KnowledgeClaim(ConfiguredBaseModel):
    """
    带出处的知识断言。Knowledge ≠ Truth； Claim + Provenance + Evidence 才可被 Agent 消费。 claim_status=extracted 仅为候选；validated 才可物化为 World Model knowledge 边。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/enterprise',
         'slot_usage': {'claim_id': {'identifier': True,
                                     'name': 'claim_id',
                                     'required': True},
                        'claim_status': {'name': 'claim_status', 'required': True},
                        'predicate': {'name': 'predicate', 'required': True},
                        'subject_id': {'name': 'subject_id', 'required': True}}})

    claim_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    subject_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    predicate: ClaimPredicateEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    object_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    object_value: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    confidence: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    claim_status: ClaimStatusEnum = Field(default=..., description="""extracted=自动抽取候选；validated=企业认可的 World Model 事实""", json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    source_count: Optional[int] = Field(default=None, description="""独立来源/证据条数（多源佐证）""", json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    source_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    source_type: Optional[ProvenanceSourceTypeEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    extracted_by: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    evidence_ids: Optional[list[str]] = Field(default=None, description="""Evidence Index（Milvus）中的证据条目 ID（ev:… / pubmed:… 等）""", json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    span: Optional[str] = Field(default=None, description="""支撑 claim 的原文片段（可与 Evidence.quote 对齐）""", json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })
    created_at: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['KnowledgeClaim']} })


# Model rebuild
# see https://pydantic-docs.helpmanual.io/usage/models/#rebuilding-a-model
EnterpriseEntity.model_rebuild()
DrugCandidate.model_rebuild()
Program.model_rebuild()
Target.model_rebuild()
Indication.model_rebuild()
Experiment.model_rebuild()
Publication.model_rebuild()
KnowledgeClaim.model_rebuild()
