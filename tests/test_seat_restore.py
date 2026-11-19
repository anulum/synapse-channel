# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for starting a cut seat behind its seat holder
"""The restore path of ``worker-session``: a cut seat starts only behind its holder.

The unit tests drive each answer of the holder through injected boundaries. Two
journeys use real tmux on a private socket, a real in-process hub and a real
``synapse seat-hold``: a free seat comes back, and a live one is left alone.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli_services, worker_session
from synapse_channel.agent_tmux import (
    CODEX_MANAGED_UPDATE_CONFIG,
    AgentTmuxConfig,
    AgentTmuxWakeResult,
    _managed_agent_command,
    _provider_family,
    agent_binary,
    provider_command,
)
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.seat_hold import (
    HUB_UNAVAILABLE_EXIT_CODE,
    READY_TIMEOUT_SECONDS,
    SEAT_LIVE_EXIT_CODE,
    SeatHolder,
    write_marker,
)
from synapse_channel.seat_restore import (
    HOLDER_START_ALLOWANCE_SECONDS,
    MARKER_EXIT_CODES,
    MARKER_WAIT_SECONDS,
    holder_command,
    start_restored_seat,
    wait_for_marker,
)

NONCE = "nonce-0002"


def _started(*, started: bool = True, returncode: int = 0) -> AgentTmuxWakeResult:
    return AgentTmuxWakeResult(
        injected=False, started=started, returncode=returncode, detail="tmux said no"
    )


class _Run:
    """Records what a restore asked of tmux and of its caller."""

    def __init__(self, word: str | None, *, started: AgentTmuxWakeResult | None = None):
        self.word = word
        self.result = started or _started()
        self.configs: list[AgentTmuxConfig] = []
        self.killed: list[tuple[str, str]] = []
        self.ready_panes: list[Sequence[str]] = []

    def start(self, config: AgentTmuxConfig) -> AgentTmuxWakeResult:
        self.configs.append(config)
        return self.result

    def kill(self, tmux_bin: str, session: str) -> None:
        self.killed.append((tmux_bin, session))

    def wait(self, _path: Path) -> str | None:
        return self.word

    def after_ready(self, pane: Sequence[str]) -> int:
        self.ready_panes.append(pane)
        return 7

    def restore(self, tmp_path: Path) -> int:
        return start_restored_seat(
            identity="T/seat",
            command=["codex", "resume", "x"],
            session="synapse-T_seat",
            cwd=tmp_path,
            uri="ws://h",
            token=None,
            token_file="/k/token",
            tmux_bin="tmux",
            synapse_bin="synapse",
            env={"XDG_RUNTIME_DIR": str(tmp_path)},
            after_ready=self.after_ready,
            start=self.start,
            kill=self.kill,
            wait=self.wait,
            nonce=NONCE,
        )


def test_the_pane_runs_the_agent_behind_the_holder(tmp_path: Path) -> None:
    pane = holder_command(
        synapse_bin="synapse",
        identity="T/seat",
        nonce=NONCE,
        marker_dir=tmp_path,
        uri="ws://h",
        token_file=None,
        command=["codex", "resume", "x"],
    )
    woken = holder_command(
        synapse_bin="synapse",
        identity="T/seat",
        nonce=NONCE,
        marker_dir=tmp_path,
        uri="ws://h",
        token_file=None,
        command=["codex"],
        wake_session="synapse-T_seat",
        tmux_bin="/x/tmux",
    )
    assert woken[-6:] == [
        "--wake-session",
        "synapse-T_seat",
        "--tmux-bin",
        "/x/tmux",
        "--",
        "codex",
    ]
    assert pane == [
        "synapse",
        "seat-hold",
        "--identity",
        "T/seat",
        "--nonce",
        NONCE,
        "--marker-dir",
        str(tmp_path),
        "--uri",
        "ws://h",
        "--",
        "codex",
        "resume",
        "x",
    ]
    with_token = holder_command(
        synapse_bin="s",
        identity="T/seat",
        nonce=NONCE,
        marker_dir=tmp_path,
        uri="ws://h",
        token_file="/k",
        command=["a"],
    )
    assert with_token[with_token.index("--token-file") + 1] == "/k"


def _restored(identity: str, command: Sequence[str], tmp_path: Path) -> AgentTmuxConfig:
    """The tmux configuration a restore makes: the agent behind its holder."""
    pane = holder_command(
        synapse_bin="/opt/bin/synapse",
        identity=identity,
        nonce=NONCE,
        marker_dir=tmp_path,
        uri="ws://h",
        token_file=None,
        command=command,
    )
    return AgentTmuxConfig(identity=identity, session="s", cwd=tmp_path, agent_command=tuple(pane))


def test_the_provider_is_the_agent_after_the_holders_separator(tmp_path: Path) -> None:
    """A seat's name never decides its provider: only the agent after ``--`` does."""
    codex_under_claude = _restored("CEO/claude-0001", ["codex", "resume", "x"], tmp_path)
    assert provider_command(codex_under_claude.agent_command) == ("codex", "resume", "x")
    assert _provider_family(codex_under_claude) == "codex"
    assert agent_binary(codex_under_claude) == "codex"
    claude_under_codex = _restored("SYNAPSE-CHANNEL/codex-2970473", ["claude", "-r"], tmp_path)
    assert _provider_family(claude_under_codex) == "claude"
    assert agent_binary(claude_under_codex) == "claude"
    # An agent run by its interpreter: options are skipped, and the script names it.
    script = ["node", "--max-old-space-size=4096", "/usr/lib/codex/bin/codex.js"]
    assert _provider_family(_restored("CEO/claude-0001", script, tmp_path)) == "codex"


