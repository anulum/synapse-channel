# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real profile failure during mutex cleanup
"""Exercise a real signing-profile failure after the held command has finished."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.cli_locking import _lock
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_keys import generate_signing_key, write_signing_key
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
)
from synapse_channel.core.persistence import EventStore


@pytest.mark.asyncio
@pytest.mark.parametrize("child_exit", [0, 7])
async def test_profile_key_failure_preserves_the_finished_child_result(
    tmp_path: Path, child_exit: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """A physically corrupted fixture key makes cleanup unknown without a traceback."""
    key_path = tmp_path / "test-only-signing-key.pem"
    key = generate_signing_key()
    write_signing_key(key_path, key)
    trust = EventSignatureTrustBundle(
        keys={
            "test-profile": EventSignatureKey.from_private_key(
                key_id="test-profile", private_key=key, senders=frozenset({"profile-owner"})
            )
        },
        replay_cache=MessageReplayCache(window_seconds=30.0, max_entries=64),
    )
    journal = EventStore(tmp_path / "hub.db")

    def profile_agent(
        name: str,
        receive: Callable[[dict[str, Any]], Awaitable[None]],
        **options: Any,
    ) -> SynapseAgent:
        """Build the real SDK with the same explicit on-disk signing profile."""
        return SynapseAgent(
            name,
            receive,
            identity_key_path=str(key_path),
            identity_key_id="test-profile",
            **options,
        )

    try:
        async with running_hub(
            SynapseHub(journal=journal, identity_trust_bundle=trust, require_identity_binding=True)
        ) as (hub, uri):
            result = await _lock(
                uri=uri,
                name="profile-owner",
                task_id="profile-mutex",
                command=[
                    sys.executable,
                    "-c",
                    "import sys; from pathlib import Path; "
                    "Path(sys.argv[1]).write_text('invalid test fixture key'); "
                    "raise SystemExit(int(sys.argv[2]))",
                    str(key_path),
                    str(child_exit),
                ],
                paths=[],
                wait_timeout=0,
                agent_factory=profile_agent,
            )
            assert result == (3 if child_exit == 0 else child_exit)
            output = capsys.readouterr()
            assert "lock: release unknown" in output.err
            assert f"child exit={child_exit}" in output.err
            assert str(key_path) not in output.err
            assert "invalid test fixture key" not in output.err
            assert "Traceback" not in output.err
            assert hub.state.claims["profile-mutex"].owner == "profile-owner"
            assert not any(row.kind == "release" for row in journal.iter_events())
    finally:
        journal.close()
