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
     'description': '四支柱可观测模型（L7）：Trace(WHERE) / IO(WHAT) / State(WHY) / '
                    'Metrics(WHEN)。\n'
                    '这些表不是日志，是数据资产：它们既支撑排障，也是演进闭环(L8)的唯一信号来源。 埋点与 L3/L4/L5 '
                    '同步实现而非后置补 —— 后置补埋点必然漏掉决策中间态， 而中间态恰恰是"为什么给出这个答案"的答案。',
     'id': 'https://w3id.org/asliva/biomed-ontology/obs',
     'imports': ['linkml:types', 'hmd_fact'],
     'license': 'Proprietary',
     'name': 'hmd_obs',
     'prefixes': {'hmd': {'prefix_prefix': 'hmd',
                          'prefix_reference': 'https://w3id.org/asliva/biomed-ontology/'},
                  'linkml': {'prefix_prefix': 'linkml',
                             'prefix_reference': 'https://w3id.org/linkml/'},
                  'prov': {'prefix_prefix': 'prov',
                           'prefix_reference': 'http://www.w3.org/ns/prov#'}},
     'source_file': 'schema/hmd_obs.yaml',
     'title': 'HMD Observability Model'} )

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


class MetricCode(str, Enum):
    """
    受控指标口径。策展 SSOT 是 ontology/extract/table_metrics.yaml； 本枚举是 LinkML 合同。口径变更走 ontology release，禁止 prompt 私货。
    """
    ORR = "ORR"
    """
    Objective response rate
    """
    PFS = "PFS"
    """
    Progression-free survival
    """
    OS = "OS"
    """
    Overall survival
    """
    DCR = "DCR"
    """
    Disease control rate
    """
    IC50 = "IC50"
    """
    Half-maximal inhibitory concentration
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
    has_target: Optional[list[str]] = Field(default=None, description="""药物作用的靶点。与 parents 一样是概念之间的边，但它是**跨实体类型**的： parents 只在同类内部走（疾病→上位疾病），has_target 从 SUBSTANCE 跳到 TARGET。 检索期的 search-around 靠的就是这类跨类型跳转 —— 「VEGFR2 抑制剂」这种查询要从靶点反向找到药，层级扩展一步也走不到。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept']} })
    treats: Optional[list[str]] = Field(default=None, description="""药物的适应症。同为跨类型边（SUBSTANCE → DISEASE）。 与事实层同名谓词的区别在证据强度：这里是种子里的人工断言， 事实层那条是从正文抽出来、带 reifier 与出处的。两者落在不同命名图。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept']} })
    is_obsolete: Optional[bool] = Field(default=False, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept'], 'ifabsent': 'false'} })
    replaced_by: Optional[str] = Field(default=None, description="""废弃概念的唯一继任者""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept'], 'slot_uri': 'oio:replacedBy'} })
    consider: Optional[list[str]] = Field(default=None, description="""废弃概念的候选替代（非唯一，需人工判断）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Concept'], 'slot_uri': 'oio:consider'} })
    license_tier: Optional[LicenseTierEnum] = Field(default=LicenseTierEnum.TIER_0, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept',
                       'Synonym',
                       'Mapping',
                       'Document',
                       'Fact',
                       'Provenance'],
         'ifabsent': 'LicenseTierEnum(TIER_0)'} })
    created_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept', 'Fact']} })
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
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Fact', 'DecisionRecord']} })
    is_ambiguous: Optional[bool] = Field(default=False, description="""该别名映射到多个概念，禁止直接单选""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym'], 'ifabsent': 'false'} })
    review_status: Optional[ReviewStatusEnum] = Field(default=ReviewStatusEnum.PENDING, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Fact'],
         'ifabsent': 'ReviewStatusEnum(PENDING)'} })
    license_tier: Optional[LicenseTierEnum] = Field(default=LicenseTierEnum.TIER_0, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept',
                       'Synonym',
                       'Mapping',
                       'Document',
                       'Fact',
                       'Provenance'],
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
    subject_id: str = Field(default=..., description="""内部 HMD CURIE 或外部 CURIE（建团阶段两端都可能是外部 ID）""", json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping', 'Fact']} })
    subject_label: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping', 'Fact']} })
    predicate_id: PredicateEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    object_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping', 'Fact']} })
    object_label: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping', 'Fact']} })
    mapping_justification: MappingJustificationEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    mapping_tool: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    confidence: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Fact', 'DecisionRecord']} })
    author_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    mapping_date: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping']} })
    source: str = Field(default=..., description="""数据源标识，必须在 source registry 中注册""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Hierarchy']} })
    source_version: Optional[str] = Field(default=None, description="""源快照版本。缺此字段无法做离线可重放构建""", json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Hierarchy']} })
    license_tier: Optional[LicenseTierEnum] = Field(default=LicenseTierEnum.TIER_0, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept',
                       'Synonym',
                       'Mapping',
                       'Document',
                       'Fact',
                       'Provenance'],
         'ifabsent': 'LicenseTierEnum(TIER_0)'} })
    review_status: Optional[ReviewStatusEnum] = Field(default=ReviewStatusEnum.PENDING, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Fact'],
         'ifabsent': 'ReviewStatusEnum(PENDING)'} })

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
    predicate: Optional[PredicateEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Hierarchy', 'Fact']} })
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


class Document(ConfiguredBaseModel):
    """
    语料中的一篇文档。PoC 语料为 PubMed/PMC、ClinicalTrials.gov、专利。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/fact',
         'slot_usage': {'doc_id': {'identifier': True,
                                   'name': 'doc_id',
                                   'required': True},
                        'doc_type': {'name': 'doc_type', 'required': True},
                        'source_id': {'description': '必须是 registry 中已注册的源，否则许可元数据缺失。',
                                      'name': 'source_id',
                                      'required': True}}})

    doc_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Document',
                       'DocumentSection',
                       'Chunk',
                       'Evidence',
                       'Provenance']} })
    source_id: str = Field(default=..., description="""必须是 registry 中已注册的源，否则许可元数据缺失。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Document']} })
    external_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Document']} })
    title: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Document', 'Evidence']} })
    doc_type: DocTypeEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Document']} })
    published_on: Optional[date] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Document']} })
    language: Optional[LanguageEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Document']} })
    license_tier: Optional[LicenseTierEnum] = Field(default=LicenseTierEnum.TIER_0, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept',
                       'Synonym',
                       'Mapping',
                       'Document',
                       'Fact',
                       'Provenance'],
         'ifabsent': 'LicenseTierEnum(TIER_0)'} })
    retrieved_on: Optional[datetime ] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Document']} })
    labels: Optional[list[str]] = Field(default=None, description="""标引分类打的多标签，取值来自 taxonomy。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Document']} })

    @field_validator('doc_id')
    def pattern_doc_id(cls, v):
        pattern=re.compile(r"^DOC:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid doc_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid doc_id format: {v}"
            raise ValueError(err_msg)
        return v


class DocumentSection(ConfiguredBaseModel):
    """
    语义树的一个节点。文档不是切片的扁平袋子，而是有层级的 —— 检索命中的是碎片， 但研究员要看的是碎片所在的完整章节（Citationware：引用优先）。 section_path 是导航主键：碎片靠它找回兄弟节点与父章节，无需重新解析原文。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/fact',
         'slot_usage': {'doc_id': {'name': 'doc_id', 'required': True},
                        'heading_source': {'description': '该层级的判定来源。与 '
                                                          'heading_confidence '
                                                          '一起构成决策记录， 同时写入 '
                                                          'TraceContext.record_decision，使"层级怎么定的"可审计。',
                                           'name': 'heading_source'},
                        'section_id': {'identifier': True,
                                       'name': 'section_id',
                                       'required': True},
                        'section_level': {'name': 'section_level', 'required': True},
                        'section_path': {'name': 'section_path', 'required': True}}})

    section_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection', 'Chunk']} })
    doc_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Document',
                       'DocumentSection',
                       'Chunk',
                       'Evidence',
                       'Provenance']} })
    parent_section_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection']} })
    section_path: str = Field(default=..., description="""层级路径，如 \"Results / Efficacy / ORR\"。检索结果的面包屑与还原主键。""", json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection', 'Chunk']} })
    section_title: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection']} })
    section_level: int = Field(default=..., description="""1-based 层级深度。跳级会被 SYNTHETIC 层补齐，保证树无断层。""", json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection']} })
    sort_order: Optional[int] = Field(default=None, description="""同层内的阅读顺序。还原完整章节时按它重组，不依赖字典序。""", json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection', 'Chunk']} })
    start_page: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection']} })
    end_page: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection']} })
    summary: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection']} })
    heading_source: Optional[HeadingSourceEnum] = Field(default=None, description="""该层级的判定来源。与 heading_confidence 一起构成决策记录， 同时写入 TraceContext.record_decision，使\"层级怎么定的\"可审计。""", json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection']} })
    heading_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection']} })

    @field_validator('section_id')
    def pattern_section_id(cls, v):
        pattern=re.compile(r"^SEC:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid section_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid section_id format: {v}"
            raise ValueError(err_msg)
        return v

    @field_validator('doc_id')
    def pattern_doc_id(cls, v):
        pattern=re.compile(r"^DOC:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid doc_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid doc_id format: {v}"
            raise ValueError(err_msg)
        return v

    @field_validator('parent_section_id')
    def pattern_parent_section_id(cls, v):
        pattern=re.compile(r"^SEC:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid parent_section_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid parent_section_id format: {v}"
            raise ValueError(err_msg)
        return v


class Chunk(ConfiguredBaseModel):
    """
    文档切片，检索与抽取的基本单位。 保留 section 与 bbox 是硬要求：provenance 要能把研究员的视线引到原文那一段。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/fact',
         'slot_usage': {'chunk_id': {'identifier': True,
                                     'name': 'chunk_id',
                                     'required': True},
                        'concept_ids': {'description': '直接归一化命中的概念，用于精确过滤。',
                                        'name': 'concept_ids'},
                        'concept_ids_expanded': {'description': '含本体子树扩展的概念集合，用于召回。 与 '
                                                                'concept_ids 分开存是为了让"查 '
                                                                'NSCLC 召回肺腺癌"不污染精确匹配。',
                                                 'name': 'concept_ids_expanded'},
                        'degraded': {'description': '产出该切片时缺失的能力（如 formula / bbox / '
                                                    'ocr）。 一路透传到 '
                                                    'agent：与其让下游以为拿到了完整信息，不如显式声明这次少了什么。',
                                     'name': 'degraded'},
                        'doc_id': {'name': 'doc_id', 'required': True},
                        'same_as_chunk_id': {'description': '指向内容等价的属主切片。同一段正文可能同时归属多个叶节点（如跨章节的表格说明）， '
                                                            '去重后只保留一份，其余以此引用属主，避免同一证据在结果里重复占位。',
                                             'name': 'same_as_chunk_id'},
                        'text': {'name': 'text', 'required': True}}})

    chunk_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    doc_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Document',
                       'DocumentSection',
                       'Chunk',
                       'Evidence',
                       'Provenance']} })
    section: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    section_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection', 'Chunk']} })
    section_path: Optional[str] = Field(default=None, description="""层级路径，如 \"Results / Efficacy / ORR\"。检索结果的面包屑与还原主键。""", json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection', 'Chunk']} })
    sort_order: Optional[int] = Field(default=None, description="""同层内的阅读顺序。还原完整章节时按它重组，不依赖字典序。""", json_schema_extra = { "linkml_meta": {'domain_of': ['DocumentSection', 'Chunk']} })
    char_start: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    char_end: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    page: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    bbox: Optional[list[float]] = Field(default=None, description="""[x0, y0, x1, y1]，页面坐标。表格/图像抽取的溯源靠它定位。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    text: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk']} })
    modality: Optional[ModalityChannelEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Fact', 'Evidence', 'Provenance']} })
    concept_ids: Optional[list[str]] = Field(default=None, description="""直接归一化命中的概念，用于精确过滤。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk']} })
    concept_ids_expanded: Optional[list[str]] = Field(default=None, description="""含本体子树扩展的概念集合，用于召回。 与 concept_ids 分开存是为了让\"查 NSCLC 召回肺腺癌\"不污染精确匹配。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk']} })
    same_as_chunk_id: Optional[str] = Field(default=None, description="""指向内容等价的属主切片。同一段正文可能同时归属多个叶节点（如跨章节的表格说明）， 去重后只保留一份，其余以此引用属主，避免同一证据在结果里重复占位。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk']} })
    asset_path: Optional[str] = Field(default=None, description="""表格 HTML / 图像 PNG 的仓库相对路径。 绝不由文档内容拼接得出（路径穿越），一律由 doc_id + 内容哈希生成。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk']} })
    asset_summary: Optional[str] = Field(default=None, description="""视觉模型对表格/图像的文本摘要，使视觉内容可被文本查询命中。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk']} })
    asset_keywords: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk']} })
    degraded: Optional[list[str]] = Field(default=None, description="""产出该切片时缺失的能力（如 formula / bbox / ocr）。 一路透传到 agent：与其让下游以为拿到了完整信息，不如显式声明这次少了什么。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk']} })

    @field_validator('chunk_id')
    def pattern_chunk_id(cls, v):
        pattern=re.compile(r"^CHK:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid chunk_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid chunk_id format: {v}"
            raise ValueError(err_msg)
        return v

    @field_validator('doc_id')
    def pattern_doc_id(cls, v):
        pattern=re.compile(r"^DOC:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid doc_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid doc_id format: {v}"
            raise ValueError(err_msg)
        return v

    @field_validator('section_id')
    def pattern_section_id(cls, v):
        pattern=re.compile(r"^SEC:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid section_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid section_id format: {v}"
            raise ValueError(err_msg)
        return v

    @field_validator('same_as_chunk_id')
    def pattern_same_as_chunk_id(cls, v):
        pattern=re.compile(r"^CHK:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid same_as_chunk_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid same_as_chunk_id format: {v}"
            raise ValueError(err_msg)
        return v


class Fact(ConfiguredBaseModel):
    """
    一条结构化事实三元组，带语句级溯源。 RDF 落地形态是 RDF-star：<< :s :p :o >> prov:wasDerivedFrom :chunk 。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/fact',
         'slot_usage': {'evidence': {'description': '无证据的事实一律不入库。这是"事实带 provenance '
                                                    '100%"验收项的执行点。',
                                     'name': 'evidence',
                                     'required': True},
                        'fact_id': {'identifier': True,
                                    'name': 'fact_id',
                                    'required': True},
                        'object_id': {'name': 'object_id', 'range': 'ConceptCurie'},
                        'predicate': {'name': 'predicate', 'required': True},
                        'subject_id': {'description': '事实的主语必须已归一化到内部 CURIE，外部 ID '
                                                      '一律先挂靠（设计决策 D9）。',
                                       'name': 'subject_id',
                                       'range': 'ConceptCurie',
                                       'required': True}}})

    fact_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Fact']} })
    subject_id: str = Field(default=..., description="""事实的主语必须已归一化到内部 CURIE，外部 ID 一律先挂靠（设计决策 D9）。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping', 'Fact']} })
    predicate: PredicateEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Hierarchy', 'Fact']} })
    object_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping', 'Fact']} })
    object_value: Optional[str] = Field(default=None, description="""数值型客体（IC50、ORR、PFS），与 object_id 二选一。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Fact']} })
    object_unit: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Fact']} })
    qualifiers: Optional[list[str]] = Field(default=None, description="""限定条件，如 line_of_therapy=3L、population=MET_ex14。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Fact']} })
    evidence: list[Evidence] = Field(default=..., description="""无证据的事实一律不入库。这是\"事实带 provenance 100%\"验收项的执行点。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Fact']} })
    confidence: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Fact', 'DecisionRecord']} })
    extractor_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Fact']} })
    extractor_version: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Fact']} })
    review_status: Optional[ReviewStatusEnum] = Field(default=ReviewStatusEnum.PENDING, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Fact'],
         'ifabsent': 'ReviewStatusEnum(PENDING)'} })
    license_tier: Optional[LicenseTierEnum] = Field(default=LicenseTierEnum.TIER_0, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept',
                       'Synonym',
                       'Mapping',
                       'Document',
                       'Fact',
                       'Provenance'],
         'ifabsent': 'LicenseTierEnum(TIER_0)'} })
    created_in_release: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept', 'Fact']} })
    modality: Optional[ModalityChannelEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Fact', 'Evidence', 'Provenance']} })
    subject_label: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping', 'Fact']} })
    object_label: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Mapping', 'Fact']} })

    @field_validator('fact_id')
    def pattern_fact_id(cls, v):
        pattern=re.compile(r"^HMDF:\d{9}$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid fact_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid fact_id format: {v}"
            raise ValueError(err_msg)
        return v


class Evidence(ConfiguredBaseModel):
    """
    事实的出处。一条事实可有多条证据，多证据支持会提升 confidence。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/fact',
         'slot_usage': {'chunk_id': {'name': 'chunk_id', 'required': True},
                        'doc_id': {'name': 'doc_id', 'required': True}}})

    chunk_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    doc_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Document',
                       'DocumentSection',
                       'Chunk',
                       'Evidence',
                       'Provenance']} })
    section: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    char_start: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    char_end: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    page: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    bbox: Optional[list[float]] = Field(default=None, description="""[x0, y0, x1, y1]，页面坐标。表格/图像抽取的溯源靠它定位。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    modality: Optional[ModalityChannelEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Fact', 'Evidence', 'Provenance']} })
    quote: Optional[str] = Field(default=None, description="""支撑该事实的原文片段，供研究员快速核验。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Evidence']} })
    title: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Document', 'Evidence']} })

    @field_validator('chunk_id')
    def pattern_chunk_id(cls, v):
        pattern=re.compile(r"^CHK:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid chunk_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid chunk_id format: {v}"
            raise ValueError(err_msg)
        return v

    @field_validator('doc_id')
    def pattern_doc_id(cls, v):
        pattern=re.compile(r"^DOC:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid doc_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid doc_id format: {v}"
            raise ValueError(err_msg)
        return v


class Provenance(ConfiguredBaseModel):
    """
    面向 agent 返回体的溯源块（设计决策 D6），是 tool 返回体的一等公民。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/fact'})

    doc_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Document',
                       'DocumentSection',
                       'Chunk',
                       'Evidence',
                       'Provenance']} })
    chunk_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    section: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    char_start: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    char_end: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    page: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    bbox: Optional[list[float]] = Field(default=None, description="""[x0, y0, x1, y1]，页面坐标。表格/图像抽取的溯源靠它定位。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Evidence', 'Provenance']} })
    modality: Optional[ModalityChannelEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Chunk', 'Fact', 'Evidence', 'Provenance']} })
    rank_before_rerank: Optional[int] = Field(default=None, description="""rerank 前的名次。保留它才能判断 rerank 是帮忙还是帮倒忙。""", json_schema_extra = { "linkml_meta": {'domain_of': ['Provenance']} })
    retrieval_channel: Optional[RetrievalChannelEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Provenance']} })
    license_tier: Optional[LicenseTierEnum] = Field(default=LicenseTierEnum.TIER_0, json_schema_extra = { "linkml_meta": {'domain_of': ['Concept',
                       'Synonym',
                       'Mapping',
                       'Document',
                       'Fact',
                       'Provenance'],
         'ifabsent': 'LicenseTierEnum(TIER_0)'} })

    @field_validator('doc_id')
    def pattern_doc_id(cls, v):
        pattern=re.compile(r"^DOC:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid doc_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid doc_id format: {v}"
            raise ValueError(err_msg)
        return v

    @field_validator('chunk_id')
    def pattern_chunk_id(cls, v):
        pattern=re.compile(r"^CHK:[A-Za-z0-9._:-]+$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid chunk_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid chunk_id format: {v}"
            raise ValueError(err_msg)
        return v


class ToolIoRecord(ConfiguredBaseModel):
    """
    I/O 支柱（WHAT）：每次 tool 调用的完整输入输出。 全量留存的价值在于可重放与可审计；契约校验结果同表存放，违约即可告警。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/obs',
         'slot_usage': {'ontology_release_id': {'description': '不 pin release 就无法重放。这是 '
                                                               'Replay 一致性验收项的前提。',
                                                'name': 'ontology_release_id',
                                                'required': True},
                        'tool_name': {'name': 'tool_name', 'required': True},
                        'trace_id': {'name': 'trace_id', 'required': True}}})

    trace_id: str = Field(default=..., description="""随 tool 返回体回传 agent，反馈接口以它为主键（设计决策 D6）。""", json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord', 'DecisionRecord']} })
    span_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord', 'DecisionRecord']} })
    ts: Optional[datetime ] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord', 'QualityMetric']} })
    tool_name: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    tool_version: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    ontology_release_id: str = Field(default=..., description="""不 pin release 就无法重放。这是 Replay 一致性验收项的前提。""", json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord', 'QualityMetric']} })
    agent_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    session_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    input_json: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    output_json: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    latency_ms: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    status: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    error_message: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    contract_valid: Optional[bool] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    contract_errors: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    license_filtered_count: Optional[int] = Field(default=None, description="""因许可被过滤掉的条目数。持续为 0 说明过滤器可能没生效。""", json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })
    caller_entitlements: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord']} })


class DecisionRecord(ConfiguredBaseModel):
    """
    State 支柱（WHY）：一次决策的前后状态与候选集。 只记结果不记候选，就无法回答\"为什么没选那个\" —— 而这才是排障时真正要问的问题。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/obs',
         'slot_usage': {'justification': {'description': '必填。无依据的决策不可解释，直接违反"决策可解释率 '
                                                         '100%"验收项。',
                                          'name': 'justification',
                                          'required': True},
                        'stage': {'name': 'stage', 'required': True},
                        'step_seq': {'name': 'step_seq', 'required': True},
                        'trace_id': {'name': 'trace_id', 'required': True}}})

    trace_id: str = Field(default=..., description="""随 tool 返回体回传 agent，反馈接口以它为主键（设计决策 D6）。""", json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord', 'DecisionRecord']} })
    span_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord', 'DecisionRecord']} })
    step_seq: int = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord']} })
    stage: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord', 'Candidate']} })
    state_before: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord']} })
    state_after: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord']} })
    candidates: Optional[list[Candidate]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord']} })
    chosen: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord']} })
    justification: MappingJustificationEnum = Field(default=..., description="""必填。无依据的决策不可解释，直接违反\"决策可解释率 100%\"验收项。""", json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord']} })
    rule_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord']} })
    model_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord']} })
    confidence: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Synonym', 'Mapping', 'Fact', 'DecisionRecord']} })
    elapsed_ms: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord']} })


class Candidate(ConfiguredBaseModel):
    """
    决策过程中的一个候选项。保留 score 与 channel 才能做通道级归因。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/obs'})

    candidate_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Candidate']} })
    label: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Candidate']} })
    score: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Candidate']} })
    channel: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Candidate']} })
    stage: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['DecisionRecord', 'Candidate']} })


class Signal(ConfiguredBaseModel):
    """
    演进闭环的信号（L8）。 priority_score = 影响 query 数 × 影响文档数，审校队列按它排序 —— 本体缺口的价值不均等，按影响面排序才能让有限的审校人力用在刀刃上。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/obs',
         'slot_usage': {'signal_id': {'identifier': True,
                                      'name': 'signal_id',
                                      'required': True},
                        'signal_type': {'name': 'signal_type', 'required': True}}})

    signal_id: str = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })
    signal_type: SignalTypeEnum = Field(default=..., json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })
    payload: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })
    first_seen: Optional[datetime ] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })
    last_seen: Optional[datetime ] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })
    freq: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })
    impacted_queries: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })
    impacted_docs: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })
    priority_score: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })
    signal_status: Optional[SignalStatusEnum] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })
    linked_trace_ids: Optional[list[str]] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['Signal']} })

    @field_validator('signal_id')
    def pattern_signal_id(cls, v):
        pattern=re.compile(r"^HMDS:\d{9}$")
        if isinstance(v, list):
            for element in v:
                if isinstance(element, str) and not pattern.match(element):
                    err_msg = f"Invalid signal_id format: {element}"
                    raise ValueError(err_msg)
        elif isinstance(v, str) and not pattern.match(v):
            err_msg = f"Invalid signal_id format: {v}"
            raise ValueError(err_msg)
        return v