def test_a_command_not_behind_a_holder_is_its_own_agent() -> None:
    mentions = ("codex", "exec", "seat-hold", "--", "x")  # an argument, not the holder's shape
    assert provider_command(mentions) == mentions
    unfinished = ("synapse", "seat-hold", "--identity", "CEO/claude-1")
    assert provider_command(unfinished) == unfinished
    assert provider_command([]) == ()


def test_a_restored_codex_seat_gets_the_update_override_as_the_agents_argument(
    tmp_path: Path,
) -> None:
    config = _restored("CEO/claude-0001", ["codex", "resume", "x"], tmp_path)
    managed = _managed_agent_command(config)
    assert managed[: len(config.agent_command)] == config.agent_command
    assert provider_command(managed) == (
        "codex",
        "resume",
        "x",
        "--config",
        CODEX_MANAGED_UPDATE_CONFIG,
    )


def test_an_owner_override_and_a_non_codex_agent_are_left_as_they_are(tmp_path: Path) -> None:
    owned = _restored(
        "CEO/claude-0001", ["codex", "-c", "check_for_update_on_startup=true"], tmp_path
    )
    assert _managed_agent_command(owned) == owned.agent_command
    claude = _restored("SYNAPSE-CHANNEL/codex-2970473", ["claude"], tmp_path)
    assert _managed_agent_command(claude) == claude.agent_command


def test_the_marker_wait_covers_the_holders_worst_case() -> None:
    """Start, welcome and the hub keeping the name: twice the registration timeout and more."""
    assert HOLDER_START_ALLOWANCE_SECONDS == 15.0
    assert MARKER_WAIT_SECONDS == 2 * READY_TIMEOUT_SECONDS + HOLDER_START_ALLOWANCE_SECONDS == 35.0


def test_the_wait_gives_up_on_no_marker_and_on_held_alone(tmp_path: Path) -> None:
    path = tmp_path / "marker"
    assert wait_for_marker(path, timeout=0.0) is None
    write_marker(path, "held X-seat n")
    assert wait_for_marker(path, timeout=0.0) is None
    write_marker(path, "failed X-seat n")
    assert wait_for_marker(path, timeout=0.0) == "failed"


def test_the_wait_waits_through_held_for_the_final_word(tmp_path: Path) -> None:
    path = tmp_path / "marker"
    steps = iter(["held X-seat n", "held X-seat n", "ready X-seat n"])
    ticks = iter(range(100))

    def sleep(_seconds: float) -> None:
        write_marker(path, next(steps))

    assert (
        wait_for_marker(path, timeout=50.0, clock=lambda: float(next(ticks)), sleep=sleep)
        == "ready"
    )


@pytest.mark.parametrize(
    ("word", "code"), [(word, code) for word, code in MARKER_EXIT_CODES.items() if code]
)
def test_each_final_refusal_decides_the_restore(word: str, code: int, tmp_path: Path) -> None:
    run = _Run(word)
    assert run.restore(tmp_path) == code
    assert run.ready_panes == []
    assert run.killed == [("tmux", "synapse-T_seat")]


