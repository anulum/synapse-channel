# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the lease-serialising CLI commands (lock/release)

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from synapse_channel import cli, cli_locking


class FakeAgent:
    """Configurable stand-in for SynapseAgent used by the lock/release tests."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool,
        token: str | None = None,
        ready: bool = True,
        inbound: list[dict[str, Any]] | None = None,
        idle: bool = True,
    ) -> None:
        self.name = name
        self.callback = callback
        self.uri = uri
        self.token = token
        self.running = True
        self.claims: list[str] = []
        self.claim_worktrees: list[str] = []
        self.releases: list[str] = []
        self._ready = ready
        self._inbound = inbound or []
        self._idle = idle

    async def connect(self) -> None:
        for message in self._inbound:
            await self.callback(message)
        if self._idle:
            await asyncio.Event().wait()  # block until cancelled

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return self._ready

    async def claim(
        self,
        task_id: str,
        *,
        note: str = "",
        ttl_seconds: float | None = None,
        worktree: str = "",
        paths: Any = (),
        idem_key: str | None = None,
    ) -> None:
        self.claims.append(task_id)
        self.claim_worktrees.append(worktree)

    async def release(self, task_id: str, *, idem_key: str | None = None) -> None:
        self.releases.append(task_id)


def _factory(
    holder: list[FakeAgent],
    *,
    ready: bool = True,
    inbound: list[dict[str, Any]] | None = None,
    idle: bool = True,
) -> Callable[..., Any]:
    def make(
        name: str,
        callback: Any,
        *,
        uri: str,
        verbose: bool,
        token: str | None = None,
    ) -> Any:
        agent = FakeAgent(
            name,
            callback,
            uri=uri,
            verbose=verbose,
            token=token,
            ready=ready,
            inbound=inbound,
            idle=idle,
        )
        holder.append(agent)
        return agent

    return make


# --- lock (serialised commands) ----------------------------------------------


def test_parser_lock() -> None:
    args = cli.build_parser().parse_args(["lock", "q:git", "--name", "X", "--", "git", "push"])
    assert args.task_id == "q:git"
    assert args.command == ["git", "push"]
    assert args.func is cli_locking._cmd_lock


async def test_run_subprocess_returns_exit_code() -> None:
    assert await cli_locking._run_subprocess(["true"]) == 0
    assert await cli_locking._run_subprocess(["false"]) == 1


async def test_lock_runs_command_holding_lease() -> None:
    holder: list[FakeAgent] = []
    granted: dict[str, Any] = {"type": "claim_granted", "task_id": "g", "owner": "X"}
    inbound: list[dict[str, Any]] = [
        {"type": "claim_granted", "task_id": "other", "owner": "X"},  # different task → ignored
        {"type": "chat", "task_id": "g", "payload": "noise"},  # matching id, non-claim → ignored
        granted,
    ]
    factory = _factory(holder, inbound=inbound)
    ran: list[list[str]] = []

    async def runner(command: list[str]) -> int:
        ran.append(command)
        return 0

    code = await cli_locking._lock(
        uri="ws://h",
        name="X",
        task_id="g",
        command=["echo", "hi"],
        paths=["src"],
        wait_timeout=5.0,
        agent_factory=factory,
        runner=runner,
    )
    assert code == 0
    assert ran == [["echo", "hi"]]
    assert holder[0].claims == ["g"]
    # Explicit --paths opts into shared file-scope overlap: the claim stays in the
    # default worktree where declared paths are compared.
    assert holder[0].claim_worktrees == [""]
    assert holder[0].releases == ["g"]


async def test_lock_keyless_namespaces_worktree_to_task_id() -> None:
    holder: list[FakeAgent] = []
    granted: dict[str, Any] = {"type": "claim_granted", "task_id": "repo:git", "owner": "X"}
    factory = _factory(holder, inbound=[granted])

    async def runner(command: list[str]) -> int:
        return 0

    code = await cli_locking._lock(
        uri="ws://h",
        name="X",
        task_id="repo:git",
        command=["git", "push"],
        paths=[],
        wait_timeout=5.0,
        agent_factory=factory,
        runner=runner,
    )
    assert code == 0
    # A keyless lock is a pure named mutex: its claim is scoped to its own task id,
    # so a different repo's lock (a different task id) can never contend with it.
    assert holder[0].claim_worktrees == ["repo:git"]


async def test_lock_fails_fast_when_held(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    denied: dict[str, Any] = {"type": "claim_denied", "task_id": "g", "payload": "held by api-dev"}
    factory = _factory(holder, inbound=[denied], idle=False)

    async def runner(command: list[str]) -> int:
        raise AssertionError("command must not run without the lease")

    code = await cli_locking._lock(
        uri="ws://h",
        name="X",
        task_id="g",
        command=["x"],
        paths=[],
        wait_timeout=0.0,
        agent_factory=factory,
        runner=runner,
    )
    assert code == 1
    assert "Could not acquire lock 'g'" in capsys.readouterr().out


async def test_lock_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli_locking._lock(
        uri="ws://h",
        name="X",
        task_id="g",
        command=["x"],
        paths=[],
        wait_timeout=1.0,
        agent_factory=factory,
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


class _DenyingAgent:
    """A stand-in whose every claim is denied — to exercise the retry/timeout path."""

    def __init__(self, name: str, callback: Any, **_: Any) -> None:
        self.callback = callback
        self.running = True
        self.releases: list[str] = []

    async def connect(self) -> None:
        await asyncio.Event().wait()

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        return True

    async def claim(self, task_id: str, **_: Any) -> None:
        await self.callback({"type": "claim_denied", "task_id": task_id, "payload": "held"})

    async def release(self, task_id: str, **_: Any) -> None:
        self.releases.append(task_id)


async def test_lock_times_out_while_held(capsys: pytest.CaptureFixture[str]) -> None:
    def factory(name: str, callback: Any, **kwargs: Any) -> Any:
        return _DenyingAgent(name, callback)

    code = await cli_locking._lock(
        uri="ws://h",
        name="X",
        task_id="g",
        command=["x"],
        paths=[],
        wait_timeout=0.05,
        retry_interval=0.01,
        agent_factory=factory,
    )
    assert code == 1
    assert "Could not acquire lock 'g'" in capsys.readouterr().out


async def test_lock_gives_up_when_claim_gets_no_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("synapse_channel.cli_locking.asyncio.sleep", no_sleep)
    holder: list[FakeAgent] = []
    factory = _factory(holder, inbound=[], idle=False)  # the claim is never answered
    code = await cli_locking._lock(
        uri="ws://h",
        name="X",
        task_id="g",
        command=["x"],
        paths=[],
        wait_timeout=0.0,
        agent_factory=factory,
    )
    assert code == 1


def test_cmd_lock_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_locking.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(
        uri="ws://h", name="X", task_id="g", command=["x"], paths=None, wait_timeout=0.0, token=None
    )
    assert cli_locking._cmd_lock(ns) == 0


# --- release -----------------------------------------------------------------


def test_parser_release() -> None:
    args = cli.build_parser().parse_args(["release", "studio-panel-enrich", "--name", "USER"])
    assert args.task_id == "studio-panel-enrich"
    assert args.name == "USER"
    assert args.func is cli_locking._cmd_release


async def test_release_granted(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    granted: dict[str, Any] = {
        "type": "release_granted",
        "task_id": "studio-panel-enrich",
        "owner": "USER",
    }
    factory = _factory(holder, inbound=[granted])
    code = await cli_locking._release(
        uri="ws://h",
        name="USER",
        task_id="studio-panel-enrich",
        agent_factory=factory,
    )
    assert code == 0
    assert holder[0].releases == ["studio-panel-enrich"]
    assert "released 'studio-panel-enrich'" in capsys.readouterr().out


async def test_release_denied_for_non_owner(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    denied: dict[str, Any] = {
        "type": "release_denied",
        "task_id": "studio-panel-enrich",
        "payload": "owned by SCPN-MIF-CORE, not USER",
    }
    factory = _factory(holder, inbound=[denied])
    code = await cli_locking._release(
        uri="ws://h",
        name="USER",
        task_id="studio-panel-enrich",
        agent_factory=factory,
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "release refused for 'studio-panel-enrich'" in out
    assert "owned by SCPN-MIF-CORE" in out


async def test_release_ignores_noise_then_confirms(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    inbound: list[dict[str, Any]] = [
        # A grant for another task is ignored (wrong task id) ...
        {"type": "release_granted", "task_id": "other", "owner": "USER"},
        # ... and a grant addressed to a different owner is ignored too ...
        {"type": "release_granted", "task_id": "t", "owner": "ELSE"},
        # ... before the grant that actually belongs to this caller.
        {"type": "release_granted", "task_id": "t", "owner": "USER"},
    ]
    factory = _factory(holder, inbound=inbound)
    code = await cli_locking._release(uri="ws://h", name="USER", task_id="t", agent_factory=factory)
    assert code == 0
    assert "released 't'" in capsys.readouterr().out


async def test_release_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, ready=False)
    code = await cli_locking._release(uri="ws://h", name="USER", task_id="t", agent_factory=factory)
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_release_gives_up_without_response(capsys: pytest.CaptureFixture[str]) -> None:
    holder: list[FakeAgent] = []
    factory = _factory(holder, inbound=[])
    code = await cli_locking._release(uri="ws://h", name="USER", task_id="t", agent_factory=factory)
    assert code == 1
    assert "no response from hub" in capsys.readouterr().out


def test_cmd_release_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.cli_locking.asyncio.run", lambda coro: coro.close() or 0)
    ns = argparse.Namespace(uri="ws://h", name="USER", task_id="t", token=None)
    assert cli_locking._cmd_release(ns) == 0
