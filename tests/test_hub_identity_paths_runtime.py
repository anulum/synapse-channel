# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — isolated CLI hub identity-pin persistence
"""Prove independent CLI hubs retain their own durable identity pins."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from cli_e2e_helpers import free_port, run_cli
from home_redirect import redirect_home

# A hang guard, not a measurement. A cold CLI start on a loaded runner
# has stayed silent past ninety seconds while the interpreter was still
# importing. The loop still fails at once when the hub process exits, so
# a longer wait only covers a slow start; it cannot hide a hub that
# crashed. Readiness is the serving line from this process. A TCP accept
# is not enough: under load another listener can own a port that was free
# a moment earlier, and the roster query then spends its budget there.
HUB_START_SECONDS = 180.0
# A hang guard, not a measurement. ``who`` waits five seconds for the welcome
# by default. Under load the hub has already accepted TCP and the welcome
# arrives later. A hub that refuses or never welcomes still fails; this wait
# is not a retry.
WHO_READY_HANG_GUARD_SECONDS = 30.0
# The process must outlast a slow interpreter start plus the welcome wait.
# A shorter kill hides the reach failure behind a subprocess timeout.
WHO_PROCESS_HANG_GUARD_SECONDS = 120.0
# Past the five-second default, inside the hang guard. A revert to that
# default makes the late-welcome registration fail.
_LATE_WELCOME_SECONDS = 6.0


_SERVING_MARKER = "Synapse Hub running on "


def _uri_published_by(log_path: Path) -> str | None:
    """Read the port this process logged after it bound, as a loopback URI."""
    if not log_path.exists():
        return None
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if _SERVING_MARKER not in line:
            continue
        published = line.split(_SERVING_MARKER, 1)[1].strip().split()[0]
        port = int(published.rsplit(":", 1)[1])
        return f"ws://127.0.0.1:{port}"
    return None


def _served_uri(log_path: Path, process: subprocess.Popen[bytes]) -> str:
    """Return this process's serving URI once it publishes that line.

    The wait is a hang guard. A process that exits first fails at once, with
    its log. A process that stays up and never publishes the line fails when
    the guard ends.
    """
    deadline = time.monotonic() + HUB_START_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(log_path.read_text(encoding="utf-8"))
        uri = _uri_published_by(log_path)
        if uri is not None:
            return uri
        time.sleep(0.05)
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    raise AssertionError(
        f"owned CLI hub did not publish its serving line in {HUB_START_SECONDS:g} s:\n{log}"
    )


def hub_environment(home: Path) -> dict[str, str]:
    """Give owned subprocesses a temporary home, keys and candidate imports."""
    root = Path(__file__).resolve().parents[1]
    environment = redirect_home(os.environ, home)
    environment["XDG_DATA_HOME"] = str(home / "data")
    environment["PYTHONPATH"] = os.pathsep.join((str(root / "src"), str(root / "tests")))
    environment["PYTHONUTF8"] = "1"
    return environment


@contextmanager
def owned_cli_hub(
    home: Path,
    database: Path | None,
    label: str,
    *,
    pin_file: str | None = None,
    extra_args: tuple[str, ...] = (),
) -> Iterator[str]:
    """Start an actual CLI hub in an owned home; join only its process on exit."""
    home.mkdir(parents=True, exist_ok=True)
    if database is not None:
        (home / database).parent.mkdir(parents=True, exist_ok=True)
    environment = hub_environment(home)
    actual_home = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; print(Path.home())"],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=HUB_START_SECONDS,
    )
    assert Path(actual_home.stdout.strip()).resolve() == home.resolve()
    command = [sys.executable, "-u", "-m", "synapse_channel.cli", "hub", "--port", "0"]
    if database is not None:
        command.extend(("--db", str(database)))
    if pin_file is not None:
        command.extend(("--identity-pins", pin_file))
    command.extend(extra_args)
    with (home / f"{label}.log").open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            cwd=home,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            yield _served_uri(home / f"{label}.log", process)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def registered_pin(uri: str, home: Path, name: str) -> None:
    """Register a real signed CLI client and await its actual roster reply."""
    reply = run_cli(
        "who",
        "--name",
        name,
        "--ready-timeout",
        f"{WHO_READY_HANG_GUARD_SECONDS:g}",
        uri=uri,
        env=hub_environment(home),
        cwd=home,
        timeout=WHO_PROCESS_HANG_GUARD_SECONDS,
    )
    assert reply.ok(), reply.output


@contextmanager
def _roster_after_welcome(delay: float) -> Iterator[str]:
    """Accept at once and send one roster only after the welcome delay.

    The delay is how long the peer withholds the welcome. The TCP listener is
    up before the caller connects, which is the shape a loaded hub already
    has when the roster query starts.
    """
    port = free_port()
    started = threading.Event()
    stop = threading.Event()
    errors: list[BaseException] = []

    async def handler(websocket: ServerConnection) -> None:
        try:
            await asyncio.sleep(delay)
            await websocket.send(json.dumps({"type": "welcome", "hub_id": "late-welcome"}))
            async for raw in websocket:
                message = json.loads(raw)
                if isinstance(message, dict) and message.get("type") == "who_request":
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "who_snapshot",
                                "online_agents": ["P/late-welcome"],
                                "sender": "System",
                            }
                        )
                    )
                    return
        except ConnectionClosed:
            return

    def serve_until_stopped() -> None:
        async def run() -> None:
            try:
                async with serve(handler, "127.0.0.1", port):
                    started.set()
                    while not stop.is_set():
                        await asyncio.sleep(0.05)
            except BaseException as exc:
                errors.append(exc)
                started.set()

        asyncio.run(run())

    thread = threading.Thread(target=serve_until_stopped, name="late-welcome", daemon=True)
    thread.start()
    if not started.wait(timeout=5):
        stop.set()
        raise AssertionError("the late welcome peer did not start")
    if errors:
        raise AssertionError(str(errors[0]))
    try:
        yield f"ws://127.0.0.1:{port}"
    finally:
        stop.set()
        thread.join(timeout=5)


def pin_names(path: Path) -> set[str]:
    """Read the names the running hub actually persisted, without fabricating pins."""
    record = json.loads(path.read_text(encoding="utf-8"))
    return set(record["pins"])


def test_separate_cli_databases_do_not_rewrite_the_conventional_hubs_pins(
    tmp_path: Path,
) -> None:
    """A sibling hub owns its pins across restart; the conventional pins remain intact."""
    home = tmp_path / "home"
    first_db = home / "synapse" / "hub.db"
    second_db = home / "synapse" / "second.db"
    first_pins = home / "synapse" / "identity-pins.json"
    second_pins = Path(str(second_db) + ".identity-pins.json")
    with owned_cli_hub(home, first_db, "first") as first_uri:
        registered_pin(first_uri, home, "P/pin-first")
        original = first_pins.read_bytes()
        assert pin_names(first_pins) == {"P/pin-first"}
        with owned_cli_hub(home, second_db, "second") as second_uri:
            registered_pin(second_uri, home, "P/pin-second")
            assert first_pins.read_bytes() == original
            assert pin_names(second_pins) == {"P/pin-second"}
        with owned_cli_hub(home, second_db, "second-restart") as second_uri:
            registered_pin(second_uri, home, "P/pin-second")
            assert pin_names(second_pins) == {"P/pin-second"}
            assert first_pins.read_bytes() == original
    with owned_cli_hub(home, first_db, "first-restart") as first_uri:
        registered_pin(first_uri, home, "P/pin-first")
        assert first_pins.read_bytes() == original


def test_custom_sibling_journals_start_with_separate_empty_pins(tmp_path: Path) -> None:
    """Two custom journals in one directory get distinct tables before first use."""
    home = tmp_path / "home"
    first = Path("journals/first.db")
    second = Path("journals/second.db")
    first_pins = home / "journals/first.db.identity-pins.json"
    second_pins = home / "journals/second.db.identity-pins.json"
    with owned_cli_hub(home, first, "custom-first") as first_uri:
        assert pin_names(first_pins) == set()
        if os.name == "posix":
            assert stat.S_IMODE(first_pins.stat().st_mode) == 0o600
        registered_pin(first_uri, home, "P/custom-first")
        original = first_pins.read_bytes()
        with owned_cli_hub(home, second, "custom-second") as second_uri:
            assert pin_names(second_pins) == set()
            registered_pin(second_uri, home, "P/custom-second")
            assert first_pins.read_bytes() == original
            assert pin_names(second_pins) == {"P/custom-second"}
    assert not list((home / "journals").glob("*.tmp"))


def test_new_custom_hub_can_restart_before_its_first_signed_client(tmp_path: Path) -> None:
    """An empty published companion distinguishes a new hub from an ambiguous upgrade."""
    home = tmp_path / "home"
    conventional = home / "synapse/hub.db"
    custom = home / "journals/new.db"
    companion = Path(f"{custom}.identity-pins.json")
    with owned_cli_hub(home, conventional, "legacy") as uri:
        registered_pin(uri, home, "P/legacy")
    original = (home / "synapse/identity-pins.json").read_bytes()
    with owned_cli_hub(home, custom, "new-empty"):
        assert pin_names(companion) == set()
    with owned_cli_hub(home, custom, "empty-restart") as uri:
        registered_pin(uri, home, "P/new")
        assert pin_names(companion) == {"P/new"}
    assert (home / "synapse/identity-pins.json").read_bytes() == original


def refused_hub(home: Path, database: Path) -> subprocess.CompletedProcess[str]:
    """Await a real startup refusal in an owned profile, with a bounded process lifetime."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "hub",
            "--db",
            str(database),
            "--port",
            str(free_port()),
        ],
        cwd=home,
        env=hub_environment(home),
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_existing_custom_journal_requires_explicit_trust_selection(tmp_path: Path) -> None:
    """An upgrade refuses ambiguity, then honours each operator-selected existing file."""
    home = tmp_path / "home"
    database = home / "journals/existing.db"
    legacy = home / "synapse/identity-pins.json"
    companion = Path(f"{database}.identity-pins.json")
    with owned_cli_hub(home, database, "old-custom", pin_file=str(legacy)) as uri:
        registered_pin(uri, home, "P/existing")
    original_pins = legacy.read_bytes()
    original_journal = database.read_bytes()
    refusal = refused_hub(home, database)
    assert refusal.returncode == 2, refusal.stderr
    assert str(legacy) in refusal.stderr
    assert str(companion) in refusal.stderr
    assert "--identity-pins" in refusal.stderr
    assert "including names from other hubs" in refusal.stderr
    assert "Traceback" not in refusal.stderr
    assert legacy.read_bytes() == original_pins
    assert database.read_bytes() == original_journal
    assert not companion.exists()
    with owned_cli_hub(home, database, "explicit-legacy", pin_file=str(legacy)) as uri:
        registered_pin(uri, home, "P/existing")
    assert legacy.read_bytes() == original_pins
    companion.write_bytes(original_pins)
    with owned_cli_hub(home, database, "operator-prepared", pin_file=str(companion)) as uri:
        registered_pin(uri, home, "P/existing")
    with owned_cli_hub(home, database, "prepared-default") as uri:
        registered_pin(uri, home, "P/existing")
    assert companion.read_bytes() == original_pins
    assert legacy.read_bytes() == original_pins