def test_a_held_seat_goes_on_with_the_holders_command(tmp_path: Path) -> None:
    run = _Run("ready")
    assert run.restore(tmp_path) == 7
    (pane,) = run.ready_panes
    assert pane[:2] == ["synapse", "seat-hold"] and pane[-4:] == ["--", "codex", "resume", "x"]
    # The holder in the pane also wakes that pane, so no separate waiter is needed.
    assert pane[pane.index("--wake-session") + 1] == "synapse-T_seat"
    assert "--token-file" in pane
    # The registry gets the holder's command, so a restart of the pane goes through it.
    assert run.configs[0].agent_command == tuple(pane)
    assert run.configs[0].session == "synapse-T_seat"


def test_no_final_answer_ends_the_pane_and_stops_the_restore(tmp_path: Path) -> None:
    run = _Run(None)
    assert run.restore(tmp_path) == HUB_UNAVAILABLE_EXIT_CODE == 69
    assert run.killed == [("tmux", "synapse-T_seat")] and run.ready_panes == []


def test_an_existing_session_means_the_seat_is_live_here(tmp_path: Path) -> None:
    run = _Run("ready", started=_started(started=False))
    assert run.restore(tmp_path) == SEAT_LIVE_EXIT_CODE == 75
    # A session this restore did not create is never ended.
    assert run.ready_panes == [] and run.killed == []


def test_a_pane_that_cannot_be_started_stops_the_restore(tmp_path: Path) -> None:
    run = _Run("ready", started=_started(started=False, returncode=5))
    assert run.restore(tmp_path) == 69
    assert run.ready_panes == [] and run.killed == []


