"""运行时配置：pydantic-settings，来源为环境变量 + `.env`。

所有默认值都指向**零外部依赖**的形态 —— 不装 Docker、不联网、不配 API key
也能跑通全链路；需要重型能力时才逐项打开。

安全相关的默认值一律取保守侧（不信任客户端断言、不自动降级），
放宽必须由人显式改环境变量，且 `warnings()` 会在启动时把它打出来。

密钥一律用 `SecretStr`：`repr()` 与日志里只会看到 `**********`，
取值必须显式 `.get_secret_value()` —— 让泄漏成为一个需要动手的行为。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

__all__ = [
    "LayoutBackendName",
    "ModelHubName",
    "SearchBackendName",
    "Settings",
    "VisionProviderName",
    "load_settings",
    "settings",
]

LayoutBackendName = Literal["pymupdf", "mineru"]
ModelHubName = Literal["hf", "modelscope", "gitee"]
SearchBackendName = Literal["local", "milvus"]
VisionProviderName = Literal["null", "openai", "qwen"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HMD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- 版面解析 ---------------------------------------------------------
    layout_backend: LayoutBackendName = "pymupdf"
    layout_fallback: bool = False
    mineru_base_url: str = "http://localhost:8000"
    mineru_api_key: SecretStr = SecretStr("")
    mineru_timeout_s: int = Field(default=300, gt=0)

    # --- PDF 攻击面限制 ---------------------------------------------------
    parse_max_pages: int = Field(default=400, gt=0)
    parse_max_bytes: int = Field(default=64 * 1024 * 1024, gt=0)

    # --- 视觉融合 ---------------------------------------------------------
    vision_provider: VisionProviderName = "null"
    vision_model: str = "gpt-4o-mini"
    vision_base_url: str = ""
    vision_api_key: SecretStr = SecretStr("")
    vision_cache_dir: Path = Path("data/cache/vision")

    # --- 检索后端（Milvus 为必选；local 仅单测）-------------------------------
    search_backend: SearchBackendName = "milvus"
    milvus_uri: str = "http://localhost:19530"
    milvus_token: SecretStr = SecretStr("")
    milvus_collection: str = "hmd_chunks"

    # --- Foundation 联调 ----------------------------------------------------
    graphdb_url: str = "http://localhost:7200"
    graphdb_repository: str = "hmd"
    bern2_url: str = ""
    openmetadata_url: str = "http://localhost:8585"
    openmetadata_token: SecretStr = SecretStr("")
    bios_license_ack: str = ""
    bios_init: Literal["full", "subset"] = "full"
    bios_max_concepts: int = Field(default=0, ge=0)  # 0 = 全量不截断

    # --- 模型权重源 -------------------------------------------------------
    # 内网往往连不上 huggingface.co（TLS 直接被重置）。取不到时自动回落 Gitee
    # 镜像（gitee.com/hf-models），仓库名映射见 embed._MIRRORS。
    # 手工放进 model_cache_dir/models/<仓库名> 的权重优先于任何下载。
    model_hub: ModelHubName = "hf"
    model_cache_dir: Path = Path("data/cache/models")

    # --- 服务层安全 -------------------------------------------------------
    trust_entitlement_header: bool = False

    # --- 第三方组件法务闸门 -------------------------------------------------
    accept_uncleared_components: bool = False

    @field_validator(
        "mineru_base_url",
        "milvus_uri",
        "vision_base_url",
        "graphdb_url",
        "bern2_url",
        "openmetadata_url",
    )
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def mineru_is_cloud(self) -> bool:
        """云端 API 意味着语料要出网。调用方据此决定是否放行受限来源。"""
        return "mineru.net" in self.mineru_base_url

    def warnings(self) -> list[str]:
        """启动时应当打给运维看的告警。返回空列表表示配置处于保守态。"""
        out: list[str] = []
        if self.trust_entitlement_header:
            out.append(
                "HMD_TRUST_ENTITLEMENT_HEADER=true：X-HMD-Entitlements 为客户端自述且被无条件采信，"
                "任何调用方都可自行声明付费权限。仅限本地 PoC，"
                "生产必须改为从校验过的 token claim 取值。"
            )
        if self.layout_fallback:
            out.append(
                "HMD_LAYOUT_FALLBACK=true：解析后端失败时会自动降级，"
                "同一批语料可能出现能力不一致的切片（降级事实见 LayoutResult.degraded）。"
            )
        if self.layout_backend == "mineru" and self.mineru_is_cloud:
            out.append(
                "MinerU 指向云端 API：文档正文将出网至第三方。未公开专利与采购数据不得走此路径。"
            )
        if self.accept_uncleared_components:
            out.append(
                "HMD_ACCEPT_UNCLEARED_COMPONENTS=true：跳过了第三方组件的法务闸门。"
                "仅限本地试用；待核实的许可义务见 NOTICE。"
            )
        if self.search_backend != "milvus":
            out.append(
                "HMD_SEARCH_BACKEND 非 milvus：生产与 Foundation Evidence 要求必选 Milvus。"
                "请 task milvus:up 并将 HMD_SEARCH_BACKEND=milvus。"
            )
        return out


class _ExplicitSettings(Settings):
    """只认构造参数，屏蔽进程环境与 `.env`。

    `_env_file=None` 只关掉 dotenv，环境变量源仍然生效 —— 必须改数据源才能真正隔离。
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """`env=None` 读进程环境与 `.env`；传 dict 则**只**用该 dict。

    测试必须走后者：否则开发机上一个 `.env` 就能让断言在 CI 与本地给出不同结果。
    """
    if env is None:
        return Settings()
    fields = Settings.model_fields
    kwargs = {
        key: value
        for raw, value in env.items()
        if (key := raw.removeprefix("HMD_").casefold()) in fields
    }
    return _ExplicitSettings(**kwargs)


settings = load_settings()
