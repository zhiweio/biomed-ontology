"""向量化：双塔 + 稀疏词法 + 视觉列，外加一个确定性假实现供 CI。

四路各司其职：
- `dense_general`  BGE-M3 稠密 1024 维 —— 通用语义，中英都行
- `sparse_lexical` BGE-M3 词法稀疏 —— 精确术语（"MET exon 14"）不会被语义抹平
- `dense_biomed`   SapBERT 768 维 —— 生物医药实体对齐，**英文强、中文弱**
- `dense_visual`   Qwen3-VL-Embedding 2048 维 —— 图表的像素，与文本同一空间

第三条是 P13 要按语种拆开报告的原因：总平均会把"英文涨了、中文没动"抹平。
第四条针对文献里最要命的一类信息：疗效曲线、剂量表、瀑布图 ——
这些内容在纯文本管线里等于不存在。

`FakeEmbedder` 不是占位符，是 CI 的一等公民：真模型要下载 GB 级权重，
把它拖进测试会让每次跑测试都变成一场赌博。但它**不许出现在对外报数的路径上** ——
见 `eval` 与 `cli` 里的守门。
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "REAL_EMBEDDERS",
    "VECTOR_FIELDS",
    "BiomedEmbedder",
    "Embedder",
    "EmbeddingBundle",
    "FakeEmbedder",
    "GeneralEmbedder",
    "VisualEmbedder",
    "best_device",
    "get_embedder",
]

VECTOR_FIELDS = ("dense_general", "sparse_lexical", "dense_biomed", "dense_visual")

_TOKEN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")

_GITEE_ORG = "https://gitee.com/hf-models"

# HF 仓库 ID → 各镜像站的仓库名。命名空间彼此独立，没有换算规则，只能逐条登记。
#
# `modelscope: None` 表示该站**没有 PyTorch 权重**：SapBERT 在 ModelScope 上
# 只有 Xenova 的 ONNX 版，而本项目只走 PyTorch，所以它不是一个可选源。
_MIRRORS: dict[str, dict[str, str | None]] = {
    "BAAI/bge-m3": {
        "modelscope": "BAAI/bge-m3",
        "gitee": "bge-m3",
    },
    "cambridgeltl/SapBERT-from-PubMedBERT-fulltext": {
        "modelscope": None,
        "gitee": "SapBERT-from-PubMedBERT-fulltext",
    },
    # Apache-2.0。Gitee 上没有对应镜像，只能走 ModelScope。
    "Qwen/Qwen3-VL-Embedding-2B": {"modelscope": "Qwen/Qwen3-VL-Embedding-2B", "gitee": None},
}


def _local_dir(model_id: str) -> Path:
    from biomed_ontology.config import settings

    return settings.model_cache_dir / "models" / model_id.rsplit("/", 1)[-1]


def _local_candidates(model_id: str) -> list[Path]:
    """本机上可能已经躺着这份权重的几个位置。

    除了我们自己约定的 `models/<仓库名>`，还要认 ModelScope 下载器留下的
    `models/<组织>--<仓库名>[/snapshots/<版本>]`。不认的后果是：明明权重就在盘上，
    每次加载还是要联网确认一遍 —— 内网机器上这等于加载不了。
    """
    root = _local_dir(model_id).parent
    org, _, name = model_id.rpartition("/")
    hyphened = root / f"{org.replace('/', '--')}--{name}" if org else root / name
    out = [_local_dir(model_id), hyphened]
    out.extend(sorted(p for p in (hyphened / "snapshots").glob("*") if p.is_dir()))
    return out


def best_device() -> str:
    """CUDA > MPS（Apple Silicon）> CPU。

    torch 缺失时返回 `cpu`：`FakeEmbedder` 不需要 torch，而 CI 走的正是它 ——
    仅仅问一句"用什么设备"不该把可选依赖变成必需依赖。
    """
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _mirror_repo(model_id: str, site: str) -> str:
    try:
        repo = _MIRRORS[model_id][site]
    except KeyError:
        raise ValueError(f"{model_id} 未登记镜像，请补进 embed._MIRRORS") from None
    if repo is None:
        raise LookupError(f"{site} 上没有 {model_id} 的 PyTorch 权重")
    return repo


def _require_git_lfs() -> None:
    """没装 git-lfs 时 clone 照样"成功"，但权重文件是几百字节的指针文本。

    失败会推迟到 `AutoModel.from_pretrained` 那一刻，报错内容与 LFS 毫无关系，
    而且此时目录已经落盘、看起来像一份正常的本地模型。所以必须前置拦。
    """
    import subprocess

    try:
        subprocess.run(["git", "lfs", "version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "Gitee 镜像的权重走 Git LFS，但本机没有可用的 git-lfs。"
            "请先安装（macOS: brew install git-lfs；Debian/Ubuntu: apt install git-lfs），"
            "再重试。"
        ) from exc


def _from_gitee(model_id: str) -> str:
    """clone Gitee 的 hf-models 镜像。权重走 LFS。"""
    import subprocess
    import tempfile

    target = _local_dir(model_id)
    url = f"{_GITEE_ORG}/{_mirror_repo(model_id, 'gitee')}"
    _require_git_lfs()
    target.parent.mkdir(parents=True, exist_ok=True)

    # 先 clone 到临时目录再原子改名。clone 中断时留下的半份目录里 config.json
    # 一定在（它不是 LFS 文件），会被下一次 resolve_model 当成"本地已有"直接返回 ——
    # 那是一份 LFS 指针没拉下来的坏模型，要到加载时才炸，且看不出是哪来的。
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        staged = Path(staging) / "repo"
        # 仓库名取自上面的固定表，不是外部输入；且不经 shell。
        subprocess.run(["git", "clone", "--depth", "1", url, str(staged)], check=True)
        staged.rename(target)
    return str(target)


def resolve_model(model_id: str) -> str:
    """返回可直接喂给 transformers 的本地目录（或仓库 ID）。

    顺序：**本地已有 → 选定的 hub → Gitee 兜底**。

    本地优先是为了让手工放进 `data/cache/models/models/<仓库名>` 的权重直接生效 ——
    内网里手动拷权重是常态，应该走"放对位置"而不是"改代码"。

    兜底只在下载失败时触发，且**会打印实际用了哪个源**：权重来源必须可追溯，
    否则同一份代码在两台机器上可能加载到不同的模型，而报告里看不出来。
    未登记镜像属配置错误，直接抛出，不被兜底掩盖。
    """
    from biomed_ontology.config import settings

    for candidate in _local_candidates(model_id):
        if (candidate / "config.json").is_file():
            return str(candidate)

    if settings.model_hub == "gitee":
        return _from_gitee(model_id)

    try:
        if settings.model_hub == "modelscope":
            from modelscope import snapshot_download as ms_download

            return ms_download(
                _mirror_repo(model_id, "modelscope"), cache_dir=str(settings.model_cache_dir)
            )
        from huggingface_hub import snapshot_download as hf_download

        return hf_download(model_id, cache_dir=str(settings.model_cache_dir))
    except ValueError:
        raise
    except Exception as exc:
        # 下载失败的形态太多（TLS 重置、404、LFS 缺失、超时），逐个列必然漏。
        # flush：这行是权重来源的唯一线索，管道里丢掉就查不出加载了哪份模型。
        print(
            f"[embed] {settings.model_hub} 取 {model_id} 失败（{exc}），改用 Gitee 镜像",
            flush=True,
        )
        return _from_gitee(model_id)


class EmbeddingBundle(dict[str, object]):
    """一次前向产出的全部向量列。键是 Milvus 字段名，不另起别名。"""


@runtime_checkable
class Embedder(Protocol):
    name: str
    dims: dict[str, int]

    def encode(
        self, texts: list[str], *, images: list[str | None] | None = None
    ) -> list[EmbeddingBundle]: ...


class FakeEmbedder:
    """确定性哈希向量。同样的文本永远给同样的向量，跨进程、跨机器一致。

    它测不出语义质量 —— 那是 P13 的十臂消融要回答的问题。
    它测的是**管线接线是否正确**：过滤有没有生效、三列能不能分别查、
    许可有没有泄漏。这些和模型好坏无关，却最容易写错。
    """

    name = "fake"

    def __init__(
        self, *, general_dim: int = 64, biomed_dim: int = 32, visual_dim: int = 48
    ) -> None:
        self.dims = {
            "dense_general": general_dim,
            "dense_biomed": biomed_dim,
            "dense_visual": visual_dim,
        }

    def encode(
        self, texts: list[str], *, images: list[str | None] | None = None
    ) -> list[EmbeddingBundle]:
        paths = images or [None] * len(texts)
        return [
            EmbeddingBundle(
                dense_general=_hash_vector(t, self.dims["dense_general"], salt="general"),
                dense_biomed=_hash_vector(t, self.dims["dense_biomed"], salt="biomed"),
                # 把图路径掺进哈希，才能让"有图/无图"在假向量里也是可分的 ——
                # 否则视觉列的接线错了（图没传下来）测试照样全绿。
                dense_visual=_hash_vector(
                    f"{p or ''}\x00{t}", self.dims["dense_visual"], salt="visual"
                ),
                sparse_lexical=_hash_sparse(t),
            )
            for t, p in zip(texts, paths, strict=True)
        ]


def _hash_vector(text: str, dim: int, *, salt: str) -> list[float]:
    """词袋 → 定长稠密向量。相似文本共享词，因此向量也相近。"""
    vec = [0.0] * dim
    for token in _TOKEN.findall(text.casefold()) or ["\x00"]:
        digest = hashlib.blake2b(f"{salt}:{token}".encode(), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _hash_sparse(text: str) -> dict[int, float]:
    """词法稀疏向量：词 → 词频。维度靠哈希定，与真 BGE-M3 的接口形状一致。"""
    counts: dict[int, float] = {}
    for token in _TOKEN.findall(text.casefold()):
        idx = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big") % 65536
        counts[idx] = counts.get(idx, 0.0) + 1.0
    total = sum(counts.values()) or 1.0
    return {k: v / total for k, v in counts.items()}


class GeneralEmbedder:
    """BGE-M3：一次前向同时给出稠密与词法稀疏，省一半算力。"""

    name = "bge-m3"

    def __init__(
        self,
        *,
        model_id: str = "BAAI/bge-m3",
        device: str | None = None,
        use_fp16: bool = False,
    ) -> None:
        from pymilvus.model.hybrid import BGEM3EmbeddingFunction

        self.device = device or best_device()
        self._fn = BGEM3EmbeddingFunction(
            model_name=resolve_model(model_id), use_fp16=use_fp16, device=self.device
        )
        self.dims = {"dense_general": int(self._fn.dim["dense"])}

    def encode(
        self, texts: list[str], *, images: list[str | None] | None = None
    ) -> list[EmbeddingBundle]:
        out = self._fn.encode_documents(texts)
        dense, sparse = out["dense"], out["sparse"]
        return [
            EmbeddingBundle(
                dense_general=list(dense[i]),
                sparse_lexical=_row_to_dict(sparse, i),
            )
            for i in range(len(texts))
        ]


class BiomedEmbedder:
    """SapBERT：UMLS 同义词对齐训练，把"savolitinib"与"AZD6094"拉到一起。

    **英文单语**。P13 因此给它单独一臂并按语种分表 —— 总平均会把
    "英文涨了、中文没动"抹平。

    **必须取 `[CLS]` 再 L2 归一**，这是 SapBERT 的规定取法。
    这里不用 `SentenceTransformer`：官方权重目录里没有 `modules.json`，
    sentence-transformers 会自动补一层 **mean pooling**，
    于是拿到的是另一个模型的向量 —— 不报错，只是悄悄换掉语义。
    """

    name = "sapbert"

    def __init__(
        self,
        *,
        model_id: str = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
        device: str | None = None,
        batch_size: int = 32,
    ) -> None:
        import torch
        from transformers import AutoModel, AutoTokenizer

        path = resolve_model(model_id)
        self.device = device or best_device()
        self._torch = torch
        self._batch_size = batch_size
        self._tok = AutoTokenizer.from_pretrained(path)
        self._model = AutoModel.from_pretrained(path).to(self.device).eval()
        self.dims = {"dense_biomed": int(self._model.config.hidden_size)}

    def encode(
        self, texts: list[str], *, images: list[str | None] | None = None
    ) -> list[EmbeddingBundle]:
        torch = self._torch
        out: list[EmbeddingBundle] = []
        # 分批：索引时整个语料是一次传进来的，不切批会随语料规模把内存吃穿。
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            toks = self._tok(
                batch, padding=True, truncation=True, max_length=256, return_tensors="pt"
            ).to(self.device)
            with torch.inference_mode():
                cls = self._model(**toks).last_hidden_state[:, 0, :]
            cls = torch.nn.functional.normalize(cls, p=2, dim=1)
            out.extend(EmbeddingBundle(dense_biomed=v.tolist()) for v in cls.cpu())
        return out


class VisualEmbedder:
    """Qwen3-VL-Embedding：文本与图像落在**同一个** 2048 维空间。

    这是"视觉融合"与"给图配字幕"的分界线。字幕方案先把图压成一句话，
    再拿这句话去检索 —— 字幕没提到的东西就此永久丢失，而且丢得无声无息。
    这里把图块的像素直接编码进检索空间，于是文本 query 能凭图本身命中它。

    图表切片同时喂 caption 与像素（模型接受混合模态）；纯文本切片只喂文本。
    两者共享一个向量空间，跨模态比较才有意义。

    **必须用官方 `Qwen3VLEmbedder`**（随权重目录一起下发的 `scripts/`）：
    它取的是 **last-token** 池化。自己写一遍很容易顺手写成 mean 或 CLS，
    那样不报错，只是悄悄换成另一个模型 —— 这个坑本仓库在 SapBERT 上踩过一次。
    """

    name = "qwen3-vl"

    def __init__(
        self,
        *,
        model_id: str = "Qwen/Qwen3-VL-Embedding-2B",
        device: str | None = None,
        batch_size: int = 4,
    ) -> None:
        import torch

        path = Path(resolve_model(model_id))
        self.device = device or best_device()
        self._batch_size = batch_size
        # bf16 在 MPS/CUDA 上都受支持，CPU 上算得极慢且部分算子缺失，退回 fp32。
        dtype = torch.float32 if self.device == "cpu" else torch.bfloat16
        self._embedder = _load_qwen3_vl(path, dtype=dtype)
        self._embedder.model.to(self.device)
        self.dims = {"dense_visual": int(self._embedder.model.config.text_config.hidden_size)}

    def encode(
        self, texts: list[str], *, images: list[str | None] | None = None
    ) -> list[EmbeddingBundle]:
        paths = images or [None] * len(texts)
        if len(paths) != len(texts):
            raise ValueError("images 与 texts 长度必须一致")

        items: list[dict[str, str]] = []
        for text, image in zip(texts, paths, strict=True):
            item: dict[str, str] = {}
            if text.strip():
                item["text"] = text
            if image:
                item["image"] = image
            items.append(item or {"text": ""})

        out: list[EmbeddingBundle] = []
        # 批很小：一张 300 DPI 的图能展开成上千个视觉 token，按文本的批大小会直接 OOM。
        for start in range(0, len(items), self._batch_size):
            vectors = self._embedder.process(items[start : start + self._batch_size])
            out.extend(EmbeddingBundle(dense_visual=v.tolist()) for v in vectors.float().cpu())
        return out


def _load_qwen3_vl(path: Path, *, dtype: object) -> Any:
    """从权重目录里的 `scripts/qwen3_vl_embedding.py` 载入官方实现。

    这份文件是权重的一部分（随 snapshot 下发），不是可以 pip 装的包，
    所以只能按路径加载。复制一份进本仓库会更"干净"，但那意味着官方一改池化方式
    我们就静默地用着旧算法 —— 宁可依赖它随权重一起版本化。
    """
    import importlib.util

    script = path / "scripts" / "qwen3_vl_embedding.py"
    if not script.is_file():
        raise FileNotFoundError(
            f"{script} 不存在：Qwen3-VL-Embedding 的官方推理实现随权重目录下发，"
            "请重新完整下载该模型，不要只取权重文件。"
        )
    spec = importlib.util.spec_from_file_location("hmd_qwen3_vl_embedding", script)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Qwen3VLEmbedder(model_name_or_path=str(path), torch_dtype=dtype)


class CompositeEmbedder:
    """把多个 embedder 的产出并成一个 bundle。列缺失就是缺失，不补零。

    补零会让"这一列没算"和"这一列算出来是零向量"分不清，
    而后者在余弦距离下是未定义的。
    """

    name = "composite"

    def __init__(self, *parts: Embedder) -> None:
        self.parts = parts
        self.dims = {k: v for p in parts for k, v in p.dims.items()}
        # 拼出成员名，而不是笼统叫 "composite"：这个名字会被盖进 Milvus 集合描述，
        # 是事后判断"这张表到底是用哪几个模型建的"的唯一依据。
        self.name = "+".join(p.name for p in parts) or "composite"

    def encode(
        self, texts: list[str], *, images: list[str | None] | None = None
    ) -> list[EmbeddingBundle]:
        merged = [EmbeddingBundle() for _ in texts]
        for part in self.parts:
            for i, bundle in enumerate(part.encode(texts, images=images)):
                merged[i].update(bundle)
        return merged


def _row_to_dict(matrix: object, row: int) -> dict[int, float]:
    csr = matrix[[row]]  # type: ignore[index]
    coo = csr.tocoo()
    return {int(c): float(v) for c, v in zip(coo.col, coo.data, strict=True)}


REAL_EMBEDDERS = ("bge-m3", "sapbert", "dual", "qwen3-vl", "multimodal")
"""可用于对外报数的 embedder。`fake` 不在其中 —— 它的相似度连符号都可能是反的。"""


def get_embedder(name: str = "fake", *, device: str | None = None) -> Embedder:
    """配置开关的唯一落点。默认 fake —— 不下模型也能跑通全链路。

    `device=None` 表示自动挑（CUDA > MPS > CPU）。
    """
    if name == "fake":
        return FakeEmbedder()
    if name == "bge-m3":
        return GeneralEmbedder(device=device)
    if name == "sapbert":
        return BiomedEmbedder(device=device)
    if name == "qwen3-vl":
        return VisualEmbedder(device=device)
    if name == "dual":
        return CompositeEmbedder(GeneralEmbedder(device=device), BiomedEmbedder(device=device))
    if name == "multimodal":
        return CompositeEmbedder(
            GeneralEmbedder(device=device),
            BiomedEmbedder(device=device),
            VisualEmbedder(device=device),
        )
    raise ValueError(f"未知 embedder：{name!r}")
