# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded mailbox-pending WHO display tests

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.cli_mailbox_pending import (
    DEFAULT_MAILBOX_PENDING_LIMIT,
    build_pending_display,
    render_mailbox_pending,
)
from synapse_channel.cli_query_who import _who
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def test_pending_display_orders_by_count_then_identity_after_project_filter() -> None:
    display = build_pending_display(
        {"PROJ/a": 2, "PROJ/b": 5, "PROJ/zero": 0, "OTHER/larger": 9},
        project="PROJ",
        limit=1,
    )

    assert display.rows == (("PROJ/b", 5),)
    assert display.total_identities == 2
    assert display.total_messages == 7
    assert display.hidden_identities == 1


def test_pending_display_refuses_a_nonpositive_internal_limit() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        build_pending_display({"A": 1}, limit=0)


def test_default_render_is_bounded_and_all_view_is_explicit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    counts = {f"seat/{index:02d}": index for index in range(1, 23)}

    render_mailbox_pending(counts, project=None)
    bounded = capsys.readouterr().out
    assert "Mailbox pending (22 identities, 253 messages; showing top 20 by count):" in bounded
    assert "22 undelivered messages pending for seat/22" in bounded
    assert "3 undelivered messages pending for seat/03" in bounded
    assert "seat/02" not in bounded
    assert "... 2 more identities" in bounded
    assert "--all-mailbox-pending" in bounded
    assert len([line for line in bounded.splitlines() if "undelivered" in line]) == (
        DEFAULT_MAILBOX_PENDING_LIMIT
    )

    render_mailbox_pending(counts, project=None, show_all=True)
    expanded = capsys.readouterr().out
    assert "showing top" not in expanded
    assert "1 undelivered message pending for seat/01" in expanded
    assert len([line for line in expanded.splitlines() if "undelivered" in line]) == 22


def test_render_preserves_unavailable_empty_and_singular_verdicts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    render_mailbox_pending(None, project=None)
    assert "Mailbox pending: unavailable" in capsys.readouterr().out

    render_mailbox_pending({"A": 0}, project=None)
    assert capsys.readouterr().out == "Mailbox pending: none\n"

    render_mailbox_pending({"A": 1}, project=None)
    singular = capsys.readouterr().out
    assert "Mailbox pending (1 identity, 1 message):" in singular
    assert "1 undelivered message pending for A" in singular


async def test_real_who_defaults_to_top_twenty_and_all_flag_expands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
            sender = await connect_agent("sender", uri)
            try:
                for index in range(25):
                    await sender.agent.chat("pending", target=f"offline/{index:02d}")
                loop = asyncio.get_running_loop()
                deadline = loop.time() + 2.0
                while (
                    len(tuple(store.iter_events(kinds=(EventKind.CHAT,)))) < 25
                    and loop.time() < deadline
                ):
                    await asyncio.sleep(0.01)
                assert len(tuple(store.iter_events(kinds=(EventKind.CHAT,)))) == 25

                assert await _who(uri=uri, name="observer") == 0
                bounded = capsys.readouterr().out
                assert "25 identities, 25 messages; showing top 20" in bounded
                assert "offline/00" in bounded
                assert "offline/19" in bounded
                assert "offline/20" not in bounded

                assert await _who(uri=uri, name="observer", all_mailbox_pending=True) == 0
                expanded = capsys.readouterr().out
                assert "showing top" not in expanded
                assert "offline/24" in expanded
            finally:
                await close_agents(sender)
    finally:
        store.close()
