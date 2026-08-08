# 目录地图

```text
biomed-ontology/
├── README.md                 # 命令 + 实测数字（测试绊线）
├── Taskfile.yml              # 统一任务入口（替代 Makefile）
├── NOTICE                    # 出处与许可义务
├── mkdocs.yml                # 本手册
├── docs/                     # 手册源码
├── schema/                   # LinkML SSOT（含 hmd_enterprise）
├── ontology/                 # Ontology-as-Code 策展面（非 SSOT）
│   ├── mappings/             # BIOS / BERN2 / ChEBI
│   ├── owl/ + shapes/        # Protégé / SHACL 入口说明
│   └── examples/golden_path/ # HMPL-504 金路径样例
├── data/
│   ├── seed/                 # 概念与歧义登记
│   ├── foundation/           # 企业实体 / 词典 / claims / evidence / BIOS 子集
│   ├── corpus/ + parsed/     # 语料 YAML
│   ├── gold/                 # 评测与 targets
│   ├── registry/             # 源与采购
│   ├── assets/               # 渲染图块
│   └── cache/                # BIOS / 模型权重缓存
├── docker/
│   ├── milvus-standalone.yml
│   ├── docker-compose.foundation.yml
│   ├── bern2/
│   └── secrets/              # graphdb.license（gitignore）
├── scripts/
└── src/biomed_ontology/
    ├── pipeline.py           # KB 装配
    ├── foundation/           # World Model + Semantic API
    ├── ingest/               # 种子构建
    ├── ontology/             # links / rdf / ids
    ├── normalize/ + alias/
    ├── parse/ + corpus/
    ├── search/ + embed/ + rerank/
    ├── tools/ + service/     # hmd serve（Semantic Access + Foundation Ops）
    ├── licensing.py
    ├── observability/ + quality/ + evolution/
    ├── eval/
    └── _generated/           # task gen 产物（含 hmd_enterprise）
```

包 ↔ 层对照见 [分层架构](../architecture/layers.md)；世界模型见 [Foundation](../architecture/foundation.md)。
