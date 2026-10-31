"""One set of seams per session, bound once on the session's gateway key.

Every seam that calls a model (the agent's gateway, the compaction summarizer,
nested-session subagents) must reach the composition's pinned gateway with the
session's key, and every path that uses a session's seams (turns, direct tool
invokes, memory and subagent operations) must use that same set.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest

from supervisor.app import SupervisorState, build_supervisor
from supervisor.catalog.core.types import DigestRef
from supervisor.catalog.loading.bootstrap import (
    bootstrap_composition_catalog,
    default_catalog_dir,
)
from supervisor.catalog.repository import hydrate_composition_catalog
from supervisor.db.engine import open_db
from supervisor.seams.providers.gateway.http_gateway import HttpModelGatewayClient
from tests.support.keys import FakeKeys
from tests.support.platform import all_variants, shipped_harness_catalog
from tests.support.session_env import TrackingProvider

PINNED_GATEWAY = "http://model-gateway:8090"  # harness/compositions/githi-v1.json


Boot = Callable[[], Awaitable[SupervisorState]]


@pytest.fixture
async def boot(tmp_path: Path) -> AsyncIterator[Boot]:
    keys = FakeKeys()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'seams.db'}") as (_, factory):

        async def start() -> SupervisorState:
            compositions = bootstrap_composition_catalog(default_catalog_dir())
            await hydrate_composition_catalog(compositions, factory)
            state = build_supervisor(
                session_factory=factory,
                catalog=all_variants(shipped_harness_catalog()),
                blob_dir=tmp_path / "blobs",
                sandbox_provider=TrackingProvider(),
                composition_catalog=compositions,
                virtual_key_client=keys,
            )
            state.keys = keys  # type: ignore[attr-defined]
            return state

        yield start


async def _create(state: SupervisorState) -> str:
    catalog = state.composition_catalog
    assert catalog is not None
    return await state.manager.create(
        tenant_id="t1",
        harness_version="githi-v1",
        composition_id="githi-v1",
        composition_digest=catalog.list_versions("composition", "githi-v1")[-1],
    )


def _assert_one_keyed_gateway(state: SupervisorState, sid: str, token: str) -> None:
    seams = state.manager._registry.get(sid).seams
    gateway = seams.model_gateway
    assert isinstance(gateway, HttpModelGatewayClient)
    assert (gateway._base, gateway._token) == (PINNED_GATEWAY, token)
    # The summarizer calls the model on the same client.
    assert seams.context_memory._gateway is gateway


async def test_every_model_calling_seam_uses_the_pinned_gateway_and_session_key(
    boot: Boot,
) -> None:
    state = await boot()
    sid = await _create(state)
    _assert_one_keyed_gateway(state, sid, "token-1")


async def test_the_session_key_admits_the_summarizer_its_memory_profile_names(
    boot: Boot,
) -> None:
    """A profile that compacts with its own summarizer gets that model in the session's key.

    A key limited to the session's model alone has the gateway refuse every
    summary, and compaction then silently falls back to dropping the span.
    The shipped default summarises with the session's own model.
    """
    state = await boot()
    await _create(state)
    assert state.keys.minted[-1]["allowed_models"] == ["echo"]  # type: ignore[attr-defined]

    catalog = state.composition_catalog
    assert catalog is not None
    default = catalog.get("memory_profile", "default", catalog.list_versions("memory_profile", "default")[-1])
    summarizing = catalog.offer(
        dataclasses.replace(default, name="summarizing", digest="", summarization_model="cheap-summary")
    )
    githi = catalog.get("composition", "githi-v1", catalog.list_versions("composition", "githi-v1")[-1])
    variant = catalog.offer(
        dataclasses.replace(
            githi,
            name="githi-summarizing",
            digest="",
            memory_profile=DigestRef(id=summarizing.name, digest=summarizing.digest),
        )
    )
    await state.manager.create(
        tenant_id="t1",
        harness_version="githi-v1",
        composition_id=variant.name,
        composition_digest=variant.digest,
    )
    assert state.keys.minted[-1]["allowed_models"] == ["echo", "cheap-summary"]  # type: ignore[attr-defined]


async def test_every_session_path_uses_the_runtimes_seams(boot: Boot) -> None:
    state = await boot()
    sid = await _create(state)
    row = await state.manager._get_session_row(sid)
    live = state.manager._registry.get(sid).seams
    assert state.manager._seams_for_session(sid, row) is live


async def test_a_resumed_session_is_rebound_the_same_way(boot: Boot) -> None:
    sid = await _create(await boot())
    fresh = await boot()  # new process, same database
    await fresh.manager.resume(sid)
    _assert_one_keyed_gateway(fresh, sid, "token-2")  # a fresh key for the new process


async def test_a_resumed_session_adopts_the_sandbox_its_last_worker_left(
    boot: Boot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its files are there, so it must not start an empty one (F21)."""
    first = await boot()
    sid = await _create(first)
    left = await first.manager._hands.ensure(sid)  # a tool call brought one up

    fresh = await boot()
    provider = fresh.manager._provider
    asked: list[str] = []

    async def adopt(session_id: str) -> object:
        asked.append(session_id)
        return left

    monkeypatch.setattr(provider, "adopt", adopt)
    creates = len(provider.create_calls)  # type: ignore[attr-defined]
    await fresh.manager.resume(sid)
    assert asked == [sid]
    assert fresh.manager._hands.sandbox(sid) is left
    assert len(provider.create_calls) == creates  # type: ignore[attr-defined]


async def test_a_renewed_session_key_rekeys_every_model_calling_seam(boot: Boot) -> None:
    """The fake mints keys that are already expiring, so the next model step renews.

    The key lives in the vault; the renewed one replaces it there and on the live
    gateway client the summarizer and subagents share.
    """
    state = await boot()
    sid = await _create(state)
    _assert_one_keyed_gateway(state, sid, "token-1")
    assert await state.manager.renew_gateway_key(sid)
    _assert_one_keyed_gateway(state, sid, "token-2")
    assert state.manager._session_keys is not None
    stored = await state.manager._session_keys.stored(sid)
    assert stored is not None and stored.value == "token-2"
    await state.manager.terminate(sid)
    assert await state.manager._session_keys.stored(sid) is None


async def test_a_key_the_gateway_refused_is_replaced_once_for_every_worker(boot: Boot) -> None:
    """The gateway no longer takes the session's key (its signing secret changed): the
    first worker to meet the refusal mints the next key; a worker that meets it after
    takes that key instead of minting another."""
    state = await boot()
    state.keys.lifetime_s = 3600  # type: ignore[attr-defined]  # far from expiry
    sid = await _create(state)
    _assert_one_keyed_gateway(state, sid, "token-1")
    assert not await state.manager.renew_gateway_key(sid), "a live key is not renewed"

    assert await state.manager.renew_gateway_key(sid, refused="token-1")
    _assert_one_keyed_gateway(state, sid, "token-2")
    minted = len(state.keys.minted)  # type: ignore[attr-defined]
    # Another worker met the same refusal: it takes token-2, nothing more is minted.
    assert not await state.manager.renew_gateway_key(sid, refused="token-1")
    assert len(state.keys.minted) == minted  # type: ignore[attr-defined]
    assert state.manager._session_keys is not None
    stored = await state.manager._session_keys.stored(sid)
    assert stored is not None and stored.value == "token-2"
