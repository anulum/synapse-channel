# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only hub query commands (who/state/board/manifest/health)

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli_queries
from synapse_channel.core.hub import SynapseHub

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_repo_text(relative_path: str) -> str:
    """Read repository documentation for who-command contract checks."""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _single_spaced(text: str) -> str:
    """Normalize documentation whitespace for phrase checks."""
    return " ".join(text.split())


async def test_who_lists_project_agents(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        quantum_one = await connect_agent("quantum/agent-1", uri)
        quantum_two = await connect_agent("quantum/agent-2", uri)
        other = await connect_agent("other/agent-3", uri)
        try:
            code = await cli_queries._who(uri=uri, name="U", project="quantum")
        finally:
            await close_agents(quantum_one, quantum_two, other)

    assert code == 0
    out = capsys.readouterr().out
    assert "Online in quantum (2 agents · 0 waiters)" in out
    assert "quantum/agent-1" in out
    assert "other/agent-3" not in out


async def test_who_lists_all_without_project(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        b_handle = await connect_agent("b", uri)
        try:
            code = await cli_queries._who(uri=uri, name="a")
        finally:
            await close_agents(b_handle)

    assert code == 0
    assert "Online (2 agents · 0 waiters)" in capsys.readouterr().out


async def test_who_counts_waiter_sidecars_apart_from_agents(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A wake-listener sidecar must never inflate the agent count.

    This pins the defect where a workstation with ~30 terminals reported 200
    "online agents": every ``-rx`` waiter held a live socket and was counted as
    an agent. The roster now reads agents and waiters apart.
    """
    async with running_hub(SynapseHub()) as (_, uri):
        agent = await connect_agent("quantum/agent-1", uri)
        waiter = await connect_agent("quantum/agent-1-rx", uri)
        try:
            code = await cli_queries._who(uri=uri, name="U")
        finally:
            await close_agents(agent, waiter)

    assert code == 0
    out = capsys.readouterr().out
    assert "Online (2 agents · 1 waiter" in out.replace("waiters", "waiter")
    assert "Waiters (1):" in out
    assert "  quantum/agent-1-rx" in out


async def test_who_project_filter_applies_to_waiters_too(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        agent = await connect_agent("quantum/agent-1", uri)
        waiter = await connect_agent("quantum/agent-1-rx", uri)
        foreign = await connect_agent("other/agent-2-rx", uri)
        try:
            code = await cli_queries._who(uri=uri, name="U", project="quantum")
        finally:
            await close_agents(agent, waiter, foreign)

    assert code == 0
    out = capsys.readouterr().out
    assert "Online in quantum (1 agents · 1 waiters)" in out
    assert "other/agent-2-rx" not in out


async def test_who_me_reports_presence_and_waiter_without_creating_subject_presence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        waiter = await connect_agent("demo/agent-rx", uri)
        try:
            code = await cli_queries._who(uri=uri, name="demo/agent", me=True)
        finally:
            await close_agents(waiter)

    assert code == 0
    out = capsys.readouterr().out
    assert "Me: demo/agent" in out
    assert "presence: missing" in out
    assert "waiter: online (demo/agent-rx)" in out
    assert "demo/agent-who" not in out


async def test_who_me_reports_online_presence_and_missing_waiter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        agent = await connect_agent("demo/agent", uri)
        try:
            code = await cli_queries._who(uri=uri, name="demo/agent", me=True)
        finally:
            await close_agents(agent)

    assert code == 0
    out = capsys.readouterr().out
    assert "Me: demo/agent" in out
    assert "presence: online" in out
    assert "waiter: missing (demo/agent-rx)" in out
    assert "presence is not a wake loop" in out


async def test_who_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_queries._who(uri=f"ws://127.0.0.1:{_free_port()}", name="U", ready_timeout=0.1)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_query_hub_returns_quietly_when_no_matching_snapshot() -> None:
    rendered: list[str] = []
    async with running_hub(SynapseHub()) as (_, uri):
        code = await cli_queries._query_hub(
            uri=uri,
            name="U",
            token=None,
            response_type="not_a_real_snapshot_type",
            request=lambda agent: agent.request_who(),
            render=lambda value: rendered.append(str(value)),
            attempts=1,
        )
    assert code == 0
    assert rendered == []


def test_cmd_who_dispatches_real_query() -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="U",
        project=None,
        me=False,
        token=None,
        ready_timeout=0.1,
    )
    assert cli_queries._cmd_who(ns) == 1


def test_render_who_marks_a_deaf_agent_and_leaves_a_live_one_plain(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_queries._render_who(
        ["BETA", "GAMMA"],
        liveness={
            "BETA": {"proven_live": False, "has_waiter": False, "last_reaction_age": 245.0},
            "GAMMA": {"proven_live": True, "has_waiter": True, "last_reaction_age": 3.0},
        },
    )

    out = capsys.readouterr().out
    assert "BETA  (deaf ~4m)" in out  # 245s rounds to ~4m
    gamma_line = next(line for line in out.splitlines() if "GAMMA" in line)
    assert "(deaf" not in gamma_line  # a proven-live agent gets no marker


def test_render_who_flags_an_agent_that_never_reacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_queries._render_who(
        ["BETA"],
        liveness={"BETA": {"proven_live": False, "has_waiter": False, "last_reaction_age": None}},
    )

    assert "(deaf — no reaction seen)" in capsys.readouterr().out


def test_render_who_without_liveness_renders_the_plain_roster(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_queries._render_who(["BETA"])

    out = capsys.readouterr().out
    assert "  BETA" in out
    assert "(deaf" not in out


def test_render_who_formats_the_silence_age_in_seconds_minutes_and_hours(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_queries._render_who(
        ["S", "M", "H"],
        liveness={
            "S": {"proven_live": False, "last_reaction_age": 45.0},
            "M": {"proven_live": False, "last_reaction_age": 600.0},
            "H": {"proven_live": False, "last_reaction_age": 7200.0},
        },
    )

    out = capsys.readouterr().out
    assert "(deaf ~45s)" in out
    assert "(deaf ~10m)" in out
    assert "(deaf ~2h)" in out


async def test_who_marks_a_deaf_agent_end_to_end(capsys: pytest.CaptureFixture[str]) -> None:
    # A present agent with no waiter and no reaction, under a zero-second window, is deaf
    # the instant after it registers, so the roster flags it.
    hub = SynapseHub(warn_stale_recipients=True, recipient_liveness_window=0.0)
    async with running_hub(hub) as (_, uri):
        beta = await connect_agent("BETA", uri)
        try:
            code = await cli_queries._who(uri=uri, name="U")
        finally:
            await close_agents(beta)

    assert code == 0
    out = capsys.readouterr().out
    assert "BETA" in out
    assert "(deaf" in out


async def test_who_has_no_liveness_marker_when_tracking_is_off(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (_, uri):
        beta = await connect_agent("BETA", uri)
        try:
            code = await cli_queries._who(uri=uri, name="U")
        finally:
            await close_agents(beta)

    assert code == 0
    assert "(deaf" not in capsys.readouterr().out


def test_public_docs_explain_who_me_presence_and_waiter_distinction() -> None:
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("README.md"),
                _read_repo_text("docs/cli.md"),
                _read_repo_text("docs/recipes.md"),
            ]
        )
    )

    assert "syn who --me" in combined
    assert "synapse who --me" in combined
    assert "presence is not a wake loop" in combined
