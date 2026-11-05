# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — published hub state for a token set
"""Exercise the hub state files that tell an operator whether a set is in force."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from synapse_channel import cli_processes
from synapse_channel.cli import main
from synapse_channel.core.token_set import (
    TokenSetAuthenticator,
    connect_token_digest,
    state_directory,
)
from synapse_channel.token_set_hub_status import (
    HubStateWriter,
    publish_hub_state,
    read_hub_states,
)
from test_hub_identity_paths_runtime import owned_cli_hub

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
_SRC = Path(__file__).resolve().parents[1] / "src"


def _write(path: Path, raw: str | None = None) -> Path:
    document = {"version": 1, "tokens": [{"id": "a", "sha256": connect_token_digest("s-a")}]}
    staging = path.with_name(path.name + ".new")
    staging.write_text(raw if raw is not None else json.dumps(document))
    os.chmod(staging, 0o600)
    os.replace(staging, path)
    return path


def _document(writer: HubStateWriter) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(writer.path.read_text())
    return loaded


def test_a_hub_publishes_each_change_by_code_only(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")
    authenticator = TokenSetAuthenticator(path)
    writer = HubStateWriter(authenticator, clock=lambda: 1_800_000_000.0)
    authenticator.observe(writer)
    try:
        first = _document(writer)
        assert set(first) == {
            "version",
            "pid",
            "started",
            "state",
            "since",
            "reason_code",
            "signature",
        }
        assert (first["state"], first["reason_code"]) == ("applied", None)
        assert first["started"] == "2027-01-15T08:00:00Z" and first["pid"] == os.getpid()
        _write(path, raw='{"version": 1, "tokens": [{"id": "SECRET-LOOKING"}]}')
        authenticator.refresh()
        stale = _document(writer)
        assert (stale["state"], stale["reason_code"]) == ("stale", "invalid")
        assert "SECRET-LOOKING" not in writer.path.read_text()
        assert stale["signature"] != first["signature"]
        path.unlink()
        authenticator.refresh()
        assert _document(writer)["reason_code"] == "missing"
        _write(path)
        authenticator.refresh()
        assert _document(writer)["state"] == "applied"
        assert writer.path.parent == state_directory(path)
        assert [state.state for state in read_hub_states(path)] == ["applied"]
    finally:
        writer.close()
    assert read_hub_states(path) == []


@posix_only
def test_state_files_are_owner_only_and_an_unsafe_set_is_named(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")
    authenticator = TokenSetAuthenticator(path)
    writer = publish_hub_state(authenticator)
    assert writer is not None
    try:
        assert stat.S_IMODE(writer.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(writer.path.parent.stat().st_mode) == 0o700
        path.chmod(0o644)
        authenticator.refresh()
        (state,) = read_hub_states(path)
        assert (state.running, state.state, state.reason_code) == (True, "stale", "unsafe")
    finally:
        writer.close()


def test_a_hub_that_is_gone_is_unknown_never_applied(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")
    script = (
        "import os, sys\n"
        "from synapse_channel.core.token_set import TokenSetAuthenticator\n"
        "from synapse_channel.token_set_hub_status import publish_hub_state\n"
        "publish_hub_state(TokenSetAuthenticator(sys.argv[1]))\n"
        "os._exit(0)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(_SRC)}
    subprocess.run([sys.executable, "-c", script, str(path)], check=True, env=env, timeout=60)
    (state,) = read_hub_states(path)
    assert (state.running, state.state) == (False, "unknown")
    assert state.pid is not None and state.pid != os.getpid()
    assert main(["token-set", "status", str(path)]) == 1


def test_one_writer_per_hub_and_start(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")
    authenticator = TokenSetAuthenticator(path)
    first = HubStateWriter(authenticator, pid=7, clock=lambda: 5.0)
    try:
        with pytest.raises(RuntimeError, match="another writer holds"):
            HubStateWriter(authenticator, pid=7, clock=lambda: 5.0)
    finally:
        first.close()


@posix_only
def test_a_hub_that_cannot_publish_still_serves_and_says_why(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    folder = tmp_path / "readonly"
    folder.mkdir()
    path = _write(folder / "tokens.json")
    folder.chmod(0o500)
    try:
        assert publish_hub_state(TokenSetAuthenticator(path)) is None
    finally:
        folder.chmod(0o700)
    assert "token-set hub state not published" in caplog.text


@posix_only
def test_failed_state_writes_are_logged_and_leave_nothing_staged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write(tmp_path / "tokens.json")
    authenticator = TokenSetAuthenticator(path)
    writer = HubStateWriter(authenticator)
    try:
        writer.path.mkdir()
        writer.set_applied(path, ("a",))
        assert writer.path.is_dir()
        staged = [item.name for item in writer.path.parent.iterdir() if item.suffix == ".tmp"]
        assert staged == []
        writer.path.rmdir()
        writer.path.parent.chmod(0o500)
        try:
            writer.set_applied(path, ("a",))
        finally:
            writer.path.parent.chmod(0o700)
        assert caplog.text.count("cannot publish the token-set hub state") == 2
    finally:
        writer.close()


def test_status_reports_unreadable_files_and_exits_on_health(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path / "tokens.json")
    assert main(["token-set", "status", str(path)]) == 1
    assert "no running hub reports this set" in capsys.readouterr().out
    authenticator = TokenSetAuthenticator(path)
    writer = publish_hub_state(authenticator)
    assert writer is not None
    assert main(["token-set", "status", str(path)]) == 0
    assert ": applied since " in capsys.readouterr().out
    broken = state_directory(path) / "hub-1-1.json"
    broken.write_text("{not json")
    broken.chmod(0o600)
    assert main(["token-set", "status", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["healthy"] is True
    assert {row["state"] for row in report["hubs"]} == {"applied", "unreadable"}
    _write(path, raw="{broken")
    authenticator.refresh()
    assert main(["token-set", "status", str(path)]) == 1
    assert "stale (invalid) since" in capsys.readouterr().out
    assert main(["token-set", "status", str(path), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["healthy"] is False
    writer.close()


@posix_only
def test_status_refuses_a_linked_lock_and_skips_a_wrong_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path / "tokens.json")
    directory = state_directory(path)
    directory.mkdir(mode=0o700)
    wrong = directory / "hub-2-2.json"
    wrong.write_text(json.dumps({"version": 9, "state": "applied"}))
    wrong.chmod(0o600)
    (state,) = read_hub_states(path)
    assert state.state == "unreadable"
    (directory / "hub-3-3.json").write_text("{}")
    (directory / "hub-3-3.lock").symlink_to(tmp_path / "elsewhere")
    assert main(["token-set", "status", str(path)]) == 1
    assert "synapse token-set status:" in capsys.readouterr().err


def test_a_change_the_hub_has_not_seen_is_pending(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")
    authenticator = TokenSetAuthenticator(path)
    writer = publish_hub_state(authenticator)
    assert writer is not None
    try:
        assert [state.state for state in read_hub_states(path)] == ["applied"]
        _write(path, raw="{broken")
        assert [state.state for state in read_hub_states(path)] == ["pending"]
        assert main(["token-set", "status", str(path)]) == 1
        authenticator.refresh()
        assert [state.state for state in read_hub_states(path)] == ["stale"]
    finally:
        writer.close()


@posix_only
def test_a_state_without_its_lock_file_is_unknown_and_no_lock_is_made(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")
    writer = HubStateWriter(TokenSetAuthenticator(path))
    writer.set_applied(path, ("a",))
    orphan = writer.path.with_name("hub-9-9.json")
    orphan.write_bytes(writer.path.read_bytes())
    orphan.chmod(0o600)
    writer.close()
    (state,) = read_hub_states(path)
    assert (state.name, state.state) == ("hub-9-9.json", "unknown")
    assert not orphan.with_suffix(".lock").exists()


@posix_only
async def test_a_real_hub_applies_or_rejects_an_edit_without_a_connection_and_cleans_up(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path / "tokens.json")
    with owned_cli_hub(tmp_path / "home", None, "token-set", extra_args=("--token-set", str(path))):
        await _until_state(path, "applied")
        _write(path, raw="{broken")
        await _until_state(path, "stale")
        (state,) = read_hub_states(path)
        assert state.reason_code == "invalid"
        _write(path)
        await _until_state(path, "applied")
    assert sorted(item.name for item in state_directory(path).glob("hub-*")) == []


async def _until_state(path: Path, wanted: str) -> None:
    deadline = time.monotonic() + 10
    while [state.state for state in read_hub_states(path)] != [wanted]:
        assert time.monotonic() < deadline, read_hub_states(path)
        await asyncio.sleep(0.1)


def test_the_hub_command_publishes_while_serving_and_removes_at_stop(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")
    seen: list[list[str]] = []

    def runner(coroutine: Coroutine[Any, Any, None]) -> None:
        seen.append([state.state for state in read_hub_states(path)])
        coroutine.close()

    assert cli_processes._cmd_hub(_hub_ns(token_set=str(path)), runner=runner) == 0
    assert seen == [["applied"]]
    assert list(state_directory(path).glob("hub-*")) == []


@posix_only
def test_the_hub_command_serves_when_it_cannot_publish(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    folder = tmp_path / "readonly"
    folder.mkdir()
    path = _write(folder / "tokens.json")
    folder.chmod(0o500)
    served: list[bool] = []

    def runner(coroutine: Coroutine[Any, Any, None]) -> None:
        served.append(True)
        coroutine.close()

    try:
        assert cli_processes._cmd_hub(_hub_ns(token_set=str(path)), runner=runner) == 0
    finally:
        folder.chmod(0o700)
    assert served == [True]
    assert "token-set hub state not published" in caplog.text
