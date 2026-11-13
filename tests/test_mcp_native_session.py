# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native MCP identity and real process lifecycle tests
"""Exercise native identities, real local exclusion and stdio inbox continuity."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

import synapse_channel.cli_mcp as cli_mcp
from cli_e2e_helpers import isolated_hub
from cli_mcp_helpers import mcp_namespace
from synapse_channel.mcp.native_session import (
    _ABANDONED_LOCK_AGE_SECONDS,
    _ABANDONED_LOCK_SWEEP_LIMIT,
    NativeSessionError,
    native_stdio_session,
)
from synapse_channel.relay import append_jsonl, encode_lite

SESSION = "01a14c68-36c8-7100-9755-b2f7e673ad78"
CLOSED_HUB = "ws://127.0.0.1:1"
"""An address nothing listens on, so a command that is not refused reaches no hub."""


def _child_environment(**changes: str) -> dict[str, str]:
    """Return this process's environment without the settings a seat's shell may carry."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in ("SYN_INBOX_SOURCE", "SYNAPSE_URI", "SYN_IDENTITY", "SYN_PROJECT")
    }
    environment.update(changes)
    return environment


@pytest.fixture
def native_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Select only disposable native session storage and a canonical UUID."""
    home = tmp_path / "syn-home"
    monkeypatch.setenv("SYN_HOME", str(home))
    monkeypatch.setenv("GROK_SESSION_ID", SESSION)
    return home


def test_plain_stdio_ignores_native_state(native_home: Path) -> None:
    """Existing explicit stdio names remain unchanged without an opt-in."""
    with native_stdio_session("PROJ/fixed", ("OTHER/fixed",), None) as value:
        assert value == ("PROJ/fixed", ("OTHER/fixed",))
    assert not native_home.exists()


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "secret-not-a-uuid",
        "0" * 32,
        "00000000-0000-0000-0000-000000000000",
        SESSION.upper(),
        " " + SESSION,
        "{" + SESSION + "}",
    ],
)
def test_invalid_native_identifier_is_never_echoed(
    native_home: Path, monkeypatch: pytest.MonkeyPatch, identifier: str
) -> None:
    """Missing and noncanonical native input fails before creating storage."""
    monkeypatch.setenv("GROK_SESSION_ID", identifier)
    with pytest.raises(ValueError, match="canonical GROK_SESSION_ID") as failure:
        native_stdio_session("PROJ/grok", (), "grok").__enter__()
    assert str(failure.value) == "synapse mcp: Grok must supply a canonical GROK_SESSION_ID"
    assert not native_home.exists()


@pytest.mark.parametrize(
    "prefix", ["plain", "P/with/slash", "P/../bad", "P/secret\n", "P/" + "a" * 81]
)
def test_native_prefix_is_bounded(native_home: Path, prefix: str) -> None:
    """Unscoped, injected and oversized routing prefixes are rejected."""
    with pytest.raises(ValueError, match="project/client prefix"):
        native_stdio_session(prefix, (), "grok").__enter__()
    assert not native_home.exists()


def test_unsupported_client_is_refused(native_home: Path) -> None:
    """A caller cannot select an unqualified vendor environment producer."""
    with pytest.raises(ValueError, match="unsupported native client"):
        native_stdio_session("PROJ/client", (), "unknown").__enter__()
    assert not native_home.exists()


@pytest.mark.parametrize("home", ["", "relative-session-storage"])
def test_native_storage_cannot_depend_on_working_directory(
    native_home: Path, monkeypatch: pytest.MonkeyPatch, home: str
) -> None:
    """Changing projects must not silently change the duplicate-exclusion scope."""
    monkeypatch.setenv("SYN_HOME", home)
    with pytest.raises(ValueError, match="absolute SYN_HOME"):
        native_stdio_session("PROJ/grok", (), "grok").__enter__()
    assert not native_home.exists()


def test_real_cli_duplicate_is_refused_before_connection(native_home: Path) -> None:
    """The actual CLI exits with an error while the original identity stays held."""
    with native_stdio_session("PROJ/grok", (), "grok"):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "synapse_channel.cli",
                "mcp",
                "--uri",
                CLOSED_HUB,
                "--name",
                "PROJ/grok",
                "--native-client",
                "grok",
            ],
            env=_child_environment(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 1
        assert "already active" in result.stderr
        assert result.stdout == ""


def test_native_names_alias_exclusion_and_resume(native_home: Path) -> None:
    """A shared alias cannot be held by two bridges; resume retains names."""
    roles = ("OTHER/grok", "OTHER/grok")
    with native_stdio_session("PROJ/grok", roles, "grok") as first:
        assert first == (f"PROJ/grok-{SESSION}", (f"OTHER/grok-{SESSION}",) * 2)
        with pytest.raises(RuntimeError, match="already active"):
            native_stdio_session("ANOTHER/grok", roles, "grok").__enter__()
    with native_stdio_session("PROJ/grok", roles, "grok") as resumed:
        assert resumed == first
    assert list((native_home / "mcp-native-sessions").glob("*.lock")) == []


def test_a_clean_exit_removes_the_sessions_lock_files(native_home: Path) -> None:
    """A finished native session leaves none of its lock files behind."""
    with native_stdio_session("PROJ/grok", ("OTHER/grok",), "grok"):
        present = sorted(path.name for path in (native_home / "mcp-native-sessions").glob("*.lock"))
        assert present == sorted(
            hashlib.sha256(identity.encode()).hexdigest() + ".lock"
            for identity in (f"OTHER/grok-{SESSION}", f"PROJ/grok-{SESSION}")
        )
    assert list((native_home / "mcp-native-sessions").glob("*.lock")) == []


def test_one_holder_remains_when_a_session_exits_during_acquisition(native_home: Path) -> None:
    """A clean exit while another process acquires the same name leaves one holder."""
    holder_code = (
        "import sys\n"
        "from synapse_channel.mcp.native_session import native_stdio_session\n"
        "with native_stdio_session('PROJ/grok', (), 'grok'):\n"
        "    print('held', flush=True)\n"
        "    sys.stdin.readline()\n"
    )
    challenger_code = (
        "import time\n"
        "from synapse_channel.mcp.native_session import native_stdio_session\n"
        "deadline = time.monotonic() + 10\n"
        "while time.monotonic() < deadline:\n"
        "    try:\n"
        "        with native_stdio_session('PROJ/grok', (), 'grok'):\n"
        "            print('held', flush=True)\n"
        "            time.sleep(30)\n"
        "    except RuntimeError:\n"
        "        continue\n"
        "print('lost', flush=True)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        env=_child_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    processes = [holder]
    try:
        assert holder.stdout is not None and holder.stdin is not None
        assert holder.stdout.readline() == "held\n"
        challenger = subprocess.Popen(
            [sys.executable, "-c", challenger_code],
            env=_child_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(challenger)
        assert challenger.stdout is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert holder.wait(timeout=5) == 0
        assert challenger.stdout.readline() == "held\n"
        with pytest.raises(RuntimeError, match="already active"):
            native_stdio_session("PROJ/grok", (), "grok").__enter__()
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)


def test_a_held_lock_survives_another_sessions_exit_and_sweep(native_home: Path) -> None:
    """Another process's lock is not removed by an exit or by the abandoned sweep.

    After that process dies without unlinking, the file is a crash leftover
    older than 24 hours and the next session removes it.
    """
    directory = native_home / "mcp-native-sessions"
    directory.mkdir(parents=True, mode=0o700)
    held = directory / ("ab" * 32 + ".lock")
    held.write_bytes(b"")
    os.chmod(held, 0o600)
    old = time.time() - _ABANDONED_LOCK_AGE_SECONDS - 5
    os.utime(held, (old, old))
    code = (
        "from pathlib import Path\n"
        "import os, time\n"
        "from synapse_channel.advisory_lock import locked\n"
        "path = Path(os.environ['HELD_LOCK'])\n"
        "with locked(path, blocking=False, "
        "purpose='native MCP session lock directory') as acquired:\n"
        "    assert acquired\n"
        "    print('held', flush=True)\n"
        "    time.sleep(30)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        env=_child_environment(HELD_LOCK=str(held)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline() == "held\n"
        with native_stdio_session("PROJ/grok", (), "grok"):
            assert held.is_file()
        assert held.is_file()
        assert list(directory.glob("*.lock")) == [held]
    finally:
        process.terminate()
        stderr = process.communicate(timeout=5)[1]
    assert process.returncode is not None
    assert held.is_file(), stderr
    with native_stdio_session("PROJ/grok", (), "grok"):
        assert not held.exists()
    assert list(directory.glob("*.lock")) == []


def test_a_recent_crash_file_is_left_until_it_is_old(native_home: Path) -> None:
    """A crash file younger than 24 hours stays, as does anything that is not a lock."""
    directory = native_home / "mcp-native-sessions"
    directory.mkdir(parents=True, mode=0o700)
    old = time.time() - _ABANDONED_LOCK_AGE_SECONDS - 5
    recent = directory / ("cd" * 32 + ".lock")
    recent.write_bytes(b"")
    note = directory / "notes.txt"
    note.write_bytes(b"keep")
    os.utime(note, (old, old))
    broken = directory / ("ef" * 32 + ".lock")
    broken.symlink_to(directory / "missing")
    target = directory / "target-file"
    target.write_bytes(b"")
    os.utime(target, (old, old))
    linked = directory / ("12" * 32 + ".lock")
    linked.symlink_to("target-file")
    with native_stdio_session("PROJ/grok", (), "grok"):
        pass
    session_lock = hashlib.sha256(f"PROJ/grok-{SESSION}".encode()).hexdigest() + ".lock"
    names = {path.name for path in directory.iterdir()}
    assert recent.name in names
    assert note.name in names
    assert broken.name in names
    assert linked.name in names
    assert target.name in names
    assert session_lock not in names


def test_the_abandoned_sweep_examines_a_bounded_number_of_names(native_home: Path) -> None:
    """One session start removes at most the documented number of old lock files."""
    directory = native_home / "mcp-native-sessions"
    directory.mkdir(parents=True, mode=0o700)
    old = time.time() - _ABANDONED_LOCK_AGE_SECONDS - 5
    for index in range(_ABANDONED_LOCK_SWEEP_LIMIT + 1):
        path = directory / f"{index:064x}.lock"
        path.write_bytes(b"")
        os.utime(path, (old, old))
    kept = directory / f"{_ABANDONED_LOCK_SWEEP_LIMIT:064x}.lock"
    with native_stdio_session("PROJ/grok", (), "grok"):
        pass
    assert list(directory.glob("*.lock")) == [kept]


def test_unavailable_lock_refuses_without_repairing_files(native_home: Path) -> None:
    """A nonfile at the exact lock path cannot be replaced or followed."""
    directory = native_home / "mcp-native-sessions"
    directory.mkdir(parents=True, mode=0o700)
    digest = hashlib.sha256(f"PROJ/grok-{SESSION}".encode()).hexdigest()
    target = directory / (digest + ".lock")
    target.mkdir()
    with pytest.raises(RuntimeError, match="lock is unavailable"):
        native_stdio_session("PROJ/grok", (), "grok").__enter__()
    assert target.is_dir()


def test_child_exit_releases_native_identity(native_home: Path) -> None:
    """An actual terminated child cannot strand a PID-based stale lock."""
    code = (
        "from synapse_channel.mcp.native_session import native_stdio_session;"
        "import time\n"
        "with native_stdio_session('PROJ/grok', (), 'grok'):\n"
        " print('held',flush=True)\n"
        " time.sleep(20)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline() == "held\n"
        with pytest.raises(RuntimeError, match="already active"):
            native_stdio_session("PROJ/grok", (), "grok").__enter__()
    finally:
        process.terminate()
        process.communicate(timeout=5)
    with native_stdio_session("PROJ/grok", (), "grok") as (name, _):
        assert name == f"PROJ/grok-{SESSION}"


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        (["--native-client", "grok"], "explicit --name prefix"),
        (["--native-client", "grok", "--transport", "streamable-http"], "requires stdio"),
        (["--native-client", "grok", "--name", "P/grok"], "canonical GROK_SESSION_ID"),
    ],
)
def test_cli_native_admission_without_network(
    native_home: Path, arguments: list[str], fragment: str
) -> None:
    """Real CLI admission refuses invalid native configuration before serving."""
    environment = _child_environment(GROK_SESSION_ID="credential-shaped-invalid-input")
    result = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", "mcp", "--uri", CLOSED_HUB, *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert fragment in result.stderr
    assert "credential-shaped-invalid-input" not in result.stderr
    assert not native_home.exists()


async def test_real_stdio_native_inbox_survives_restart(native_home: Path, tmp_path: Path) -> None:
    """Real MCP processes retain exact per-session cursors across restarts."""
    first_name = f"PROJ/grok-{SESSION}"
    second_session = "01a14c68-3fa7-7aa1-89c1-600908698b00"
    feed = tmp_path / "feed.ndjson"
    for index, name in enumerate((first_name, f"PROJ/grok-{second_session}"), 1):
        append_jsonl(
            feed,
            encode_lite(
                {
                    "type": "chat",
                    "sender": "PEER",
                    "target": name,
                    "payload": f"private message {index}",
                    "timestamp": 1.0,
                    "msg_id": index,
                }
            ),
        )
    with isolated_hub(tmp_path) as hub:
        for identifier, expected in (
            (SESSION, ["private message 1"]),
            (second_session, ["private message 2"]),
            (SESSION, []),
        ):
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m",
                    "synapse_channel.cli",
                    "mcp",
                    "--uri",
                    hub.uri,
                    "--name",
                    "PROJ/grok",
                    "--native-client",
                    "grok",
                    "--inbox-feed",
                    str(feed),
                ],
                env=_child_environment(GROK_SESSION_ID=identifier),
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    status = await session.call_tool("synapse_status", {})
                    inbox = await session.call_tool("synapse_inbox", {"limit": 10})
                    assert isinstance(status.content[0], TextContent)
                    assert isinstance(inbox.content[0], TextContent)
                    assert (
                        json.loads(status.content[0].text)["identity"] == f"PROJ/grok-{identifier}"
                    )
                    assert [
                        row["payload"] for row in json.loads(inbox.content[0].text)["messages"]
                    ] == expected


def test_an_interrupt_names_the_session_the_bridge_ran_under(
    native_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The stop message carries the derived name, not the prefix it was derived from."""

    async def interrupted(**_: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_mcp, "serve_stdio", interrupted)
    arguments = mcp_namespace(name="PROJ/grok", uri=CLOSED_HUB, native_client="grok")
    assert cli_mcp._cmd_mcp(arguments) == 0
    assert f"[PROJ/grok-{SESSION}] MCP server stopped." in capsys.readouterr().out


def test_a_refusal_of_the_native_session_is_its_own_error(native_home: Path) -> None:
    """Only the native checks end the command with their text; they raise their own class."""
    with pytest.raises(NativeSessionError, match="unsupported native client"):
        native_stdio_session("PROJ/client", (), "unknown").__enter__()
    assert not native_home.exists()


def test_another_value_error_of_the_serving_lifetime_is_not_swallowed(
    native_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A value error that is not a native refusal is not turned into a refusal message."""

    async def broken(**_: object) -> int:
        raise ValueError("a defect elsewhere")

    monkeypatch.setattr(cli_mcp, "serve_stdio", broken)
    arguments = mcp_namespace(name="PROJ/grok", uri=CLOSED_HUB, native_client="grok")
    with pytest.raises(ValueError, match="a defect elsewhere"):
        cli_mcp._cmd_mcp(arguments)
