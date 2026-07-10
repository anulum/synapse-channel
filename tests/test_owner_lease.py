# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — client-side ownership-lease persistence

"""Persisting the hub's ownership-lease token across client processes.

The file half is exercised directly on a temporary directory; the wire half
drives a real :class:`SynapseAgent` against a live hub and proves the full
client loop the waiter relies on: grant → persist → present → re-take.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

from hub_e2e_helpers import running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.owner_lease import (
    lease_agent_kwargs,
    lease_path,
    load_lease,
    save_lease,
)

NAME = "PROJ/persisted-owner"


def test_lease_path_flattens_the_identity_into_one_contained_file(tmp_path: Path) -> None:
    path = lease_path("quantum/claude-7f3a", base=tmp_path)
    assert path.parent == tmp_path
    assert "/" not in path.name
    assert path == tmp_path / "quantum%2Fclaude-7f3a"


def test_lease_path_defaults_to_the_synapse_home_sibling() -> None:
    path = lease_path("PROJ")
    assert path == Path.home() / "synapse" / "owner-lease" / "PROJ"


def test_a_missing_or_unreadable_file_reads_as_no_token(tmp_path: Path) -> None:
    assert load_lease(tmp_path / "absent") == ""
    unreadable = tmp_path / "unreadable"
    unreadable.write_text("secret-token", encoding="utf-8")
    unreadable.chmod(0o000)
    try:
        assert load_lease(unreadable) == ""
    finally:
        unreadable.chmod(0o600)


def test_save_and_load_round_trip_with_owner_only_permissions(tmp_path: Path) -> None:
    marker = tmp_path / "deep" / "lease"
    save_lease(marker, "token-123\n")
    assert load_lease(marker) == "token-123"
    mode = stat.S_IMODE(os.stat(marker).st_mode)
    assert mode == 0o600
    # No temporary residue from the atomic write.
    assert [p.name for p in marker.parent.iterdir()] == ["lease"]


def test_saving_an_empty_token_removes_the_stored_credential(tmp_path: Path) -> None:
    marker = tmp_path / "lease"
    save_lease(marker, "token-123")
    save_lease(marker, "")
    assert not marker.exists()
    assert load_lease(marker) == ""
    # Clearing an already-absent token is a no-op, never an error.
    save_lease(marker, "")


def test_lease_agent_kwargs_wires_the_full_triple_or_nothing(tmp_path: Path) -> None:
    assert lease_agent_kwargs(None) == {}
    marker = tmp_path / "lease"
    save_lease(marker, "stored-token")
    kwargs = lease_agent_kwargs(marker)
    assert kwargs["request_lease"] is True
    assert kwargs["owner_lease"] == "stored-token"
    kwargs["on_lease_granted"]("fresh-token")
    assert load_lease(marker) == "fresh-token"


async def test_the_agent_persists_the_grant_and_re_takes_the_name_with_it(
    tmp_path: Path,
) -> None:
    """The full client loop over a real hub: grant → persist → present → re-take."""
    marker = lease_path(NAME, base=tmp_path)
    async with running_hub() as (hub, uri):
        first = SynapseAgent(NAME, None, uri=uri, verbose=False, **lease_agent_kwargs(marker))
        first_task = asyncio.create_task(first.connect())
        assert await first.wait_until_ready(timeout=3.0)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 3.0
        while loop.time() < deadline and not load_lease(marker):
            await asyncio.sleep(0.01)
        token = load_lease(marker)
        assert token, "the granted token was not persisted"
        assert first.owner_lease == token
        first.running = False
        first_task.cancel()
        deadline = loop.time() + 3.0
        while loop.time() < deadline and hub.clients.agent_sockets.get(NAME) is not None:
            await asyncio.sleep(0.01)

        # A stranger (no token file) is refused in the reconnect gap...
        stranger = SynapseAgent(NAME, None, uri=uri, verbose=False)
        stranger_task = asyncio.create_task(stranger.connect())
        deadline = loop.time() + 3.0
        while loop.time() < deadline and stranger.last_close_code is None:
            await asyncio.sleep(0.01)
        assert stranger.last_close_code == 4016
        stranger_task.cancel()

        # ...while the owner's next process re-takes the name from the file alone.
        second = SynapseAgent(NAME, None, uri=uri, verbose=False, **lease_agent_kwargs(marker))
        second_task = asyncio.create_task(second.connect())
        assert await second.wait_until_ready(timeout=3.0)
        deadline = loop.time() + 3.0
        while loop.time() < deadline and hub.clients.agent_sockets.get(NAME) is None:
            await asyncio.sleep(0.01)
        assert hub.clients.agent_sockets.get(NAME) is not None
        assert load_lease(marker) == token, "an honoured lease must not be rotated"
        second.running = False
        second_task.cancel()
