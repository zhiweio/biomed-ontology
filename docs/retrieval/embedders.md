# 嵌入器与权重解析

源码：`src/biomed_ontology/embed/`。

## 嵌入器矩阵

| 名称 | 列 | 用途 |
|---|---|---|
| `fake` | 确定性哈希向量 | 只验证索引/过滤/融合接线；**必须** `--allow-fake` |
| `bge-m3` / `sapbert` / `qwen3-vl` / `biomedclip` | 单列为主 | 消融单模型 |
| `dual` | 通用 + 生医 | 文本双塔 |
| `multimodal` | + 视觉（Qwen3-VL） | 四列 |
| `multimodal-bio` | + BiomedCLIP | 五列 |

索引与评测必须同一 embedder；集合 description 盖戳，对不上直接退出。

## 权重从哪来

`embed.resolve_model` 解析顺序：

```text
本地已有 → 选定源（HMD_MODEL_HUB） → Gitee 兜底
```

- 本地优先：`data/cache/models/models/<仓库名>/` —— 内网拷权重是常态  
- 兜底时**打印实际源** —— 同一代码两台机器加载不同权重却看不出来，比下载失败更危险  
- clone 前先验 `git lfs`：没装时 clone「成功」但文件是指针  

```bash
export HMD_MODEL_HUB=modelscope   # hf / modelscope / gitee
```

## BiomedCLIP 闸门

权重 MIT ≠ 可以随便部署。模型卡用途限定与「待法务核实」状态挂在 `licensing.COMPONENTS["biomedclip"]`；未 cleared 时相关路径应失败或明确告警，而不是当普通依赖 import。详见 [组件闸门](../licensing/components.md)。

## 视觉列与资产路径

视觉编码需要读到真实像素。路径必须经 `parse.assets.resolve_asset` —— 曾经 `DOC:` → 目录名未替换导致 44 张图静默变 0 张可读。见 [资产路径](../parse/assets.md)。

## 小样本陷阱

SapBERT / 视觉列在 n 很小时可以「看起来很赚」；gold 放大后净值缩一个数量级甚至翻号。读 README 时认准当前 n 与 by_lang 拆分；手册不抄具体数。

## 如何验证

```bash
uv run hmd index --embedder fake --allow-fake --recreate
uv run pytest tests/test_licensing.py -q   # 组件闸门
```
