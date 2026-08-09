"""OpenAI-compatible 文本 chat（受限 JSON），镜像 parse.vision 的 Provider 模式。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

__all__ = [
    "ChatCache",
    "ChatProvider",
    "ChatResult",
    "DEFAULT_LLM_BASE_URLS",
    "NullChatProvider",
    "OpenAIChatProvider",
    "get_chat_provider",
]

ChatProviderName = Literal["null", "openai", "deepseek", "qwen"]

# 官方 / 常见兼容端点；空 base_url 时按 provider 填入
DEFAULT_LLM_BASE_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}


@dataclass(frozen=True)
class ChatResult:
    text: str
    cached: bool = False
    warnings: tuple[str, ...] = ()


@runtime_checkable
class ChatProvider(Protocol):
    name: str

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, str] | None = None,
    ) -> ChatResult: ...


class NullChatProvider:
    """离线降级：不调 API，返回空 JSON。"""

    name = "null"

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, str] | None = None,
    ) -> ChatResult:
        return ChatResult(
            text='{"relations":[]}',
            warnings=("未配置 LLM 后端，跳过文本关系抽取",),
        )


class OpenAIChatProvider:
    """OpenAI / DeepSeek / Qwen 兼容 chat.completions。"""

    def __init__(
        self,
        *,
        model: str = "deepseek-v4-flash",
        api_key: str = "",
        base_url: str = "",
        timeout_s: float = 60.0,
        temperature: float = 0.0,
        name: str = "openai",
        thinking: bool = False,
    ) -> None:
        self.name = name
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.thinking = thinking

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, str] | None = None,
    ) -> ChatResult:
        from openai import OpenAI

        client = OpenAI(
            api_key=self.api_key or "unset",
            base_url=self.base_url or None,
            timeout=self.timeout_s,
        )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        # DeepSeek V4 默认 thinking；关系抽取要稳定 JSON，默认关掉
        if self.name == "deepseek" and not self.thinking:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        elif self.name == "deepseek" and self.thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        resp = client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or "{}"
        return ChatResult(text=content)


class ChatCache:
    """按 messages 哈希寻址的磁盘缓存。"""

    def __init__(self, root: Path, provider: ChatProvider) -> None:
        self.root = root
        self.provider = provider
        self.calls = 0

    @property
    def name(self) -> str:
        return f"cache:{self.provider.name}"

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, str] | None = None,
    ) -> ChatResult:
        blob = json.dumps(
            {"messages": messages, "fmt": response_format, "p": self.provider.name},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        key = hashlib.blake2b(blob, digest_size=20).hexdigest()
        path = self.root / key[:2] / f"{key}.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return ChatResult(
                text=str(data.get("text") or "{}"),
                cached=True,
                warnings=tuple(data.get("warnings") or ()),
            )
        result = self.provider.complete(messages, response_format=response_format)
        self.calls += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"text": result.text, "warnings": list(result.warnings)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return result


def get_chat_provider(config: Any | None = None) -> ChatProvider:
    """按 Settings 装配。

    - ``HMD_LLM_PROVIDER=null`` → Null
    - 非 null 但无 API key → Null（避免空密钥打到公网）
    - ``deepseek`` 默认 base ``https://api.deepseek.com``、模型 ``deepseek-v4-flash``
    """
    from biomed_ontology.config import settings as default_settings

    cfg = config or default_settings
    name = str(getattr(cfg, "llm_provider", "deepseek") or "deepseek")
    if name == "null":
        return NullChatProvider()

    api_key = (
        getattr(cfg, "llm_api_key", None).get_secret_value()
        if hasattr(getattr(cfg, "llm_api_key", None), "get_secret_value")
        else str(getattr(cfg, "llm_api_key", "") or "")
    )
    if not api_key.strip():
        return NullChatProvider()

    base_url = str(getattr(cfg, "llm_base_url", "") or "").rstrip("/")
    if not base_url:
        base_url = DEFAULT_LLM_BASE_URLS.get(name, "")

    provider: ChatProvider = OpenAIChatProvider(
        name=name,
        model=str(getattr(cfg, "llm_model", "deepseek-v4-flash") or "deepseek-v4-flash"),
        api_key=api_key,
        base_url=base_url,
        timeout_s=float(getattr(cfg, "llm_timeout_s", 60.0)),
        thinking=bool(getattr(cfg, "llm_thinking", False)),
    )
    cache_dir = Path(getattr(cfg, "llm_cache_dir", "data/cache/llm"))
    if cache_dir:
        return ChatCache(cache_dir, provider)
    return provider
