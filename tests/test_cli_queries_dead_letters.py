# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only `dead-letters` query command

from __future__ import annotations

import argparse

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_queries
from synapse_channel.core.hub import SynapseHub


async def test_dead_letters_lists_the_blackhole_with_a_drain_remedy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        sender = await connect_agent("SPEAKER", uri)
        try:
            await sender.agent.send_message("chat", target="GHOST/coordinator", payload="anyone?")
            await sender.agent.send_message("chat", target="all", payload="broadcast is fine")
            code = await cli_queries._dead_letters(uri=uri, name="U")
        finally:
            await close_agents(sender)

    assert code == 0
    out = capsys.readouterr().out
    assert "Dead letters (1:" in out
    assert "GHOST/coordinator" in out
    assert "count=1" in out
    assert "from=SPEAKER" in out
    assert "broadcast is fine" not in out  # audiences are not dead letters
    assert "syn inbox --as GHOST/coordinator" in out  # the exact drain remedy


async def test_dead_letters_states_an_empty_ledger_plainly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        assert await cli_queries._dead_letters(uri=uri, name="U") == 0

    assert "Dead letters: none" in capsys.readouterr().out


async def test_dead_letters_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        await cli_queries._dead_letters(
            uri=f"ws://127.0.0.1:{_free_port()}", name="U", ready_timeout=0.1
        )
        == 1
    )
    assert "Could not reach hub" in capsys.readouterr().out


def test_cmd_dead_letters_dispatches_real_query() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="U",
        token=None,
        ready_timeout=0.1,
    )
    assert cli_queries._cmd_dead_letters(ns) == 1


def test_render_dead_letters_formats_worst_first_with_remedy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = {
        "dead_letters": [
            {"target": "A/coord", "count": 3, "last_ts": 0.0, "last_sender": "X"},
            {"target": "B/coord", "count": 1, "last_ts": 0.0, "last_sender": "Y"},
        ]
    }

    cli_queries._render_dead_letters(snapshot)

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Dead letters (2: directed messages the hub delivered to nobody live):"
    assert "A/coord  count=3 from=X" in lines[1]
    assert "B/coord  count=1 from=Y" in lines[2]
    assert lines[-1] == "  drain a name's backlog: syn inbox --as A/coord"


def test_render_dead_letters_states_absence(capsys: pytest.CaptureFixture[str]) -> None:
    cli_queries._render_dead_letters({})

    assert "Dead letters: none" in capsys.readouterr().out
