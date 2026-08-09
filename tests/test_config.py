"""运行时配置：默认值必须落在保守侧，放宽必须留下告警痕迹。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from biomed_ontology.config import load_settings


def test_defaults_prefer_milvus_evidence_index():
    """Milvus 为必选 Evidence Index；layout/vision 仍保持零依赖默认。"""
    s = load_settings({})
    assert s.layout_backend == "auto"
    assert s.search_backend == "milvus"
    assert s.vision_provider == "null"


def test_security_sensitive_defaults_are_closed():
    s = load_settings({})
    assert s.trust_entitlement_header is False
    assert s.layout_fallback is False
    # PoC 默认可试用 pending 组件，但必须留下告警；生产显式关。
    assert s.accept_uncleared_components is True
    assert any("法务闸门" in w for w in s.warnings())
    # 关掉 accept 后，milvus 默认本身不应再触发其它告警
    closed = load_settings({"HMD_ACCEPT_UNCLEARED_COMPONENTS": "false"})
    assert closed.warnings() == []


def test_local_search_backend_emits_milvus_warning():
    s = load_settings({"HMD_SEARCH_BACKEND": "local"})
    assert any("milvus" in w.lower() for w in s.warnings())


def test_trusting_client_entitlements_emits_a_warning():
    """客户端自述权限被采信是 OWASP A01，绝不允许静默生效。"""
    s = load_settings({"HMD_TRUST_ENTITLEMENT_HEADER": "true"})
    assert any("X-HMD-Entitlements" in w for w in s.warnings())


def test_auto_fallback_emits_a_warning():
    s = load_settings({"HMD_LAYOUT_FALLBACK": "1"})
    assert any("degraded" in w for w in s.warnings())


def test_cloud_mineru_warns_about_corpus_leaving_the_network():
    s = load_settings(
        {
            "HMD_MINERU_TRANSPORT": "http",
            "HMD_MINERU_BASE_URL": "https://mineru.net/api/v4",
            "HMD_ACCEPT_UNCLEARED_COMPONENTS": "false",
        }
    )
    assert s.mineru_is_cloud
    assert any("出网" in w for w in s.warnings())


def test_local_mineru_transport_never_counts_as_cloud():
    """即使 base_url 写了云域名，local transport 也不算出网。"""
    s = load_settings(
        {
            "HMD_MINERU_TRANSPORT": "local",
            "HMD_MINERU_BASE_URL": "https://mineru.net/api/v4",
            "HMD_ACCEPT_UNCLEARED_COMPONENTS": "false",
        }
    )
    assert not s.mineru_is_cloud
    assert s.warnings() == []


def test_self_hosted_mineru_http_does_not_warn_about_network():
    s = load_settings(
        {
            "HMD_MINERU_TRANSPORT": "http",
            "HMD_MINERU_BASE_URL": "http://localhost:8000",
            "HMD_ACCEPT_UNCLEARED_COMPONENTS": "false",
        }
    )
    assert not s.mineru_is_cloud
    assert s.warnings() == []


def test_mineru_and_router_settings_defaults():
    s = load_settings({})
    assert s.mineru_transport == "local"
    assert s.mineru_engine == "pipeline"
    assert s.mineru_parse_method == "auto"
    assert s.mineru_lang == "ch"
    assert s.mineru_formula_enable is True
    assert s.mineru_table_enable is True
    assert s.mineru_effort == "medium"
    assert s.parse_fast_max_pages == 40
    assert s.parse_max_pages == 400


def test_secrets_are_not_in_repr():
    """配置对象会进日志与异常回溯，密钥不能跟着一起出去。"""
    s = load_settings(
        {
            "HMD_MINERU_API_KEY": "sk-secret",
            "HMD_VISION_API_KEY": "sk-also",
            "HMD_MILVUS_TOKEN": "tok-milvus",
        }
    )
    blob = f"{s!r} {s} {s.model_dump()}"
    assert "sk-secret" not in blob
    assert "sk-also" not in blob
    assert "tok-milvus" not in blob
    assert s.mineru_api_key.get_secret_value() == "sk-secret"


@pytest.mark.parametrize(
    ("key", "value", "allowed"),
    [
        ("HMD_LAYOUT_BACKEND", "mineru2", "pymupdf4llm"),
        ("HMD_SEARCH_BACKEND", "qdrant", "milvus"),
        ("HMD_VISION_PROVIDER", "claude", "qwen"),
    ],
)
def test_unknown_backend_fails_loudly(key: str, value: str, allowed: str):
    """配置写错要立刻炸，且报错要说清合法取值 —— 否则运维只能翻源码。"""
    with pytest.raises(ValidationError) as exc:
        load_settings({key: value})
    assert allowed in str(exc.value)


def test_non_integer_limit_fails_loudly():
    with pytest.raises(ValidationError):
        load_settings({"HMD_PARSE_MAX_PAGES": "many"})


@pytest.mark.parametrize(
    "key", ["HMD_PARSE_MAX_PAGES", "HMD_PARSE_MAX_BYTES", "HMD_MINERU_TIMEOUT_S"]
)
def test_attack_surface_limits_must_be_positive(key: str):
    """0 或负数会让上限形同虚设，比没配还危险。"""
    with pytest.raises(ValidationError):
        load_settings({key: "0"})


def test_test_env_ignores_host_environment(monkeypatch: pytest.MonkeyPatch):
    """传 dict 就只认这个 dict —— 开发机上一个 .env 不该让断言在本地与 CI 分叉。"""
    monkeypatch.setenv("HMD_SEARCH_BACKEND", "local")
    assert load_settings({}).search_backend == "milvus"


def test_unknown_hmd_variables_are_ignored_not_fatal():
    """拼错的变量名不该拖垮进程；但它也不会生效 —— 这正是 warnings() 存在的理由。"""
    s = load_settings({"HMD_NO_SUCH_KNOB": "x"})
    assert s.layout_backend == "auto"


def test_foundation_openmetadata_settings():
    s = load_settings(
        {
            "HMD_OPENMETADATA_URL": "http://om:8585",
            "HMD_OPENMETADATA_EMAIL": "a@b.com",
            "HMD_OPENMETADATA_PASSWORD": "secret,",
        }
    )
    assert s.openmetadata_url == "http://om:8585"
    assert s.openmetadata_email == "a@b.com"
    assert s.openmetadata_password.get_secret_value() == "secret,"
    assert "secret," not in repr(s)