class QualityMetric(ConfiguredBaseModel):
    """
    Metrics 支柱（WHEN）的一条质量指标观测。 与 release 绑定，才能算出\"较上版下降多少\"这个发版守门的判据。
    """
    linkml_meta: ClassVar[LinkMLMeta] = LinkMLMeta({'from_schema': 'https://w3id.org/asliva/biomed-ontology/obs'})

    metric_name: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['QualityMetric']} })
    metric_value: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['QualityMetric']} })
    ontology_release_id: Optional[str] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord', 'QualityMetric']} })
    ts: Optional[datetime ] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['ToolIoRecord', 'QualityMetric']} })
    metric_dimension: Optional[str] = Field(default=None, description="""指标的切片维度（实体类型 / 语言 / 模态）。不分维度的总体准确率会掩盖局部崩塌。""", json_schema_extra = { "linkml_meta": {'domain_of': ['QualityMetric']} })
    sample_size: Optional[int] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['QualityMetric']} })
    threshold: Optional[float] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['QualityMetric']} })
    passed: Optional[bool] = Field(default=None, json_schema_extra = { "linkml_meta": {'domain_of': ['QualityMetric']} })


# Model rebuild
# see https://pydantic-docs.helpmanual.io/usage/models/#rebuilding-a-model
Concept.model_rebuild()
Synonym.model_rebuild()
Mapping.model_rebuild()
Hierarchy.model_rebuild()
Clique.model_rebuild()
Document.model_rebuild()
DocumentSection.model_rebuild()
Chunk.model_rebuild()
Fact.model_rebuild()
Evidence.model_rebuild()
Provenance.model_rebuild()
ToolIoRecord.model_rebuild()
DecisionRecord.model_rebuild()
Candidate.model_rebuild()
Signal.model_rebuild()
QualityMetric.model_rebuild()
