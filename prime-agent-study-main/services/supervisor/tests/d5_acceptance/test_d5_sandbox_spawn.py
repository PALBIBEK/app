"""D5 §6 B / T9 — sandbox sanitize + harness spawn credential hygiene."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.seams import RuntimeSeams
from supervisor.harness import build_spawn_plan
from supervisor.sandbox.env import sanitize_sandbox_env
from supervisor.seams.providers.http_gateway import HttpModelGatewayClient


def test_t9_sandbox_env_strips_gateway_token_and_vendor_keys() -> None:
    """T9: sanitize never passes GATEWAY_TOKEN / vendor keys into sandbox env."""
    dirty = {
        "PATH": "/usr/bin",
        "HOME": "/tmp",
        "GATEWAY_TOKEN": "svc-secret",
        "MODEL_GATEWAY_TOKEN": "svc-secret",
        "OPENAI_API_KEY": "sk-openai",
        "OPENROUTER_API_KEY": "sk-or",
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "ANTHROPIC_API_KEY": "sk-ant",
    }
    out = sanitize_sandbox_env(extra=dirty, base={})
    for banned in (
        "GATEWAY_TOKEN",
        "MODEL_GATEWAY_TOKEN",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
    ):
        assert banned not in out, f"{banned} must not enter sandbox env"


def test_t9_sandbox_may_receive_session_vk_or_bind_file_only() -> None:
    """§6 B: intentional VK inject (vk_…) may pass; service tokens must not."""
    # If product allowlists a session credential key, it must be vk_-shaped only.
    vk = "vk_session_d5_acceptance_only"
    out = sanitize_sandbox_env(
        extra={
            "YAL_SESSION_VK": vk,
            "GATEWAY_TOKEN": "svc",
            "OPENAI_API_KEY": "sk-leak",
        },
        base={"PATH": "/bin"},
    )
    assert "GATEWAY_TOKEN" not in out
    assert out.get("OPENAI_API_KEY") in (None, "") or str(out.get("OPENAI_API_KEY", "")).startswith(
        "vk_"
    )
    # Service token must never appear as any remaining value.
    assert "svc" not in out.values()
    assert "sk-leak" not in out.values()


def test_spawn_never_injects_gateway_token_or_yal_gateway_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§6 B: harness spawn must not inject GATEWAY_TOKEN / \"yal-gateway\" as API key."""
    monkeypatch.setenv("GATEWAY_TOKEN", "host-service-token-must-not-leak")
    monkeypatch.setenv("HARNESS_CLI", "yal-harness")
    yal = tmp_path / ".yal"
    yal.mkdir()
    (yal / "plug_bindings.json").write_text(
        '{"gateway_base_url":"http://gateway.test:8090","model_route":"githi"}\n',
        encoding="utf-8",
    )
    (yal / "launch.json").write_text('{"cli":"yal-harness"}\n', encoding="utf-8")
    # Session mint left a bind file (manager path); spawn must use vk_ only.
    (yal / "vk").write_text("vk_session_from_mint\n", encoding="utf-8")

    seams = RuntimeSeams(
        model_gateway=HttpModelGatewayClient(
            base_url="http://gateway.test:8090",
            token="host-service-token-must-not-leak",
        )
    )
    plan = build_spawn_plan(
        session_dir=str(tmp_path),
        model="githi",
        provider=None,
        seams=seams,
    )
    api_key = plan.env.get("OPENAI_API_KEY", "")
    assert api_key != "yal-gateway"
    assert api_key != "host-service-token-must-not-leak"
    assert "GATEWAY_TOKEN" not in plan.env
    assert "MODEL_GATEWAY_TOKEN" not in plan.env
    assert api_key.startswith("vk_") or plan.env.get("YAL_VK_BIND_PATH") or plan.env.get(
        "OPENAI_API_KEY_FILE"
    ), (
        "spawn must inject session vk_… (or bind-file path), "
        f"got OPENAI_API_KEY={api_key!r} env_keys={sorted(plan.env)}"
    )
    assert plan.env.get("OPENAI_BASE_URL") == "http://gateway.test:8090/v1"
