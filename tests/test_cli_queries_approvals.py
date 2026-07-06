# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only `approvals` query command

from __future__ import annotations

import argparse

import pytest

from hub_e2e_helpers import _free_port, running_hub
from synapse_channel import cli_queries
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.operator_relay import RELAY_RELEASE
from synapse_channel.core.operator_relay_wire import RelayActionRequest


def _pending(operator: str, *, task: str, namespace: str = "MY-NS") -> RelayActionRequest:
    return RelayActionRequest(
        action=RELAY_RELEASE,
        namespace=namespace,
        task_id=task,
        operator=operator,
        origin_hub_id="syn-origin",
    )


async def test_approvals_lists_the_pending_quorum_with_the_remedy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A relay awaiting its second operator rides in the state snapshot; the query renders it.
    hub = SynapseHub()
    hub.relay_approvals.submit(_pending("alice", task="MY-NS/build"))
    async with running_hub(hub) as (_, uri):
        code = await cli_queries._approvals(uri=uri, name="U")

    assert code == 0
    out = capsys.readouterr().out
    assert "Pending approvals (1:" in out
    assert "release on MY-NS/MY-NS/build" in out
    assert "requested by alice" in out
    assert "awaiting a different operator" in out
    assert "synapse federation relay" in out  # the exact approve remedy


async def test_approvals_states_an_empty_ledger_plainly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        assert await cli_queries._approvals(uri=uri, name="U") == 0

    assert "Pending approvals: none" in capsys.readouterr().out


async def test_approvals_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        await cli_queries._approvals(
            uri=f"ws://127.0.0.1:{_free_port()}", name="U", ready_timeout=0.1
        )
        == 1
    )
    assert "Could not reach hub" in capsys.readouterr().out


def test_cmd_approvals_dispatches_real_query() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="U",
        token=None,
        ready_timeout=0.1,
    )
    assert cli_queries._cmd_approvals(ns) == 1


def test_render_approvals_lists_oldest_first_with_remedy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = {
        "pending_relay_approvals": [
            {"action": "release", "namespace": "A", "task_id": "A/build", "requester": "alice"},
            {"action": "release", "namespace": "B", "task_id": "B/lint", "requester": "carol"},
        ]
    }

    cli_queries._render_approvals(snapshot)

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Pending approvals (2: relays awaiting a second, different operator):"
    assert "release on A/A/build  requested by alice" in lines[1]
    assert "release on B/B/lint  requested by carol" in lines[2]
    assert "synapse federation relay" in lines[-1]


def test_render_approvals_states_absence(capsys: pytest.CaptureFixture[str]) -> None:
    cli_queries._render_approvals({})

    assert "Pending approvals: none" in capsys.readouterr().out
