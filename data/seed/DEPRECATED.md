# DEPRECATED — `data/seed/` 已退役

身份与术语权威迁至：

- [`ontology/catalog/`](../../ontology/catalog/) — 文献/检索 ENT 目录（`HMD:ENT:*`）
- [`ontology/entities/`](../../ontology/entities/) — 企业世界模型金路径实体
- [`ontology/dictionary/`](../../ontology/dictionary/) — ER Exact Dictionary
- Entity Resolution：BERN2 → Dictionary → Zingg → BIOS/xref → `HMD:ENT:*`

本目录仅作迁移对照与 `id_mode=ledger` 单测输入，**不得**再作为运行时身份权威。
新概念请写入 `ontology/catalog/`（或金路径 `ontology/entities/`）。
