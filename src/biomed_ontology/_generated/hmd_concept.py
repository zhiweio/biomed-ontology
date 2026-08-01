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
     'description': '术语层核心模型（L1）。 概念的主键是内部 CURIE 而非外部 ID（设计决策 D1）—— 外部本体的 obsolete '
                    '/ merge 是常态，用外部 ID 当主键会让下游索引与历史报告一起崩。',
     'id': 'https://w3id.org/asliva/biomed-ontology/concept',
     'imports': ['linkml:types', 'hmd_types'],
     'license': 'Proprietary',
     'name': 'hmd_concept',
     'prefixes': {'biolink': {'prefix_prefix': 'biolink',
                              'prefix_reference': 'https://w3id.org/biolink/vocab/'},
                  'dcterms': {'prefix_prefix': 'dcterms',
                              'prefix_reference': 'http://purl.org/dc/terms/'},
                  'hmd': {'prefix_prefix': 'hmd',
                          'prefix_reference': 'https://w3id.org/asliva/biomed-ontology/'},
                  'linkml': {'prefix_prefix': 'linkml',
                             'prefix_reference': 'https://w3id.org/linkml/'},
                  'oio': {'prefix_prefix': 'oio',
                          'prefix_reference': 'http://www.geneontology.org/formats/oboInOwl#'},
                  'prov': {'prefix_prefix': 'prov',
                           'prefix_reference': 'http://www.w3.org/ns/prov#'},
                  'skos': {'prefix_prefix': 'skos',
                           'prefix_reference': 'http://www.w3.org/2004/02/skos/core#'},
                  'sssom': {'prefix_prefix': 'sssom',
                            'prefix_reference': 'https://w3id.org/sssom/'}},
     'source_file': 'schema/hmd_concept.yaml',
     'title': 'HMD Concept and Alias Model'} )

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



