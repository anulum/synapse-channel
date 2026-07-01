# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the one-line hub status command (shell prompt / tmux bar)

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_status
from synapse_channel.cli_status import (
    HubStatus,
    _count_word,
    _len_of,
    _tally,
    add_parsers,
    query_status,
    render_status_line,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType

REPO_ROOT = Path(__file__).resolve().parents[1]


def _repo_text(relative_path: str) -> str:
    """Read a repository file for the status-command documentation contract checks."""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


# --- pure rendering -----------------------------------------------------------


@pytest.mark.parametrize(
    ("count", "singular", "expected"),
    [(0, "agent", "0 agents"), (1, "agent", "1 agent"), (2, "claim", "2 claims")],
)
def test_count_word_pluralises_only_beyond_one(count: int, singular: str, expected: str) -> None:
    assert _count_word(count, singular) == expected


def test_render_reachable_shows_agents_and_claims() -> None:
    line = render_status_line(HubStatus(reachable=True, online=5, claims=2))
    assert line == "synapse ● 5 agents · 2 claims"


def test_render_appends_resources_only_when_present() -> None:
    assert "resource" not in render_status_line(HubStatus(reachable=True, online=1, claims=0))
    with_resources = render_status_line(HubStatus(reachable=True, online=1, claims=0, resources=3))
    assert with_resources == "synapse ● 1 agent · 0 claims · 3 resources"


def test_render_singular_forms_for_one_of_each() -> None:
    line = render_status_line(HubStatus(reachable=True, online=1, claims=1, resources=1))
    assert line == "synapse ● 1 agent · 1 claim · 1 resource"


def test_render_plain_is_ascii_only() -> None:
    line = render_status_line(HubStatus(reachable=True, online=7, claims=3), plain=True)
    assert line == "synapse online 7 agents 3 claims"
    assert "●" not in line and "·" not in line


def test_render_offline_default_and_plain() -> None:
    assert render_status_line(HubStatus(reachable=False)) == "synapse ○ offline"
    assert render_status_line(HubStatus(reachable=False), plain=True) == "synapse offline"


# --- tallying raw replies -----------------------------------------------------


def test_tally_counts_roster_excluding_probe_and_reads_state() -> None:
    seen: dict[str, dict[str, Any]] = {
        MessageType.WHO_SNAPSHOT: {
            "type": MessageType.WHO_SNAPSHOT,
            "online_agents": ["alpha", "beta", "USER-status"],
        },
        MessageType.STATE_SNAPSHOT: {
            "type": MessageType.STATE_SNAPSHOT,
            "snapshot": {"active_claims": [{"task_id": "t1"}], "resources": [{"kind": "gpu"}]},
        },
    }
    status = _tally(seen, probe="USER-status")
    assert status == HubStatus(reachable=True, online=2, claims=1, resources=1)


def test_tally_returns_zeroes_when_replies_absent() -> None:
    assert _tally({}, probe="USER-status") == HubStatus(reachable=True)


def test_tally_tolerates_malformed_fields() -> None:
    seen: dict[str, dict[str, Any]] = {
        MessageType.WHO_SNAPSHOT: {"online_agents": "not-a-list"},
        MessageType.STATE_SNAPSHOT: {"snapshot": "not-a-dict"},
    }
    assert _tally(seen, probe="USER-status") == HubStatus(reachable=True)


@pytest.mark.parametrize(
    ("value", "expected"),
    [([1, 2, 3], 3), ({"a": 1}, 1), (None, 0), (7, 0), ("xyz", 0)],
)
def test_len_of_sizes_only_sized_reply_fields(value: object, expected: int) -> None:
    assert _len_of(value) == expected


# --- live hub round trips -----------------------------------------------------


async def test_query_status_counts_online_agents_excluding_probe() -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        alpha = await connect_agent("alpha", uri)
        beta = await connect_agent("quantum/beta", uri)
        try:
            status = await query_status(uri=uri, name="USER")
        finally:
            await close_agents(alpha, beta)

    assert status.reachable is True
    assert status.online == 2  # the USER-status probe is filtered back out
    assert status.claims == 0


async def test_query_status_reports_unreachable_hub() -> None:
    status = await query_status(
        uri=f"ws://127.0.0.1:{_free_port()}", name="USER", ready_timeout=0.1
    )
    assert status == HubStatus(reachable=False)


# --- command dispatch ---------------------------------------------------------


def _namespace(*, uri: str, plain: bool = False, ready_timeout: float = 5.0) -> argparse.Namespace:
    """Build the parsed-args namespace the status dispatcher expects."""
    return argparse.Namespace(
        uri=uri, name="USER", plain=plain, token=None, ready_timeout=ready_timeout
    )


def test_cmd_status_prints_offline_and_exits_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli_status._cmd_status(
        _namespace(uri=f"ws://127.0.0.1:{_free_port()}", ready_timeout=0.1)
    )
    assert code == 1
    assert capsys.readouterr().out.strip() == "synapse ○ offline"


def test_cmd_status_plain_offline_is_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli_status._cmd_status(
        _namespace(uri=f"ws://127.0.0.1:{_free_port()}", plain=True, ready_timeout=0.1)
    )
    assert code == 1
    assert capsys.readouterr().out.strip() == "synapse offline"


async def test_cmd_status_reachable_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    import asyncio

    async with running_hub(SynapseHub()) as (_, uri):
        online = await connect_agent("solo", uri)
        try:
            code = await asyncio.to_thread(cli_status._cmd_status, _namespace(uri=uri))
        finally:
            await close_agents(online)

    assert code == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("synapse ● ") and "1 agent" in out


def test_add_parsers_routes_status_to_dispatcher() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_parsers(sub)
    args = parser.parse_args(["status", "--plain"])
    assert args.func is cli_status._cmd_status
    assert args.plain is True


# --- documentation contract ---------------------------------------------------


def test_status_is_documented_and_classified_stable() -> None:
    from synapse_channel.surface_taxonomy import CLI_TAXONOMY, STABLE

    assert CLI_TAXONOMY["status"] == STABLE
    assert "`status`" in _repo_text("docs/public-surface.md")
    assert "synapse status" in _repo_text("docs/cli.md")
