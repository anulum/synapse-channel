# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse new coding-fleet` scaffold

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel import cli, cli_new
from synapse_channel.coding_fleet import _free_port, run_coding_agents_demo
from synapse_channel.coding_fleet_template import create_coding_fleet


def test_parser_routes_new_coding_fleet() -> None:
    args = cli.build_parser().parse_args(["new", "coding-fleet", "demo-workspace"])

    assert args.func is cli_new._cmd_new_coding_fleet
    assert args.path == "demo-workspace"
    assert args.force is False


def test_create_coding_fleet_writes_runnable_workspace(tmp_path: Path) -> None:
    target = tmp_path / "fleet"

    lines = create_coding_fleet(target)

    assert "created coding fleet workspace" in "\n".join(lines)
    assert (
        (target / "README.md").read_text(encoding="utf-8").startswith("# Synapse coding fleet demo")
    )
    assert "success: coding fleet demo completed" in (target / "README.md").read_text(
        encoding="utf-8"
    )
    assert "synapse_channel.coding_fleet" in (target / "run_demo.py").read_text(encoding="utf-8")
    assert (target / ".synapse" / "project").read_text(encoding="utf-8") == "coding-fleet\n"
    assert (target / "src" / "app" / "api.py").exists()
    assert (target / "tests" / "test_api.py").exists()


def test_create_coding_fleet_refuses_non_empty_directory(tmp_path: Path) -> None:
    target = tmp_path / "fleet"
    target.mkdir()
    (target / "notes.txt").write_text("keep me", encoding="utf-8")

    with pytest.raises(FileExistsError, match="not empty"):
        create_coding_fleet(target)

    assert (target / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_create_coding_fleet_force_keeps_unrelated_files(tmp_path: Path) -> None:
    target = tmp_path / "fleet"
    target.mkdir()
    (target / "notes.txt").write_text("keep me", encoding="utf-8")

    create_coding_fleet(target, force=True)

    assert (target / "notes.txt").read_text(encoding="utf-8") == "keep me"
    assert (target / "run_demo.py").exists()


def test_cmd_new_coding_fleet_prints_next_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "$(touch injected)"

    assert cli.main(["new", "coding-fleet", str(target)]) == 0

    out = capsys.readouterr().out
    assert "created coding fleet workspace" in out
    assert f"cd -- '{target}'" in out
    assert "python run_demo.py" in out


async def test_packaged_coding_fleet_demo_prevents_collisions() -> None:
    log = await run_coding_agents_demo(_free_port())

    assert any("claimed src/app/api.py" in line for line in log)
    assert any("refused" in line for line in log)
    assert any("disjoint scope, granted" in line for line in log)
    assert any("test-dev received:" in line for line in log)
    assert any("released" in line for line in log)


def test_cmd_new_coding_fleet_refuses_an_existing_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Scaffolding into an existing non-empty target is refused, exit 2."""
    target = tmp_path / "demo-fleet"
    target.mkdir()
    (target / "keep.txt").write_text("precious", encoding="utf-8")

    exit_code = cli.main(["new", "coding-fleet", str(target)])

    assert exit_code == 2
    assert "synapse new coding-fleet:" in capsys.readouterr().err
    assert (target / "keep.txt").read_text(encoding="utf-8") == "precious"


async def test_coding_fleet_helpers_time_out_honestly() -> None:
    """The fleet demo's waiters raise named timeouts instead of hanging."""
    from synapse_channel import coding_fleet

    inbox = coding_fleet.CodingFleetInbox()
    with pytest.raises(TimeoutError, match="expected message did not arrive"):
        await inbox.wait_for(lambda _m: False, timeout=0.05)
    with pytest.raises(TimeoutError, match="did not start listening"):
        await coding_fleet._await_listening(coding_fleet._free_port(), timeout=0.05)


async def test_coding_fleet_run_emits_no_handshake_abort_records(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real fleet-demo flow keeps hub handshake-abort tracebacks off stderr."""
    with caplog.at_level(logging.DEBUG, logger="synapse.hub.ws"):
        level_during = logging.getLogger("synapse.hub.ws").level
        log = await run_coding_agents_demo(_free_port())
        assert logging.getLogger("synapse.hub.ws").level == level_during

    assert any("disjoint scope, granted" in line for line in log)
    handshake_records = [
        record
        for record in caplog.records
        if record.name.startswith("synapse.hub.ws")
        and "opening handshake failed" in record.getMessage()
    ]
    assert handshake_records == []


def test_coding_fleet_import_has_no_global_logging_side_effect() -> None:
    """Importing the fleet demo leaves every logger untouched (isolated run)."""
    script = (
        "import logging\n"
        "names = ('websockets.server', 'synapse.hub.ws')\n"
        "def snap():\n"
        "    return {n: (logging.getLogger(n).level, list(logging.getLogger(n).filters))"
        " for n in names}\n"
        "before = snap()\n"
        "import synapse_channel.coding_fleet\n"
        "after = snap()\n"
        "assert before == after, (before, after)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr


async def test_coding_fleet_probe_deadline_holds_against_stalled_listener() -> None:
    """A listener that accepts TCP but never handshakes cannot stretch the probe.

    Each handshake attempt must be bounded by the remaining caller budget; a
    fixed per-attempt open timeout would hold a 0.2 s probe open for a full
    second against a stalled listener.
    """
    from synapse_channel import coding_fleet

    release = asyncio.Event()

    async def _stall(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await release.wait()
        writer.close()

    listener = await asyncio.start_server(_stall, "localhost", 0)
    port = int(listener.sockets[0].getsockname()[1])
    loop = asyncio.get_event_loop()
    started = loop.time()
    try:
        with pytest.raises(TimeoutError, match="did not start listening"):
            await coding_fleet._await_listening(port, timeout=0.2)
    finally:
        release.set()
        listener.close()
        await listener.wait_closed()
    assert loop.time() - started < 0.9


async def test_coding_fleet_probe_leaves_logger_levels_untouched_on_both_exits() -> None:
    """The clean-handshake probe never mutates logger state, success or timeout."""
    from synapse_channel import coding_fleet

    ws_logger = logging.getLogger("synapse.hub.ws")
    previous_level = ws_logger.level
    ws_logger.setLevel(logging.WARNING)
    try:
        with pytest.raises(TimeoutError, match="did not start listening"):
            await coding_fleet._await_listening(coding_fleet._free_port(), timeout=0.05)
        assert ws_logger.level == logging.WARNING
    finally:
        ws_logger.setLevel(previous_level)


def test_coding_fleet_demo_refuses_an_empty_narration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A demo run that produced no narration is an error, not silent success."""
    from synapse_channel import coding_fleet

    monkeypatch.setattr(
        "synapse_channel.coding_fleet.asyncio.run", lambda coro: (coro.close(), [])[1]
    )
    with pytest.raises(RuntimeError, match="produced no narration"):
        coding_fleet.main()
