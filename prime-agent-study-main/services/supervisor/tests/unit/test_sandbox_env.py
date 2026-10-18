"""WP-M5: sandbox env sanitization unit tests."""

from __future__ import annotations

from supervisor.sandbox.env import (
    is_allowed_gateway_inject,
    is_denied_env_key,
    sanitize_sandbox_env,
)


def test_denied_keys() -> None:
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "MODEL_GATEWAY_TOKEN",
        "DATABASE_URL",
        "AUTH_TOKEN",
        "LITELLM_API_KEY",
        "HF_TOKEN",
        "COHERE_API_KEY",
        "GROQ_API_KEY",
        "XAI_API_KEY",
        "MY_APP_PASSWORD",
        "CUSTOM_SECRET",
    ):
        assert is_denied_env_key(key)


def test_sanitize_keeps_path_strips_secrets() -> None:
    out = sanitize_sandbox_env(
        {"MY_APP_FLAG": "1", "OPENAI_API_KEY": "sk-x", "HF_TOKEN": "hf_x"},
        base={"PATH": "/usr/bin", "HOME": "/tmp", "OPENAI_API_KEY": "sk-host"},
    )
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/tmp"
    assert out["MY_APP_FLAG"] == "1"
    assert "OPENAI_API_KEY" not in out
    assert "HF_TOKEN" not in out


def test_sanitize_strips_vk_shaped_extras() -> None:
    out = sanitize_sandbox_env(
        {
            "VIRTUAL_KEY_TOKEN": "vk_abc",
            "GATEWAY_TOKEN": "svc",
            "SAFE_FLAG": "1",
        },
        base={"PATH": "/bin"},
    )
    assert out.get("SAFE_FLAG") == "1"
    assert "VIRTUAL_KEY_TOKEN" not in out
    assert "GATEWAY_TOKEN" not in out
    assert out["PATH"] == "/bin"


def test_sanitize_allowlists_session_vk_and_gateway_url() -> None:
    out = sanitize_sandbox_env(
        {
            "OPENAI_BASE_URL": "http://model-gateway:8090/v1",
            "OPENAI_API_KEY": "vk_session_abc",
            "YAL_VK_BIND_PATH": "/tmp/sess/.yal/vk",
            "OPENAI_API_KEY_FILE": "/tmp/sess/.yal/vk",
            "GATEWAY_TOKEN": "svc-secret",
            "OPENAI_API_KEY_HOST": "sk-leak",
        },
        base={"PATH": "/bin", "GATEWAY_TOKEN": "host-svc"},
    )
    assert out["OPENAI_BASE_URL"] == "http://model-gateway:8090/v1"
    assert out["OPENAI_API_KEY"] == "vk_session_abc"
    assert out["YAL_VK_BIND_PATH"] == "/tmp/sess/.yal/vk"
    assert out["OPENAI_API_KEY_FILE"] == "/tmp/sess/.yal/vk"
    assert "GATEWAY_TOKEN" not in out
    assert "OPENAI_API_KEY_HOST" not in out


def test_sanitize_rejects_vendor_openai_base_url() -> None:
    assert not is_allowed_gateway_inject(
        "OPENAI_BASE_URL", "https://api.openai.com/v1"
    )
    assert not is_allowed_gateway_inject(
        "OPENAI_BASE_URL", "https://openrouter.ai/api/v1"
    )
    assert is_allowed_gateway_inject(
        "OPENAI_BASE_URL", "http://model-gateway:8090/v1"
    )
    out = sanitize_sandbox_env(
        {
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_API_KEY": "vk_ok",
        },
        base={"PATH": "/bin"},
    )
    assert "OPENAI_BASE_URL" not in out
    assert out["OPENAI_API_KEY"] == "vk_ok"


def test_sanitize_rejects_non_vk_openai_api_key() -> None:
    assert not is_allowed_gateway_inject("OPENAI_API_KEY", "sk-openai")
    assert not is_allowed_gateway_inject("OPENAI_API_KEY", "yal-gateway")
    assert not is_allowed_gateway_inject("OPENAI_API_KEY", "svc-token")
    out = sanitize_sandbox_env(
        {"OPENAI_API_KEY": "yal-gateway", "OPENAI_BASE_URL": "http://gw/v1"},
        base={"PATH": "/bin"},
    )
    assert "OPENAI_API_KEY" not in out
    assert out["OPENAI_BASE_URL"] == "http://gw/v1"
