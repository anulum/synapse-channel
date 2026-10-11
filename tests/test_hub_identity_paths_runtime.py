# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — isolated CLI hub identity-pin persistence
"""Prove independent CLI hubs retain their own durable identity pins."""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cli_e2e_helpers import free_port, run_cli


def hub_environment(home: Path) -> dict[str, str]:
    """Give owned subprocesses a temporary home, keys and candidate imports."""
    root = Path(__file__).resolve().parents[1]
    return {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_DATA_HOME": str(home / "data"),
        "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root / "tests"))),
        "PYTHONUTF8": "1",
    }


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
        timeout=10,
    )
    assert Path(actual_home.stdout.strip()).resolve() == home.resolve()
    port = free_port()
    command = [sys.executable, "-u", "-m", "synapse_channel.cli", "hub", "--port", str(port)]
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
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError((home / f"{label}.log").read_text())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise AssertionError("owned CLI hub did not become reachable")
            yield f"ws://127.0.0.1:{port}"
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
    reply = run_cli("who", "--name", name, uri=uri, env=hub_environment(home), cwd=home)
    assert reply.ok(), reply.output


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
