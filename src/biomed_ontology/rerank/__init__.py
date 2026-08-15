"""交叉编码器精排：融合出候选，重排定顺序。

双塔嵌入把 query 与 passage 各自编码成一个向量再比距离 —— 两侧从头到尾
没有见过对方。交叉编码器把两者拼成一条序列一起过 transformer，
每一个 query token 都能注意到每一个 passage token，于是能判断
"这段确实在回答这个问题"而不只是"这段和这个问题谈的是一件事"。
代价是不能预计算：候选有多少条就要前向多少次，所以它只配跑在候选池上。

在这个仓库里精排要修的是一个具体的毛病：RRF 用名次融合，而名次只表达
"在本通道内排第几"，不表达"到底有多相关"。三个通道各自的第 3 名进了融合，
谁该在最终榜单上更靠前，RRF 没有任何依据 —— 它只能按通道数投票。
精排给的就是那个缺失的依据。

模块形态照抄 `embed/`：Protocol + 真实现 + Null 实现 + `resolve_model` 走镜像。
`NullReranker` 不是占位符 —— 它让"没开精排"成为一个显式的、能被报表打印出来的
状态，而不是一个 `if reranker is not None` 的隐式分支。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

__all__ = [
    "REAL_RERANKERS",
    "BgeReranker",
    "NullReranker",
    "Reranker",
    "get_reranker",
]


@runtime_checkable
class Reranker(Protocol):
    name: str

    def rescore(self, query: str, passages: list[str]) -> list[float]: ...


class NullReranker:
    """恒等重排：原样返回融合名次。

    分数取的是**递减序列**而不是全零：全零会让下游的稳定排序退化成
    按 chunk_id 排 —— 这个仓库刚在图通道上吃过一次这个亏。
    """

    name = "null"

    def rescore(self, query: str, passages: list[str]) -> list[float]:
        return [1.0 - i / max(len(passages), 1) for i in range(len(passages))]


class BgeReranker:
    """BAAI/bge-reranker-v2-m3（Apache-2.0，568M，多语言）。

    选它而不是 `bge-reranker-v2-gemma` / `-minicpm-layerwise`：后两者是
    2.5B/2.7B 的 LLM-based reranker，效果更好但权重 10GB 量级，
    在这个只有 588 个切片的 PoC 上换不回相应的收益。

    选它而不是 `bge-reranker-base/large`：那两个是中英双语，
    而这份 gold 有 9 条中文 query 要跨语言命中英文文献 —— v2-m3 与 BGE-M3
    同底座，跨语言对齐是它明确训练过的能力。

    分数过 sigmoid 压到 [0,1]。原始 logit 跨 query 不可比
    （同一个 -5 在不同 query 上含义不同），而报表里要打印这个分数。

    **不走 `FlagEmbedding.FlagReranker`**，尽管 `FlagEmbedding` 已在依赖里：
    它的 `compute_score` 依赖 `tokenizer.prepare_for_model()` 来做
    "只截断 passage、保留完整 query"的拼接，而 transformers 5.x 已经把这个方法
    从 `PreTrainedTokenizerBase` 上删掉了，调用当场 `AttributeError`。
    这里的等价写法是 `tokenizer(text=query, text_pair=passage,
    truncation="only_second")` —— 同一套截断语义，走的是公开且稳定的 API，
    也就不必为了一个包去把 `transformers` 钉回旧版本。
    """

    name = "bge-reranker-v2-m3"

    def __init__(
        self,
        *,
        model_id: str = "BAAI/bge-reranker-v2-m3",
        device: str | None = None,
        use_fp16: bool = False,
        batch_size: int = 16,
        max_length: int = 512,
    ) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        from biomed_ontology.embed import best_device, resolve_model

        path = resolve_model(model_id)
        self.device = device or best_device()
        self._torch = torch
        self._batch_size = batch_size
        self._max_length = max_length
        self._tok: Any = AutoTokenizer.from_pretrained(path, local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True)
        # fp16 在 CPU 上不但不快，部分算子还会直接报错。
        if use_fp16 and self.device != "cpu":
            model = model.half()
        self._model = model.to(self.device).eval()

    def rescore(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        torch = self._torch
        out: list[float] = []
        for start in range(0, len(passages), self._batch_size):
            batch = passages[start : start + self._batch_size]
            toks = self._tok(
                text=[query] * len(batch),
                text_pair=batch,
                padding=True,
                # only_second：query 必须完整保留，超长时砍 passage 的尾巴。
                # 默认的 longest_first 会在长 passage 上把 query 也一起截了。
                truncation="only_second",
                max_length=self._max_length,
                return_tensors="pt",
            ).to(self.device)
            with torch.inference_mode():
                logits = self._model(**toks).logits.view(-1).float()
            out.extend(torch.sigmoid(logits).cpu().tolist())
        return out


REAL_RERANKERS = ("bge-reranker-v2-m3",)
"""可用于对外报数的精排模型。`null` 不在其中 —— 它根本没有重排。"""


def get_reranker(name: str = "null", *, device: str | None = None) -> Reranker:
    if name in {"null", "", "none"}:
        return NullReranker()
    if name == "bge-reranker-v2-m3":
        return BgeReranker(device=device)
    raise ValueError(f"未知 reranker：{name!r}")
