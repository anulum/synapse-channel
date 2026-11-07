# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a pending pane wake keeps one long, parent-bound wait
"""A bridge whose wake cannot be injected yet holds one wait and retries every interval.

Real hub, real tmux pane, real bridge. The pane shows an approval chooser until
the test creates a ready file; then it renders an idle composer that records
every submitted line.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from _platform_caps import requires_proc
from cli_e2e_helpers import _candidate_environment, _release, _stop, isolated_hub, run_cli
from synapse_channel.agent_tmux import build_wake_prompt

_TMUX = shutil.which("tmux")
pytestmark = [pytest.mark.skipif(_TMUX is None, reason="tmux is not installed"), requires_proc]

_IDENTITY = "E2EPENDING"
_INTERVAL = 0.3


def _gated_provider(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A pane that shows a chooser until ``ready`` exists, then an idle composer."""
    provider = tmp_path / "codex-fixture-gated"
    ready = tmp_path / "codex-fixture-gated.ready"
    submissions = tmp_path / "codex-fixture-gated.submissions"
    provider.write_text(
        "#!/bin/sh\n"
        "printf '\\n\\n\\n\\nAllow Codex to run this command?\\n› 1. Yes\\n  2. No\\n'\n"
        f"while [ ! -e '{ready}' ]; do sleep 0.05; done\n"
        "printf '\\033[2J\\033[H\\033[999B› \\n'\n"
        "while IFS= read -r line; do\n"
        f"  printf '%s\\n' \"$line\" >> '{submissions}'\n"
        "  printf '%s\\n› \\n' \"$line\"\n"
        "done\n",
        encoding="utf-8",
    )
    provider.chmod(0o700)
    return provider, ready, submissions


def _refusing_tmux(tmp_path: Path) -> Path:
    """A tmux that refuses ``set-buffer`` with status 7 and runs every other command."""
    assert _TMUX is not None
    wrapper = tmp_path / "tmux-refusing-set-buffer"
    wrapper.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "set-buffer" ]; then echo "set-buffer refused" >&2; exit 7; fi\n'
        f'exec {_TMUX} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    return wrapper


@contextmanager
def _session() -> Iterator[str]:
    """Yield a unique tmux session name and remove the session afterwards."""
    assert _TMUX is not None
    name = f"synapse-e2e-pending-{uuid.uuid4().hex[:12]}"
    try:
        yield name
    finally:
        subprocess.run([_TMUX, "kill-session", "-t", name], capture_output=True, check=False)