def test_worker_session_restore_arms_the_waiter_with_the_holders_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, Any] = {}

    def fake_restore(**kwargs: Any) -> int:
        calls["restore"] = kwargs
        return int(kwargs["after_ready"](["synapse", "seat-hold", "--", "codex"]))

    def record(name: str, result: Any = None) -> Any:
        def recorder(**kwargs: Any) -> Any:
            calls[name] = kwargs
            return result

        return recorder

    def no_real_tmux(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a restore never takes the plain tmux path")

    monkeypatch.setattr(worker_session, "start_restored_seat", fake_restore)
    # Every real tmux boundary is closed, so a wrong path fails here instead of
    # starting a real session on the user's tmux server.
    monkeypatch.setattr(worker_session, "_launch_terminal_worker", no_real_tmux)
    monkeypatch.setattr(worker_session, "start_session", no_real_tmux)
    monkeypatch.setattr(worker_session, "_record_started_pane", record("pane"))
    # The holder in the pane runs the waiter; a separate one would be a second process.
    monkeypatch.setattr(worker_session, "_start_tmux_waiter", no_real_tmux)
    monkeypatch.setattr(worker_session, "_attach_tmux_session", record("attach", 0))

    code = worker_session.run_worker_session(
        identity="T/seat",
        command=["codex"],
        restore=True,
        terminal_tmux="off",  # a restore always goes through tmux
        cwd=tmp_path,
        environ={"XDG_RUNTIME_DIR": str(tmp_path)},
    )

    assert code == 0
    assert calls["restore"]["session"] == "synapse-T_seat"
    assert calls["restore"]["env"]["SYN_TMUX_PROVIDER"] == "1"
    assert "waiter" not in calls
    assert calls["pane"]["command"] == ["synapse", "seat-hold", "--", "codex"]
    assert calls["attach"]["session"] == "synapse-T_seat"


def test_the_restore_flag_reaches_the_session_runner() -> None:
    seen: list[dict[str, Any]] = []

    def runner(**kwargs: Any) -> int:
        seen.append(kwargs)
        return 0

    parser = argparse.ArgumentParser()
    cli_services.add_parsers(parser.add_subparsers())
    for extra in (["--restore"], []):
        args = parser.parse_args(["worker-session", "--identity", "T/seat", *extra, "--", "codex"])
        assert cli_services._cmd_worker_session(args, session_runner=runner) == 0
    assert seen[0]["restore"] is True
    assert "restore" not in seen[1]


# --- journeys on real tmux, a real hub and a real seat holder ---------------------


@pytest.fixture
def private_tmux(tmp_path: Path) -> Iterator[str]:
    """A tmux wrapper on a private socket with no user configuration."""
    if not sys.platform.startswith("linux") or shutil.which("tmux") is None:
        pytest.skip("the seat holder needs Linux and tmux")
    socket = f"synapse-test-{uuid.uuid4().hex[:10]}"
    wrapper = tmp_path / "tmux-private"
    wrapper.write_text(f'#!/bin/sh\nexec tmux -L {socket} -f /dev/null "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    try:
        yield str(wrapper)
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True, check=False)
        socket_dir = Path(os.environ.get("TMUX_TMPDIR", "/tmp")) / f"tmux-{os.getuid()}"
        (socket_dir / socket).unlink(missing_ok=True)


def _synapse_wrapper(tmp_path: Path) -> str:
    """A ``synapse`` executable that runs this checkout's command line.

    The tmux server is started by the user manager, so the pane does not inherit
    this session's isolated ``XDG_DATA_HOME``; the wrapper sets it, and the
    holder presents the same test machine key as the clients in this process.
    """
    wrapper = tmp_path / "synapse"
    data_home = shlex.quote(os.environ["XDG_DATA_HOME"])
    runtime = shlex.quote(str(tmp_path))  # the holder's waiter keeps its registry here
    wrapper.write_text(
        f"#!/bin/sh\nXDG_DATA_HOME={data_home} XDG_RUNTIME_DIR={runtime} exec {sys.executable} -c "
        "'from synapse_channel.cli import main; raise SystemExit(main())' \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return str(wrapper)


def _has_session(tmux_bin: str, session: str) -> bool:
    found = subprocess.run([tmux_bin, "has-session", "-t", f"={session}"], capture_output=True)
    return found.returncode == 0


async def _restore(tmp_path: Path, tmux_bin: str, uri: str, ready: list[Sequence[str]]) -> int:
    def after_ready(pane: Sequence[str]) -> int:
        ready.append(pane)
        return 0

    return await asyncio.to_thread(
        start_restored_seat,
        identity="T/seat",
        command=["sleep", "60"],
        session="synapse-T_seat",
        cwd=tmp_path,
        uri=uri,
        token=None,
        token_file=None,
        tmux_bin=tmux_bin,
        synapse_bin=_synapse_wrapper(tmp_path),
        env={"XDG_RUNTIME_DIR": str(tmp_path)},
        after_ready=after_ready,
    )


async def test_a_free_seat_comes_back_behind_a_real_holder(
    private_tmux: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    ready: list[Sequence[str]] = []
    async with running_hub() as (_hub, uri):
        code = await _restore(tmp_path, private_tmux, uri, ready)
        assert code == 0 and len(ready) == 1
        try:
            assert _has_session(private_tmux, "synapse-T_seat")
            # The holder in the pane keeps the name: a second holder finds it live.
            second = SeatHolder("T/seat", uri=uri, token=None, factory=SynapseAgent)
            assert await second.acquire(timeout=5.0) == "live"
            markers = list(tmp_path.glob("synapse-seat-hold/T_seat.*"))
            assert [m.read_text(encoding="utf-8").split()[0] for m in markers] == ["ready"]
        finally:
            subprocess.run([private_tmux, "kill-session", "-t", "=synapse-T_seat"], check=False)


async def test_a_live_seat_is_left_alone_and_nothing_runs(
    private_tmux: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    ready: list[Sequence[str]] = []
    async with running_hub() as (_hub, uri):
        live = await connect_agent("T/seat-seat", uri)
        try:
            code = await _restore(tmp_path, private_tmux, uri, ready)
        finally:
            await close_agents(live)
    assert code == 75 and ready == []
    assert not _has_session(private_tmux, "synapse-T_seat")
    markers = list(tmp_path.glob("synapse-seat-hold/T_seat.*"))
    assert [m.read_text(encoding="utf-8").split()[0] for m in markers] == ["refused"]


def _waiter(tmp_path: Path, **guard: bool) -> list[str]:
    return worker_session._waiter_command(
        identity="T/seat",
        session="synapse-T_seat",
        cwd=tmp_path,
        command=["synapse", "seat-hold", "--", "codex"],
        synapse_bin="synapse",
        tmux_bin="tmux",
        uri="ws://127.0.0.1:8876",
        token=None,
        token_file=None,
        **guard,
    )


def test_the_restore_waiter_command_asks_for_no_first_takeover(tmp_path: Path) -> None:
    assert _waiter(tmp_path, first_no_takeover=True)[-1] == "--first-no-takeover"
    assert "--first-no-takeover" not in _waiter(tmp_path)