def test_no_journal_and_explicit_empty_or_file_keep_their_contract(tmp_path: Path) -> None:
    """The no-db home file, explicit empty store, and explicit custom file stay distinct."""
    home = tmp_path / "home"
    legacy = home / "synapse/identity-pins.json"
    database = home / "journals/memory-pins.db"
    with owned_cli_hub(home, None, "no-db") as uri:
        registered_pin(uri, home, "P/no-db")
    original = legacy.read_bytes()
    with owned_cli_hub(home, database, "explicit-memory", pin_file="") as uri:
        registered_pin(uri, home, "P/volatile")
    assert not Path(f"{database}.identity-pins.json").exists()
    assert legacy.read_bytes() == original
    explicit = home / "selected/pins.json"
    with owned_cli_hub(home, database, "explicit-file", pin_file=str(explicit)) as uri:
        registered_pin(uri, home, "P/selected")
    with owned_cli_hub(home, database, "explicit-file-restart", pin_file=str(explicit)) as uri:
        registered_pin(uri, home, "P/selected")
    assert pin_names(explicit) == {"P/selected"}
    assert legacy.read_bytes() == original


def test_invalid_or_unwritable_companion_refuses_before_creating_journal(tmp_path: Path) -> None:
    """Real filesystem failures never reset malformed pins or fall back to volatile trust."""
    home = tmp_path / "home"
    home.mkdir()
    database = home / "journals/bad.db"
    database.parent.mkdir()
    companion = Path(f"{database}.identity-pins.json")
    companion.write_text("malformed-canary", encoding="utf-8")
    refusal = refused_hub(home, database)
    assert refusal.returncode == 2
    assert "could not initialise the isolated identity-pin file" in refusal.stderr
    assert "malformed-canary" not in refusal.stderr
    assert "Traceback" not in refusal.stderr
    assert companion.read_text() == "malformed-canary"
    assert not database.exists()
    companion.unlink()
    companion.mkdir()
    refusal = refused_hub(home, database)
    assert refusal.returncode == 2
    assert companion.is_dir()
    assert not database.exists()
    blocker = home / "blocker"
    blocker.write_text("keep", encoding="utf-8")
    refusal = refused_hub(home, blocker / "hub.db")
    assert refusal.returncode == 2
    assert "No in-memory fallback" in refusal.stderr
    assert blocker.read_text() == "keep"
    assert not list(database.parent.glob("*.tmp"))
    directory = home / "directory.db"
    directory.mkdir()
    refusal = refused_hub(home, directory)
    assert refusal.returncode == 2
    assert "Traceback" not in refusal.stderr
    assert directory.is_dir()
    assert not Path(f"{directory}.identity-pins.json").exists()
    if os.name == "posix":
        loop = home / "loop.db"
        loop.symlink_to(loop)
        refusal = refused_hub(home, loop)
        assert refusal.returncode == 2
        assert "Traceback" not in refusal.stderr
        assert loop.is_symlink()
        target = home / "another-hubs-pins.json"
        target.write_text('{"pins": {}}\n', encoding="utf-8")
        companion.rmdir()
        companion.symlink_to(target)
        refusal = refused_hub(home, database)
        assert refusal.returncode == 2
        assert "Traceback" not in refusal.stderr
        assert companion.is_symlink()
        assert target.read_text() == '{"pins": {}}\n'