def _capture(session: str) -> str:
    assert _TMUX is not None
    return subprocess.run(
        [_TMUX, "capture-pane", "-t", session, "-p", "-J"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def _wait_children(pid: int) -> set[int]:
    """Return the live children of ``pid`` that run ``synapse wait``, bound or not."""
    children: set[int] = set()
    for task in Path(f"/proc/{pid}/task").iterdir():
        for child in (task / "children").read_text().split():
            try:
                argv = Path(f"/proc/{child}/cmdline").read_bytes().split(b"\0")
            except FileNotFoundError:
                continue
            if b"wait" in argv and b"--name" in argv:
                children.add(int(child))
    return children


def _bound_to(child: int, parent: int) -> bool:
    argv = Path(f"/proc/{child}/cmdline").read_bytes().split(b"\0")
    return (
        b"--exit-with-parent" in argv
        and argv[argv.index(b"--exit-with-parent") + 1] == str(parent).encode()
    )


def _pending_bridge(
    tmp_path: Path, session: str, uri: str, provider: Path, *, tmux: str
) -> tuple[subprocess.Popen[str], Path]:
    """Start the pane with a queued wake, start its bridge, and wait for registration."""
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    env = {"XDG_RUNTIME_DIR": str(runtime)}
    common = [
        "--identity",
        _IDENTITY,
        "--session",
        session,
        "--cwd",
        str(tmp_path),
        "--agent-command",
        str(provider),
        "--submit-delay",
        "0",
        "--tmux-bin",
        tmux,
    ]
    started = run_cli("agent-tmux", "start", *common, env=env)
    assert started.ok(), started.output
    deadline = time.monotonic() + 5
    while "Allow Codex" not in _capture(session):
        assert time.monotonic() < deadline, "the chooser did not render"
        time.sleep(0.05)
    registry = runtime / "synapse-agent-tmux" / f"{_IDENTITY}.json"
    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["pending_wake"] = True
    registry.write_text(json.dumps(payload), encoding="utf-8")
    bridge = subprocess.Popen(  # noqa: S603 - candidate CLI, test-only
        [
            sys.executable,
            "-u",
            "-m",
            "synapse_channel.cli",
            "agent-tmux",
            "wait",
            *common,
            "--uri",
            uri,
            "--pane-probe-interval",
            str(_INTERVAL),
            "--synapse-bin",
            str(Path(sys.executable).parent / "synapse"),
            "--max-wakes",
            "1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_candidate_environment(env),
    )
    deadline = time.monotonic() + 15
    while f"{_IDENTITY}-pane-rx" not in run_cli("who", uri=uri).stdout:
        if time.monotonic() > deadline or bridge.poll() is not None:
            _stop(bridge)
            raise AssertionError(f"pane bridge did not register: {bridge.communicate()[0]}")
        time.sleep(0.05)
    return bridge, registry


def _children_over(bridge: subprocess.Popen[str], seconds: float) -> set[int]:
    seen: set[int] = set()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        seen |= _wait_children(bridge.pid)
        time.sleep(0.02)
    return seen


def test_a_pending_wake_holds_one_wait_and_is_delivered_once_the_pane_is_safe(
    tmp_path: Path,
) -> None:
    """Many probe intervals, one bound wait; the wake lands once the pane is safe."""
    provider, ready, submissions = _gated_provider(tmp_path)
    assert _TMUX is not None
    with _session() as session, isolated_hub(tmp_path) as hub:
        bridge, registry = _pending_bridge(tmp_path, session, hub.uri, provider, tmux=_TMUX)
        try:
            seen = _children_over(bridge, 10 * _INTERVAL)
            assert len(seen) == 1, seen
            assert _bound_to(next(iter(seen)), bridge.pid)
            assert not submissions.exists()
            assert json.loads(registry.read_text(encoding="utf-8"))["pending_wake"] is True

            ready.touch()
            output, _ = bridge.communicate(timeout=15)
            assert bridge.returncode == 0, output
            assert submissions.read_text(encoding="utf-8").splitlines() == [
                build_wake_prompt(_IDENTITY)
            ]
            assert json.loads(registry.read_text(encoding="utf-8"))["pending_wake"] is False
        finally:
            _release(bridge)


def test_a_new_wake_while_pending_restarts_the_wait_without_input(tmp_path: Path) -> None:
    """A message ends the pending wait; the bridge retries, stays queued, and waits again."""
    provider, _ready, submissions = _gated_provider(tmp_path)
    assert _TMUX is not None
    with _session() as session, isolated_hub(tmp_path) as hub:
        bridge, registry = _pending_bridge(tmp_path, session, hub.uri, provider, tmux=_TMUX)
        try:
            (first,) = _children_over(bridge, 3 * _INTERVAL)
            sent = run_cli(
                "send", "another wake", "--name", "A", "--target", _IDENTITY, uri=hub.uri
            )
            assert sent.ok(), sent.output
            deadline = time.monotonic() + 10
            while True:
                current = _wait_children(bridge.pid) - {first}
                if current:
                    break
                assert bridge.poll() is None, bridge.communicate()[0]
                assert time.monotonic() < deadline, "no new wait after the wake"
                time.sleep(0.02)
            (second,) = current
            assert _bound_to(second, bridge.pid)
            assert not submissions.exists()
            assert json.loads(registry.read_text(encoding="utf-8"))["pending_wake"] is True
        finally:
            _release(bridge)


def test_an_injection_failure_while_pending_stops_the_wait_and_ends_the_bridge(
    tmp_path: Path,
) -> None:
    """A refused tmux send during the long wait reaps the wait and becomes the exit code."""
    provider, ready, submissions = _gated_provider(tmp_path)
    tmux = _refusing_tmux(tmp_path)
    with _session() as session, isolated_hub(tmp_path) as hub:
        bridge, registry = _pending_bridge(tmp_path, session, hub.uri, provider, tmux=str(tmux))
        try:
            (child,) = _children_over(bridge, 3 * _INTERVAL)
            ready.touch()
            output, _ = bridge.communicate(timeout=15)
            assert bridge.returncode == 7, output
            assert not Path(f"/proc/{child}").exists() or b"Z" in _state(child)
            assert not submissions.exists()
            assert json.loads(registry.read_text(encoding="utf-8"))["pending_wake"] is True
            deadline = time.monotonic() + 5
            while f"{_IDENTITY}-pane-rx" in run_cli("who", uri=hub.uri).stdout:
                assert time.monotonic() < deadline, "the pane receiver outlived the bridge"
                time.sleep(0.05)
        finally:
            _release(bridge)


def _state(pid: int) -> bytes:
    """Return the scheduler state letter of ``pid``, or ``Z`` once it is gone."""
    try:
        return Path(f"/proc/{pid}/stat").read_bytes().rsplit(b")", 1)[1].split()[0]
    except (FileNotFoundError, IndexError):
        return b"Z"
