"""给图打类型标签：CT 影像、病理镜检、生存曲线、流程图……

存在的理由是一个实测到的缺陷。README 里那个 "chest CT scan showing a pulmonary
nodule" 的查询，加上 `modalities=[IMAGE]` 之后首位仍然是一张"Top 30 PT 信号强度"
柱状图 —— 过滤保证了返回的是图，却完全没有保证是**那一类**图。
向量相似度在这件事上不可靠：一张 CT 和一张柱状图在图文空间里的距离，
主要由 caption 里的词决定，而 caption 常常两句话都不提图型。

图型是个布尔条件，和 `modality` 同一性质，所以处置也相同 —— 落成标量字段下推，
而不是往分数里掺一个说不清的偏置项。

判定走 BiomedCLIP 零样本：它的训练集 PMC-15M 就是 PMC OA 的图-caption 对，
判别本领域的图型正是它的主场（官方示例上 9/9 命中）。caption 关键词做兜底 ——
不是因为它更准，而是因为**没有 GPU / 没下权重时这条路必须还能走**，
否则 `figure_type` 会在半数环境里恒为空，那比没有这个字段更糟。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "FIGURE_TYPES",
    "BiomedClipFigureTyper",
    "FigureTyping",
    "KeywordFigureTyper",
    "get_figure_typer",
    "type_from_caption",
]

# 图型集合。刻意保持粗粒度：这些是**检索意图**能区分的类别，
# 不是放射科的分类体系。细到"轴位增强 CT"就没有任何一条 query 用得上，
# 却会让零样本分类在几个近义标签之间反复横跳。
FIGURE_TYPES = (
    "RADIOLOGY",  # CT / MRI / X 光 / PET
    "MICROSCOPY",  # H&E / IHC / 荧光镜检
    "GROSS_PATHOLOGY",  # 大体标本照片
    "CHART",  # KM 曲线 / 瀑布图 / 森林图 / 柱状图
    "GEL_BLOT",  # western blot / 凝胶电泳
    "DIAGRAM",  # CONSORT 流程图 / 通路图 / 示意图
    "TABLE_IMAGE",  # 被渲染成图的表
    "OTHER",
)

# 零样本用的提示语。每类给多条：单条提示的措辞会主导结果，
# 而"这张图是什么"这个问题在文献插图上有很多种问法。取同类里的最高分。
_PROMPTS: dict[str, tuple[str, ...]] = {
    "RADIOLOGY": (
        "a computed tomography CT scan",
        "a magnetic resonance MRI scan",
        "a chest X-ray radiograph",
        "a PET scan of a patient",
    ),
    "MICROSCOPY": (
        "a hematoxylin and eosin stained histopathology slide",
        "an immunohistochemistry stained tissue section",
        "a microscopy image of cells",
    ),
    # 与 MICROSCOPY 分开是被实测逼出来的：语料里那张切除标本的照片
    # 在只有 MICROSCOPY 可选时被判成了镜检（0.356）。两者都是"组织的样子"，
    # 但一个是肉眼、一个是显微镜下，检索意图完全不同。
    "GROSS_PATHOLOGY": (
        "a gross pathology photograph of a resected surgical specimen",
        "a macroscopic photograph of an excised tumor on a surgical drape",
    ),
    "CHART": (
        "a Kaplan-Meier survival curve plot",
        "a bar chart of statistics",
        "a line chart of measurements over time",
        "a forest plot of hazard ratios",
        "a scatter plot with data points",
    ),
    "GEL_BLOT": (
        "a western blot showing protein bands",
        "a gel electrophoresis image",
    ),
    "DIAGRAM": (
        "a CONSORT patient flow diagram",
        "a schematic diagram of a signaling pathway",
        "a flowchart of a study design",
    ),
    # 表格常常整格都是句子而不是数字（试验方案、纳入标准）。只写"数字表格"
    # 会让这类表输给 OTHER 里的"带文字的期刊页面"—— 实测就是这么丢的。
    "TABLE_IMAGE": (
        "a table of numbers with rows and columns",
        "a table with rows and columns of text in shaded cells",
        "a screenshot of a data table",
    ),
    "OTHER": (
        "a photograph of laboratory equipment",
        "a paragraph of running text from a journal article",
    ),
}

# caption 兜底。顺序即优先级：先判最专的类，`figure` / `chart` 这类通用词垫底，
# 否则 "Figure 3. CT scan..." 会先被 DIAGRAM 的 "figure" 抓走。
_CAPTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "RADIOLOGY",
        re.compile(
            r"\b(ct|computed tomograph\w*|mri|magnetic resonance|x-?ray|radiograph\w*|pet[- /]ct"
            r"|ultrasound|sonograph\w*)\b|CT\s*(影像|扫描|图像)|磁共振|核磁|X\s*线|超声",
            re.IGNORECASE,
        ),
    ),
    (
        "GROSS_PATHOLOGY",
        re.compile(
            r"\b(gross (specimen|patholog\w*|appearance)|resected specimen|surgical specimen"
            r"|macroscopic)\b|大体标本|切除标本",
            re.IGNORECASE,
        ),
    ),
    (
        "MICROSCOPY",
        re.compile(
            r"\b(h\s*&\s*e|haematoxylin|hematoxylin|immunohistochem\w*|ihc\b|histopatholog\w*"
            r"|histolog\w*|micrograph\w*|microscop\w*|staining)\b|免疫组化|组织学|镜检|染色",
            re.IGNORECASE,
        ),
    ),
    (
        "GEL_BLOT",
        re.compile(
            r"\b(western blot\w*|immunoblot\w*|gel electrophoresis|sds-?page)\b|蛋白印迹|电泳",
            re.IGNORECASE,
        ),
    ),
    (
        "CHART",
        re.compile(
            r"\b(kaplan[- ]meier|survival curve|waterfall plot|forest plot|swimmer plot"
            r"|spider plot|box\s*plot|scatter\s*plot|bar (chart|graph)|line (chart|graph)"
            r"|histogram|roc curve|volcano plot)\b"
            r"|生存曲线|瀑布图|森林图|柱状图|折线图|散点图",
            re.IGNORECASE,
        ),
    ),
    (
        "DIAGRAM",
        re.compile(
            r"\b(consort|flow\s*chart|flow diagram|schematic|study design|pathway"
            r"|trial profile)\b|流程图|示意图|通路图|研究设计",
            re.IGNORECASE,
        ),
    ),
    (
        "TABLE_IMAGE",
        re.compile(r"\b(table|tabulat\w*)\b|表\s*\d", re.IGNORECASE),
    ),
)


@dataclass(frozen=True)
class FigureTyping:
    """一次判定的结果。`source` 必须留着 —— 零样本给的和关键词兜底给的
    是两个可信度完全不同的标签，混在一个字段里就再也分不开了。"""

    figure_type: str
    score: float
    source: str  # biomedclip | caption | none

    @property
    def confident(self) -> bool:
        return self.figure_type != "OTHER" and self.score >= 0.5


def type_from_caption(caption: str) -> FigureTyping:
    """caption 关键词判定。命中不了就是 OTHER，不猜。"""
    text = caption or ""
    for kind, pattern in _CAPTION_RULES:
        if pattern.search(text):
            # 分数固定 0.5：关键词命中是个二值事实，编一个 0.87 出来
            # 只会让它在报表上看起来和零样本的置信度可比，而它们不可比。
            return FigureTyping(kind, 0.5, "caption")
    return FigureTyping("OTHER", 0.0, "none")


class KeywordFigureTyper:
    """只看 caption 的兜底实现。没有 BiomedCLIP 时的默认。"""

    name = "caption"

    def classify(self, images: list[str | None], captions: list[str]) -> list[FigureTyping]:
        if len(images) != len(captions):
            raise ValueError("images 与 captions 长度必须一致")
        return [
            type_from_caption(cap) if img else FigureTyping("", 0.0, "none")
            for img, cap in zip(images, captions, strict=True)
        ]


class BiomedClipFigureTyper:
    """BiomedCLIP 零样本图型分类。

    与 `BiomedVisualEmbedder` 共用 `load_biomedclip`：同一份 800MB 权重，
    索引时本来就要为第五列做一次图像前向，这里复用的是同一个模型对象。

    caption 在两种情况下压过零样本，两条都不是调参而是证据强弱的判断：

    1. **低置信**：0.31 的 RADIOLOGY 和一句 "Figure 2. CT scan at baseline"
       相比，后者才是更强的证据。
    2. **判成 OTHER**：OTHER 的语义是"没认出来"，不是"确定不属于任何一类"，
       所以哪怕它给了 0.99 也不该压住 caption 里的明示。实测里那张
       "Table 2. Specifications" 整格都是句子而不是数字，零样本判成 OTHER 0.72，
       而 caption 第一个词就写着 Table。
    """

    name = "biomedclip"

    def __init__(
        self,
        *,
        device: str | None = None,
        batch_size: int = 16,
        min_score: float = 0.35,
        model: Any = None,
        preprocess: Any = None,
        tokenizer: Any = None,
    ) -> None:
        import torch

        from biomed_ontology.embed import load_biomedclip

        self._torch = torch
        self._batch_size = batch_size
        self._min_score = min_score
        if model is None:
            model, preprocess, tokenizer, device = load_biomedclip(device=device)
        self._model, self._preprocess, self._tok = model, preprocess, tokenizer
        self.device = device or "cpu"
        self._labels, self._prompt_text = _flatten_prompts()
        self._prompts = self._encode_prompts()

    def _encode_prompts(self) -> Any:
        torch = self._torch
        toks = self._tok(
            [f"this is a photo of {p}" for p in self._prompt_text],
            padding="max_length",
            truncation=True,
            max_length=256,
            return_tensors="pt",
        )
        with torch.inference_mode():
            return self._model.encode_text(toks["input_ids"].to(self.device), normalize=True)

    def classify(self, images: list[str | None], captions: list[str]) -> list[FigureTyping]:
        if len(images) != len(captions):
            raise ValueError("images 与 captions 长度必须一致")
        out: list[FigureTyping] = [FigureTyping("", 0.0, "none")] * len(images)
        slots = [i for i, p in enumerate(images) if p]
        for start in range(0, len(slots), self._batch_size):
            batch = slots[start : start + self._batch_size]
            for slot, typing in zip(
                batch, self._classify_batch([str(images[i]) for i in batch]), strict=True
            ):
                out[slot] = self._settle(typing, captions[slot])
        return out

    def _settle(self, typing: FigureTyping, caption: str) -> FigureTyping:
        if typing.score >= self._min_score and typing.figure_type != "OTHER":
            return typing
        fallback = type_from_caption(caption)
        return fallback if fallback.figure_type != "OTHER" else typing

    def _classify_batch(self, paths: list[str]) -> list[FigureTyping]:
        import torch
        from PIL import Image

        pixels = torch.stack([self._preprocess(Image.open(p).convert("RGB")) for p in paths]).to(
            self.device
        )
        with torch.inference_mode():
            feats = self._model.encode_image(pixels, normalize=True)
            # softmax 跨全部提示语算一次，再按类取最大：
            # 先按类归约再 softmax 会让提示语多的类天然占便宜。
            probs = (100.0 * feats @ self._prompts.T).softmax(dim=-1).float().cpu()

        out: list[FigureTyping] = []
        for row in probs:
            best_kind, best = "OTHER", 0.0
            for kind in FIGURE_TYPES:
                idx = [i for i, lbl in enumerate(self._labels) if lbl == kind]
                score = float(max(row[i] for i in idx)) if idx else 0.0
                if score > best:
                    best_kind, best = kind, score
            out.append(FigureTyping(best_kind, round(best, 4), "biomedclip"))
        return out


def _flatten_prompts() -> tuple[list[str], list[str]]:
    labels: list[str] = []
    prompts: list[str] = []
    for kind, texts in _PROMPTS.items():
        for text in texts:
            labels.append(kind)
            prompts.append(text)
    return labels, prompts


def get_figure_typer(name: str = "caption", *, device: str | None = None) -> Any:
    """`caption` 不下模型也能用；`biomedclip` 需要权重。

    默认是 caption 而不是 biomedclip：与 `get_embedder` 默认 fake 同一个理由 ——
    让不带模型的环境跑得通全链路，而不是让它在 import 阶段就炸。
    """
    if name in {"caption", "keyword"}:
        return KeywordFigureTyper()
    if name == "biomedclip":
        return BiomedClipFigureTyper(device=device)
    raise ValueError(f"未知 figure typer：{name!r}")


def asset_caption(chunk: Any) -> str:
    """图切片用来兜底判型的文字。图像切片的 `text` 本身就是 caption + 视觉摘要。"""
    return str(getattr(chunk, "text", "") or "")


def asset_path_of(chunk: Any, root: Path | None) -> str | None:
    from biomed_ontology.parse.assets import resolve_asset

    return resolve_asset(root, getattr(chunk, "doc_id", None), getattr(chunk, "asset_path", None))
