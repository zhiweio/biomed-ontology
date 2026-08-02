# 目录地图

```text
biomed-ontology/
├── README.md                 # 命令 + 实测数字（测试绊线）
├── NOTICE                    # 出处与许可义务
├── mkdocs.yml                # 本手册
├── docs/                     # 手册源码
├── schema/                   # LinkML SSOT
├── data/
│   ├── seed/                 # 概念与歧义登记
│   ├── corpus/ + parsed/     # 语料 YAML
│   ├── gold/                 # 评测与 targets
│   ├── registry/             # 源与采购
│   ├── assets/               # 渲染图块
│   └── cache/models/         # 本地权重
├── docker/milvus-standalone.yml
├── scripts/dump_sections.py
└── src/biomed_ontology/
    ├── pipeline.py           # KB 装配
    ├── ingest/               # 种子构建
    ├── ontology/             # links / rdf / ids
    ├── normalize/ + alias/
    ├── parse/ + corpus/
    ├── search/ + embed/ + rerank/
    ├── agentapi/ + service/
    ├── licensing.py
    ├── observability/ + quality/ + evolution/
    ├── eval/
    └── _generated/           # make gen 产物
```

包 ↔ 层对照见 [分层架构](../architecture/layers.md)。
