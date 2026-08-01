# LinkML schema 是模型的单一事实来源；Python / JSON Schema / SHACL / OWL 全部由它生成。
# 生成产物纳入版本控制，以便 CI 检出即可运行，无需先跑代码生成。

SCHEMA_DIR := schema
GEN_PY := src/biomed_ontology/_generated
GEN_ART := schema/generated
# hmd_types 只被 import，不单独生成。
SCHEMAS := hmd_concept hmd_fact hmd_taxonomy hmd_obs hmd_agentapi
COMPOSE := docker compose -f docker/milvus-standalone.yml

.PHONY: all gen gen-py gen-jsonschema gen-shacl gen-owl lint test check clean \
        milvus-up milvus-down milvus-logs corpus

all: gen lint test

gen: gen-py gen-jsonschema gen-shacl gen-owl

gen-py:
	@mkdir -p $(GEN_PY)
	@touch $(GEN_PY)/__init__.py
	@for s in $(SCHEMAS); do \
		echo "gen-pydantic $$s"; \
		uv run gen-pydantic $(SCHEMA_DIR)/$$s.yaml > $(GEN_PY)/$$s.py; \
	done

gen-jsonschema:
	@mkdir -p $(GEN_ART)
	@for s in $(SCHEMAS); do \
		echo "gen-json-schema $$s"; \
		uv run gen-json-schema $(SCHEMA_DIR)/$$s.yaml > $(GEN_ART)/$$s.schema.json; \
	done

# SHACL 用于 RDF 三元组库侧的约束校验，JSON Schema 用于 agent 工具 I/O 契约校验。
# 两者必须同源，否则图侧和接口侧会各自漂移。
gen-shacl:
	@mkdir -p $(GEN_ART)
	@for s in $(SCHEMAS); do \
		echo "gen-shacl $$s"; \
		uv run gen-shacl $(SCHEMA_DIR)/$$s.yaml > $(GEN_ART)/$$s.shacl.ttl; \
	done

gen-owl:
	@mkdir -p $(GEN_ART)
	@for s in $(SCHEMAS); do \
		echo "gen-owl $$s"; \
		PYTHONWARNINGS=ignore::DeprecationWarning uv run gen-owl $(SCHEMA_DIR)/$$s.yaml > $(GEN_ART)/$$s.owl.ttl; \
	done

lint:
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

fmt:
	uv run ruff format src tests scripts
	uv run ruff check --fix src tests scripts

test:
	uv run pytest

check: lint test

# Milvus 只在需要向量后端时起；默认 HMD_SEARCH_BACKEND=local，不装 Docker 也能跑全套测试。
milvus-up:
	$(COMPOSE) up -d
	@echo "Milvus: http://localhost:19530  (make milvus-logs 看启动进度，首次约 1 分钟)"

milvus-down:
	$(COMPOSE) down

milvus-logs:
	$(COMPOSE) logs -f standalone

# 真实语料不入 git（体积 + 许可），只提交下载脚本与 SOURCES.md。
corpus:
	uv run python scripts/fetch_corpus.py

clean:
	rm -rf $(GEN_ART) $(GEN_PY) .pytest_cache .ruff_cache
