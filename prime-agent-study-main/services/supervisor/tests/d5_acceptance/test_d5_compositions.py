"""D5 §6 B / T10 — compositions plug_bindings + fake gateway refused."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.agent.seams import RuntimeSeams
from supervisor.harness import build_spawn_plan
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.seams.providers.http_gateway import HttpModelGatewayClient

REPO = Path(__file__).resolve().parents[4]
COMPOSITIONS = REPO / "harness" / "compositions"


def test_t10_fake_gateway_refused_when_plug_bindings_pin_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T10: plug_bindings.gateway_base_url + FakeModelGatewayClient → refused."""
    monkeypatch.setenv("HARNESS_CLI", "yal-harness")
    yal = tmp_path / ".yal"
    yal.mkdir()
    (yal / "plug_bindings.json").write_text(
        json.dumps(
            {
                "gateway_base_url": "http://gateway.test:8090",
                "model_route": "githi",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    seams = RuntimeSeams(model_gateway=FakeModelGatewayClient())
    with pytest.raises(ValueError, match="FakeModelGatewayClient"):
        build_spawn_plan(
            session_dir=str(tmp_path),
            model="githi",
            provider=None,
            seams=seams,
        )


def test_t10_http_gateway_accepted_with_plug_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """plug_bindings + HttpModelGatewayClient is the allowed pairing."""
    monkeypatch.setenv("HARNESS_CLI", "yal-harness")
    monkeypatch.setenv("GATEWAY_TOKEN", "vk_will_be_replaced_by_implementers")
    yal = tmp_path / ".yal"
    yal.mkdir()
    (yal / "plug_bindings.json").write_text(
        json.dumps(
            {
                "gateway_base_url": "http://gateway.test:8090",
                "model_route": "echo",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    seams = RuntimeSeams(
        model_gateway=HttpModelGatewayClient(
            base_url="http://gateway.test:8090", token="svc"
        )
    )
    # May still fail spawn credential hygiene (§6 B) — that is covered elsewhere.
    # Here we only require Fake refusal is not the only gate; HTTP is accepted type-wise.
    try:
        plan = build_spawn_plan(
            session_dir=str(tmp_path),
            model="echo",
            provider=None,
            seams=seams,
        )
    except ValueError as exc:
        assert "FakeModelGatewayClient" not in str(exc)
        raise
    assert "openai" in plan.argv or plan.env.get("OPENAI_BASE_URL")


def test_offered_run_compositions_include_gateway_plug_bindings() -> None:
    """§6 B: offered Run compositions pin gateway_base_url + model_route."""
    assert COMPOSITIONS.is_dir(), COMPOSITIONS
    offered: list[Path] = []
    missing: list[str] = []
    for path in sorted(COMPOSITIONS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") != "offered":
            continue
        offered.append(path)
        plug = data.get("plug_bindings") or {}
        if not plug.get("gateway_base_url") or not plug.get("model_route"):
            missing.append(path.name)
    assert offered, "expected at least one offered composition under harness/compositions"
    assert not missing, (
        "offered compositions missing plug_bindings.gateway_base_url + model_route: "
        + ", ".join(missing)
    )
