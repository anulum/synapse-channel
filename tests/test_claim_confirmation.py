# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real exact-scope confirmation refusals
"""Reject malformed and mismatched confirmations at the live wire boundary."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import pytest

from claim_outcome_helpers import ClaimProxy, claim_proxy
from cli_e2e_helpers import git_repo
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.git.gitclaim import run_git_claim


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("snapshot", None),
        ("active_claims", None),
        ("active_claims", []),
        ("generated_at", None),
        ("generated_at", True),
        ("generated_at", "invalid"),
        ("generated_at", float("nan")),
        ("generated_at", 10**400),
        ("task_id", "other"),
        ("owner", "other"),
        ("worktree", "/other"),
        ("paths", ["wider/"]),
        ("path_identity", None),
        ("git", None),
        ("epoch", None),
        ("epoch", True),
        ("epoch", 0),
        ("epoch", 1.5),
        ("status", None),
        ("status", "done"),
        ("status", "invalid"),
        ("lease_expires_at", "invalid"),
        ("lease_expires_at", float("inf")),
        ("lease_expires_at", 0),
        ("duplicate", True),
        ("target", "other"),
        ("request_id", "old-response"),
    ],
)
async def test_invalid_confirmation_never_authorizes_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    """Corrupt a real snapshot and retain an unknown outcome without replay."""
    monkeypatch.chdir(git_repo(tmp_path / "repo"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    def corrupt(data: dict[str, Any]) -> None:
        """Change one real response field after the hub has granted the lease."""
        if data.get("type") != "state_snapshot":
            return
        if field in {"snapshot", "target", "request_id"}:
            data[field] = value
        elif field in {"active_claims", "generated_at"}:
            data["snapshot"][field] = value
        elif field == "duplicate":
            data["snapshot"]["active_claims"] *= 2
        else:
            data["snapshot"]["active_claims"][0][field] = value

    async with running_hub() as (hub, upstream):
        proxy = ClaimProxy(upstream, transform=corrupt)
        async with claim_proxy(proxy) as uri:
            result = await run_git_claim(
                uri=uri,
                name="me",
                task_id="T",
                paths=["a.py"],
                auto_release_on="manual",
                reply_timeout=0.1,
            )
        assert result == 3
        assert hub.state.claims["T"].owner == "me"
        assert proxy.requests.count("claim") == 1


async def test_confirmation_without_disk_store_filters_unrelated_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid confirmation ignores unrelated records without requiring persistence."""
    monkeypatch.chdir(git_repo(tmp_path / "repo"))

    def unrelated(data: dict[str, Any]) -> None:
        """Add unrelated wire records around the actual matching lease."""
        if data.get("type") == "state_snapshot":
            data["snapshot"]["active_claims"] += [None, {"task_id": "other"}]

    async with running_hub() as (_hub, upstream):
        async with claim_proxy(ClaimProxy(upstream, transform=unrelated)) as uri:
            assert (
                await run_git_claim(
                    uri=uri,
                    name="me",
                    task_id="T",
                    paths=["a.py"],
                    auto_release_on="manual",
                    reply_timeout=0.1,
                    agent_factory=partial(SynapseAgent, persist_lease_epochs=False),
                )
                == 0
            )


@pytest.mark.parametrize("request_id", [None, "", "x" * 129, "exact-opaque-id"])
async def test_state_confirmation_echo_is_bounded(request_id: str | None) -> None:
    """The real hub echoes only bounded non-empty string correlation ids."""
    async with running_hub() as (_hub, uri):
        handle = await connect_agent("reader", uri)
        try:
            await handle.agent.send_message(
                "state_request",
                target="System",
                request_id=request_id,
            )
            response = await handle.recorder.wait_for(
                lambda data: data.get("type") == "state_snapshot"
            )
            if request_id == "exact-opaque-id":
                assert response["request_id"] == request_id
            else:
                assert "request_id" not in response
        finally:
            await close_agents(handle)


async def test_unwritable_fence_storage_keeps_recovery_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost reply cannot recover successfully if later processes lack its fence."""
    monkeypatch.chdir(git_repo(tmp_path / "repo"))
    blocker = tmp_path / "blocked-data-home"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(blocker))
    async with running_hub() as (hub, upstream):
        async with claim_proxy(ClaimProxy(upstream)) as uri:
            result = await run_git_claim(
                uri=uri,
                name="me",
                task_id="T",
                paths=["a.py"],
                auto_release_on="manual",
                reply_timeout=0.1,
            )
        assert result == 3
        assert hub.state.claims["T"].epoch == 1
