# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the one-line hub status command (shell prompt / tmux bar)

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_status
from synapse_channel.cli_status import (
    HubStatus,
    _count_word,
    _len_of,
    _tally,
    add_parsers,
    query_status,
    render_status_line,
    status_to_json,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_fold import fold_observed_state
from synapse_channel.core.multihub_merge import HubEvent
from synapse_channel.core.protocol import MessageType
from synapse_channel.observed_peers import ObservedPeerSnapshot

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


def _namespace(
    *,
    uri: str,
    plain: bool = False,
    ready_timeout: float = 5.0,
    as_json: bool = False,
    watch: bool = False,
    interval: float = 0.01,
    count: int = 0,
) -> argparse.Namespace:
    """Build the parsed-args namespace the status dispatcher expects."""
    return argparse.Namespace(
        uri=uri,
        name="USER",
        plain=plain,
        token=None,
        ready_timeout=ready_timeout,
        json=as_json,
        watch=watch,
        interval=interval,
        count=count,
        observed_peers=[],
        observed_token=None,
        observed_timeout=10.0,
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


class _SilentStatusAgent:
    """Connects and reports ready, but neither snapshot ever arrives."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.running = True

    async def connect(self) -> None:
        while self.running:
            await asyncio.sleep(0.01)

    async def wait_until_ready(self, *, timeout: float) -> bool:
        return True

    async def request_who(self) -> None:
        return None

    async def request_state(self) -> None:
        return None


async def test_query_status_tallies_zeros_when_snapshots_never_arrive() -> None:
    """A ready hub that answers nothing still yields a reachable zero status."""
    status = await query_status(
        uri="ws://unused",
        agent_factory=_SilentStatusAgent,  # type: ignore[arg-type]
        attempts=2,
    )
    assert status.reachable is True
    assert (status.online, status.claims, status.resources) == (0, 0, 0)


def test_render_appends_waiters_only_when_present() -> None:
    assert "waiter" not in render_status_line(HubStatus(reachable=True, online=1, claims=0))
    line = render_status_line(HubStatus(reachable=True, online=1, claims=0, waiters=4))
    assert "4 waiters" in line


def test_tally_counts_waiter_sidecars_apart_from_agents() -> None:
    """A wake-listener socket is presence plumbing, not an agent.

    Pins the inflated-fleet defect: 166 ``-rx`` waiters (most for dead
    terminals) once made a ~30-terminal workstation report 200 online agents.
    """
    seen: dict[str, dict[str, Any]] = {
        MessageType.WHO_SNAPSHOT: {
            "type": MessageType.WHO_SNAPSHOT,
            "online_agents": ["alpha", "alpha-rx", "beta-rx", "USER-status"],
        },
        MessageType.STATE_SNAPSHOT: {
            "type": MessageType.STATE_SNAPSHOT,
            "snapshot": {"active_claims": []},
        },
    }
    status = _tally(seen, probe="USER-status")
    assert status == HubStatus(reachable=True, online=1, claims=0, waiters=2)


def test_cmd_status_json_offline_reports_unreachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli_status._cmd_status(
        _namespace(uri=f"ws://127.0.0.1:{_free_port()}", ready_timeout=0.1, as_json=True)
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "reachable": False,
        "online": 0,
        "claims": 0,
        "resources": 0,
        "waiters": 0,
        "observed_peers": [],
        "observed_claims": 0,
        "observed_max_lag": None,
        "observed_max_clock_skew_seconds": None,
    }


async def test_cmd_status_json_carries_the_live_counts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    async with running_hub(SynapseHub()) as (_, uri):
        online = await connect_agent("solo", uri)
        try:
            code = await asyncio.to_thread(
                cli_status._cmd_status, _namespace(uri=uri, as_json=True)
            )
        finally:
            await close_agents(online)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reachable"] is True
    assert payload["online"] == 1


def test_parser_accepts_status_json() -> None:
    args = cli.build_parser().parse_args(["status", "--json"])
    assert args.json is True


def test_parser_accepts_status_observed_peer_flags() -> None:
    args = cli.build_parser().parse_args(
        [
            "status",
            "--observed-peer",
            "east=ws://127.0.0.1:8877",
            "--observed-token",
            "secret",
            "--observed-timeout",
            "3.5",
        ]
    )

    assert args.observed_peers[0].hub_id == "east"
    assert args.observed_peers[0].uri == "ws://127.0.0.1:8877"
    assert args.observed_token == "secret"
    assert args.observed_timeout == 3.5


def test_status_line_and_json_include_observed_peer_counts() -> None:
    observed = ObservedPeerSnapshot(
        hub_id="east",
        uri="ws://east",
        reachable=True,
        cursor=2,
        log_end_seq=3,
        clock_skew_seconds=-6.5,
        state=fold_observed_state(
            [HubEvent("east", 2, 2.0, EventKind.CLAIM, {"task_id": "T", "owner": "a"})]
        ),
    )
    status = HubStatus(reachable=True, online=1, claims=0, observed_peers=(observed,))

    assert "1 observed peer" in render_status_line(status, plain=True)
    assert "1 observed claim" in render_status_line(status, plain=True)
    assert "max skew -6.500s" in render_status_line(status, plain=True)
    payload = status_to_json(status)
    assert payload["observed_claims"] == 1
    assert payload["observed_max_lag"] == 1
    assert payload["observed_max_clock_skew_seconds"] == -6.5


# --- watch mode -----------------------------------------------------------------


class _FakeTty:
    """A text sink whose isatty answer is scripted, recording every write."""

    def __init__(self, *, tty: bool) -> None:
        self._tty = tty
        self.chunks: list[str] = []

    def isatty(self) -> bool:
        return self._tty

    def write(self, text: str) -> int:
        self.chunks.append(text)
        return len(text)

    def flush(self) -> None:
        return None


def test_watch_offline_appends_lines_and_exits_one() -> None:
    out = _FakeTty(tty=False)
    code = asyncio.run(
        cli_status.watch_status(
            uri=f"ws://127.0.0.1:{_free_port()}",
            ready_timeout=0.05,
            interval=0.01,
            count=2,
            out=out,  # type: ignore[arg-type]
        )
    )
    assert code == 1
    text = "".join(out.chunks)
    assert text.count("synapse ○ offline\n") == 2
    assert "\r" not in text


def test_watch_json_emits_ndjson() -> None:
    out = _FakeTty(tty=False)
    code = asyncio.run(
        cli_status.watch_status(
            uri=f"ws://127.0.0.1:{_free_port()}",
            ready_timeout=0.05,
            interval=0.01,
            count=2,
            as_json=True,
            out=out,  # type: ignore[arg-type]
        )
    )
    assert code == 1
    lines = "".join(out.chunks).strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["reachable"] is False for line in lines)


def test_watch_tty_rewrites_in_place_and_restores_the_newline() -> None:
    out = _FakeTty(tty=True)
    asyncio.run(
        cli_status.watch_status(
            uri=f"ws://127.0.0.1:{_free_port()}",
            ready_timeout=0.05,
            interval=0.01,
            count=2,
            plain=True,
            out=out,  # type: ignore[arg-type]
        )
    )
    text = "".join(out.chunks)
    assert text.count("\r\x1b[2K") == 2
    assert text.endswith("\n")


async def test_watch_reachable_hub_exits_zero() -> None:
    out = _FakeTty(tty=False)
    async with running_hub(SynapseHub()) as (_, uri):
        online = await connect_agent("solo", uri)
        try:
            code = await asyncio.to_thread(
                asyncio.run,
                cli_status.watch_status(uri=uri, interval=0.01, count=1, out=out),  # type: ignore[arg-type]
            )
        finally:
            await close_agents(online)
    assert code == 0
    assert "1 agent" in "".join(out.chunks)


def test_cmd_status_watch_requires_a_positive_interval(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli_status._cmd_status(_namespace(uri="ws://127.0.0.1:1", watch=True, interval=0.0))
    assert code == 2
    assert "--interval must be positive" in capsys.readouterr().err


def test_cmd_status_watch_dispatches_and_bounds_refreshes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli_status._cmd_status(
        _namespace(
            uri=f"ws://127.0.0.1:{_free_port()}",
            ready_timeout=0.05,
            watch=True,
            count=2,
        )
    )
    assert code == 1
    assert capsys.readouterr().out.count("synapse ○ offline") == 2


def test_cmd_status_watch_keyboard_interrupt_is_a_clean_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupt(_coro: Any) -> int:
        _coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.cli_status.asyncio.run", interrupt)
    code = cli_status._cmd_status(_namespace(uri="ws://127.0.0.1:1", watch=True))
    assert code == 0


def test_parser_accepts_watch_flags() -> None:
    args = cli.build_parser().parse_args(["status", "--watch", "--interval", "0.5", "--count", "3"])
    assert args.watch is True
    assert args.interval == 0.5
    assert args.count == 3


def test_cmd_status_refuses_a_stray_observed_pin(capsys: pytest.CaptureFixture[str]) -> None:
    """A pin naming a hub that --observed-peer does not fetch exits 2 before connecting."""
    args = cli.build_parser().parse_args(
        ["status", "--uri", "ws://127.0.0.1:1", "--observed-pin", "ghost=sha256:" + "a" * 64]
    )
    assert cli_status._cmd_status(args) == 2
    assert "does not fetch" in capsys.readouterr().err