class Concept(ConfiguredBaseModel):
    """
    一个生物医药概念的唯一锚点。所有别名、外部 ID、抽取事实、索引条目都挂在这个 ID 上。 ID 单调递增、永不复用；废弃走 replaced_by / consider 而不是删除。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'class_uri': 'skos:Concept',
         'from_schema': 'https://w3id.org/asliva/biomed-ontology/concept',
         'slot_usage': {'concept_id': {'identifier': True,
                                       'name': 'concept_id',
                                       'required': True},
                        'entity_type': {'name': 'entity_type', 'required': True},
                        'preferred_label_en': {'name': 'preferred_label_en',
                                               'required': True},
                        'primary_xref': {'description': '等价团的代表 ID，选取规则见 '
                                                        'ontology/clique.py。 '
                                                        '仅作展示与回溯用途，不是主键。',
                                         'name': 'primary_xref'}},
         'unique_keys': {'primary_xref_unique': {'description': '一个外部 ID 不能同时是两个概念的主 '
                                                                'xref，否则说明等价团合并有误',
                                                 'unique_key_name': 'primary_xref_unique',
                                                 'unique_key_slots': ['primary_xref']}}})

    concept_id: str = Field(default=..., description="""内部概念 CURIE""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept', 'Synonym', 'Clique'], 'slot_uri': 'skos:notation'} })
    entity_type: EntityTypeEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Concept']} })
    category: Optional[list[str]] = Field(default=None, description="""Biolink Model 类别，用于与外部图谱对齐""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept']} })
    preferred_label_en: str = Field(default=..., description="""英文首选标签""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept'], 'slot_uri': 'skos:prefLabel'} })
    preferred_label_zh: Optional[str] = Field(default=None, description="""中文首选标签。中文层是自建增量（D5）—— 开放许可源的中文覆盖不足，LLM 生成的候选必须人工审校后才能升为 preferred。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept'], 'slot_uri': 'skos:prefLabel'} })
    definition: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept'], 'slot_uri': 'skos:definition'} })
    primary_xref: Optional[str] = Field(default=None, description="""等价团的代表 ID，选取规则见 ontology/clique.py。 仅作展示与回溯用途，不是主键。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept', 'Clique']} })
    equivalent_xrefs: Optional[list[str]] = Field(default=None, description="""等价团其余成员，含外采数据的 vendor ID""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept'], 'slot_uri': 'skos:exactMatch'} })
    parents: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept']} })
    is_obsolete: Optional[bool] = Field(default=False, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept'], 'ifabsent': 'false'} })
    replaced_by: Optional[str] = Field(default=None, description="""废弃概念的唯一继任者""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept'], 'slot_uri': 'oio:replacedBy'} })
    consider: Optional[list[str]] = Field(default=None, description="""废弃概念的候选替代（非唯一，需人工判断）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept'], 'slot_uri': 'oio:consider'} })
    license_tier: Optional[LicenseTierEnum] = Field(default=LicenseTierEnum.TIER_0, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept', 'Synonym', 'Mapping'],
         'ifabsent': 'LicenseTierEnum(TIER_0)'} })
    created_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept']} })
    modified_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept']} })


class Synonym(ConfiguredBaseModel):
    """
    别名条目。scope 必填 —— 检索扩展行为完全由 scope 驱动（D2）。 is_ambiguous 为真时，该别名不得直接单选，必须走上下文消歧（D3）。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/concept',
         'slot_usage': {'alias_id': {'identifier': True,
                                     'name': 'alias_id',
                                     'required': True},
                        'alias_norm': {'description': '归一化形式：小写、去连字符与空白、希腊字母转写、全角转半角。 '
                                                      '词典精确匹配走这一列，alias_raw 只用于展示与溯源。',
                                       'name': 'alias_norm',
                                       'required': True},
                        'alias_raw': {'name': 'alias_raw', 'required': True},
                        'concept_id': {'name': 'concept_id', 'required': True},
                        'confidence': {'maximum_value': 1.0,
                                       'minimum_value': 0.0,
                                       'name': 'confidence',
                                       'range': 'float'},
                        'lang': {'name': 'lang', 'required': True},
                        'scope': {'name': 'scope', 'required': True}}})

    alias_id: str = Field(default=..., description="""别名条目的稳定 ID。可观测埋点回传的就是这个 ID —— 归因排障时要能从一条错误结论定位到具体哪一行别名（Demo 场景 4）。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym']} })
    concept_id: str = Field(default=..., description="""内部概念 CURIE""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept', 'Synonym', 'Clique'], 'slot_uri': 'skos:notation'} })
    alias_raw: str = Field(default=..., description="""原始写法，保留大小写、连字符与空白""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym']} })
    alias_norm: str = Field(default=..., description="""归一化形式：小写、去连字符与空白、希腊字母转写、全角转半角。 词典精确匹配走这一列，alias_raw 只用于展示与溯源。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym']} })
    lang: LanguageEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym']} })
    scope: SynonymScopeEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym']} })
    alias_type: Optional[AliasTypeEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym']} })
    source: str = Field(default=..., description="""数据源标识，必须在 source registry 中注册""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Hierarchy']} })
    source_version: Optional[str] = Field(default=None, description="""源快照版本。缺此字段无法做离线可重放构建""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Hierarchy']} })
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping']} })
    is_ambiguous: Optional[bool] = Field(default=False, description="""该别名映射到多个概念，禁止直接单选""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym'], 'ifabsent': 'false'} })
    review_status: Optional[ReviewStatusEnum] = Field(default=ReviewStatusEnum.PENDING, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping'], 'ifabsent': 'ReviewStatusEnum(PENDING)'} })
    license_tier: Optional[LicenseTierEnum] = Field(default=LicenseTierEnum.TIER_0, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept', 'Synonym', 'Mapping'],
         'ifabsent': 'LicenseTierEnum(TIER_0)'} })
    valid_from: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym']} })
    valid_to: Optional[date] = Field(default=None, description="""为空表示当前有效""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym']} })

    @field_validator('alias_id')
    def pattern_alias_id(cls, v):
        pattern=re.compile(r"^HMDA:\d{9}$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid alias_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid alias_id format: {v}"
            raise ValueError(err_msg)
        return v


class Mapping(ConfiguredBaseModel):
    """
    SSSOM 映射记录。外采数据（UMLS / DrugBank / 智慧芽 / 医药魔方） 一律通过此表挂靠到内部 CURIE，而非替代内部 ID（设计决策 D9）。 这让底座保持供应商中立：停订阅只失去一组 xref，内部 ID 与历史报告不受影响。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'class_uri': 'sssom:Mapping',
         'from_schema': 'https://w3id.org/asliva/biomed-ontology/concept',
         'slot_usage': {'mapping_id': {'identifier': True, 'name': 'mapping_id'},
                        'mapping_justification': {'name': 'mapping_justification',
                                                  'required': True},
                        'object_id': {'name': 'object_id', 'required': True},
                        'predicate_id': {'name': 'predicate_id', 'required': True},
                        'subject_id': {'description': '内部 HMD CURIE 或外部 '
                                                      'CURIE（建团阶段两端都可能是外部 ID）',
                                       'name': 'subject_id',
                                       'required': True}}})

    mapping_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    subject_id: str = Field(default=..., description="""内部 HMD CURIE 或外部 CURIE（建团阶段两端都可能是外部 ID）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    subject_label: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    predicate_id: PredicateEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    object_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    object_label: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    mapping_justification: MappingJustificationEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    mapping_tool: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    confidence: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping']} })
    author_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    mapping_date: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    source: str = Field(default=..., description="""数据源标识，必须在 source registry 中注册""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Hierarchy']} })
    source_version: Optional[str] = Field(default=None, description="""源快照版本。缺此字段无法做离线可重放构建""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Hierarchy']} })
    license_tier: Optional[LicenseTierEnum] = Field(default=LicenseTierEnum.TIER_0, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept', 'Synonym', 'Mapping'],
         'ifabsent': 'LicenseTierEnum(TIER_0)'} })
    review_status: Optional[ReviewStatusEnum] = Field(default=ReviewStatusEnum.PENDING, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping'], 'ifabsent': 'ReviewStatusEnum(PENDING)'} })

    @field_validator('mapping_id')
    def pattern_mapping_id(cls, v):
        pattern=re.compile(r"^HMDM:\d{9}$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid mapping_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid mapping_id format: {v}"
            raise ValueError(err_msg)
        return v


class Hierarchy(ConfiguredBaseModel):
    """
    概念层级边。检索时的子树扩展依赖这张表 （查 NSCLC 要能召回 MET ex14 skipping 肺腺癌）。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/concept',
         'slot_usage': {'child_id': {'name': 'child_id', 'required': True},
                        'parent_id': {'name': 'parent_id', 'required': True}}})

    child_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Hierarchy']} })
    parent_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Hierarchy']} })
    predicate: Optional[PredicateEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Hierarchy']} })
    source: str = Field(default=..., description="""数据源标识，必须在 source registry 中注册""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Hierarchy']} })
    source_version: Optional[str] = Field(default=None, description="""源快照版本。缺此字段无法做离线可重放构建""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Hierarchy']} })


class Clique(ConfiguredBaseModel):
    """
    等价团：一组通过 exact_match 互相连通的外部 ID 集合，对应唯一一个内部概念。 建团用连通分量算法，冲突（同团内出现两个来自同一权威源的不同 ID）进人工队列。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/concept',
         'slot_usage': {'concept_id': {'identifier': True, 'name': 'concept_id'},
                        'members': {'name': 'members', 'required': True}}})

    concept_id: str = Field(default=..., description="""内部概念 CURIE""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept', 'Synonym', 'Clique'], 'slot_uri': 'skos:notation'} })
    members: list[str] = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Clique']} })
    primary_xref: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept', 'Clique']} })
    conflict_flags: Optional[list[str]] = Field(default=None, description="""建团冲突说明，非空即需人工介入""", json_schema_extra = { "linkml_meta": {'domain_of': ['Clique']} })
    built_by_justification: Optional[MappingJustificationEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Clique']} })


# Model rebuild
# see https://pydantic-docs.helpmanual.io/usage/models/#rebuilding-a-model
Concept.model_rebuild()
Synonym.model_rebuild()
Mapping.model_rebuild()
Hierarchy.model_rebuild()
Clique.model_rebuild()
