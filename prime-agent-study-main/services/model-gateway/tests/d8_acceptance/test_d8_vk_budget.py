"""D8 T13 — VK max_budget_usd must ENFORCE (VIRTUAL_KEY_BUDGET_EXCEEDED)."""

from __future__ import annotations

from model_gateway.virtual_keys import (
    MemoryVirtualKeyStore,
    MintRequest,
    enforce_virtual_key_budget,
)


def test_t13_vk_budget_enforced() -> None:
    store = MemoryVirtualKeyStore()
    claims = store.mint(
        MintRequest(
            tenant_id="t",
            session_id="s",
            principal_id="p",
            allowed_models=("echo",),
            max_budget_usd=0.01,
        )
    )
    store.add_spend(claims.token, 0.02)
    try:
        enforce_virtual_key_budget(store, claims.token)
        raised = False
    except PermissionError as exc:
        raised = True
        assert "VIRTUAL_KEY_BUDGET_EXCEEDED" in str(exc)
    assert raised, "D8: max_budget_usd advertised but not enforced"


def test_t13_docs_claim_enforced() -> None:
    from pathlib import Path

    readme = Path(__file__).resolve().parents[2] / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "VIRTUAL_KEY_BUDGET_EXCEEDED" in text or "max_budget_usd is enforced" in text