def test_restarted_custom_hub_refuses_a_different_machine_for_its_pinned_name(
    tmp_path: Path,
) -> None:
    """The isolated file still enforces possession of the original key after restart."""
    home = tmp_path / "owner"
    database = home / "journals/protected.db"
    companion = Path(f"{database}.identity-pins.json")
    name = "P/protected-owner"
    with owned_cli_hub(home, database, "owner-first") as uri:
        registered_pin(uri, home, name)
    original = companion.read_bytes()
    other_home = tmp_path / "different-machine"
    other_home.mkdir()
    with owned_cli_hub(home, database, "owner-restart") as uri:
        refused = run_cli(
            "send",
            "continuity-probe",
            "--name",
            name,
            "--ready-timeout",
            "1",
            uri=uri,
            env=hub_environment(other_home),
            cwd=other_home,
        )
        assert not refused.ok(), refused.output
        assert "identity" in refused.output.lower()
        assert "Traceback" not in refused.output
        registered_pin(uri, home, name)
        assert companion.read_bytes() == original


def test_sqlite_memory_journal_keeps_the_no_journal_pin_default(tmp_path: Path) -> None:
    """The special SQLite name never becomes a colon-bearing pin filename."""
    home = tmp_path / "home"
    legacy = home / "synapse/identity-pins.json"
    flags = ("--row-mac-key-file", str(home / "row.key"))
    with owned_cli_hub(home, Path(":memory:"), "sqlite-memory", extra_args=flags) as uri:
        registered_pin(uri, home, "P/sqlite-memory")
    original = legacy.read_bytes()
    with owned_cli_hub(home, Path(":memory:"), "sqlite-memory-restart", extra_args=flags) as uri:
        registered_pin(uri, home, "P/sqlite-memory")
    assert legacy.read_bytes() == original
    assert not (home / ":memory:.identity-pins.json").exists()
    assert not (home / ":memory:").exists()


def test_later_auth_refusal_keeps_the_companion_for_a_subsequent_start(tmp_path: Path) -> None:
    """A later startup refusal retains the complete pin namespace and closes its journal."""
    home = tmp_path / "home"
    home.mkdir()
    database = home / "journals/auth-refused.db"
    companion = Path(f"{database}.identity-pins.json")
    policy = home / "invalid-policy.json"
    policy.write_text("[]", encoding="utf-8")
    refusal = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "hub",
            "--db",
            str(database),
            "--port",
            str(free_port()),
            "--acl-policy",
            str(policy),
        ],
        cwd=home,
        env=hub_environment(home),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert refusal.returncode == 2, refusal.stderr
    assert "Traceback" not in refusal.stderr
    assert pin_names(companion) == set()
    assert database.exists()
    assert policy.read_text() == "[]"
    assert not list(database.parent.glob("*.tmp"))
    with owned_cli_hub(home, database, "after-auth-refusal") as uri:
        registered_pin(uri, home, "P/after-auth-refusal")
        assert pin_names(companion) == {"P/after-auth-refusal"}


def test_roster_query_waits_out_a_welcome_that_misses_five_seconds(tmp_path: Path) -> None:
    """The registration call accepts a welcome that arrives after five seconds.

    Five seconds is the roster query's own default. This peer accepts at once
    and welcomes only after six seconds, so dropping the named hang guard, or
    putting that default back, fails the registration. The wait is one budget.
    """
    home = tmp_path / "home"
    home.mkdir()
    with _roster_after_welcome(_LATE_WELCOME_SECONDS) as uri:
        registered_pin(uri, home, "P/late-welcome")


def test_a_peer_that_never_welcomes_fails_the_roster_query_once(tmp_path: Path) -> None:
    """A hub that accepts and never welcomes within the budget fails once.

    The short budget is explicit so the missing welcome is not waited out.
    A second attempt would print the reach failure again. The process budget
    only covers a slow interpreter start.
    """
    home = tmp_path / "home"
    home.mkdir()
    with _roster_after_welcome(WHO_READY_HANG_GUARD_SECONDS) as uri:
        reply = run_cli(
            "who",
            "--name",
            "P/silent",
            "--ready-timeout",
            "0.4",
            uri=uri,
            env=hub_environment(home),
            cwd=home,
            timeout=WHO_PROCESS_HANG_GUARD_SECONDS,
        )
    assert not reply.ok(), reply.output
    assert reply.output.count("Could not reach hub") == 1
    assert "Traceback" not in reply.output
