# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the installed first-run demo path

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from synapse_channel import cli, cli_demo
from synapse_channel.demo import _free_port, run_coordination_demo

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_repo_text(relative_path: str) -> str:
    """Read a repository text file for first-run documentation checks."""
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _single_spaced(text: str) -> str:
    """Normalize documentation whitespace for prose phrase checks."""
    return " ".join(text.split())


def test_parser_routes_demo_to_installed_first_run_command() -> None:
    args = cli.build_parser().parse_args(["demo"])

    assert args.func is cli_demo._cmd_demo


async def test_installed_demo_drives_core_coordination_flow() -> None:
    result = await run_coordination_demo(_free_port())
    log = result.narration

    assert result.completed is True
    assert any("Claude and Codex" in line for line in log)
    assert any("CONFLICT REFUSED" in line for line in log)
    assert any("MUTATION DENIED" in line for line in log)
    assert any("HANDOFF" in line for line in log)
    assert any("VERIFIED RECEIPT" in line for line in log)


def test_cmd_demo_prints_success_criterion(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    assert cli.main(["demo", "--output", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "SYNAPSE CHANNEL — five-minute golden demo" in out
    assert (tmp_path / "golden-demo.json").is_file()
    assert (tmp_path / "golden-demo-dashboard.html").is_file()
    assert "success: coordination demo completed" in out


def test_public_docs_explain_the_60_second_install_doctor_demo_path() -> None:
    combined = _single_spaced(
        "\n".join(
            [
                _read_repo_text("README.md"),
                _read_repo_text("docs/quickstart.md"),
                _read_repo_text("docs/cli.md"),
                _read_repo_text("examples/README.md"),
            ]
        )
    )

    assert "First 60 seconds" in combined
    assert "python -m pip install synapse-channel" in combined
    assert "synapse doctor" in combined
    assert "synapse demo" in combined
    assert "success: coordination demo completed" in combined


async def test_demo_run_emits_no_handshake_abort_records(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The real demo flow keeps hub handshake-abort tracebacks off stderr.

    The readiness probe completes one clean WebSocket handshake and no logger
    is mutated anywhere, so an ``opening handshake failed`` record captured
    here means a regression reintroduced an aborting probe — or surfaced a
    genuine hub-side error the demo must not hide.
    """
    with caplog.at_level(logging.DEBUG, logger="synapse.hub.ws"):
        level_during = logging.getLogger("synapse.hub.ws").level
        result = await run_coordination_demo(_free_port())
        assert logging.getLogger("synapse.hub.ws").level == level_during

    assert any("HANDOFF" in line for line in result.narration)
    handshake_records = [
        record
        for record in caplog.records
        if record.name.startswith("synapse.hub.ws")
        and "opening handshake failed" in record.getMessage()
    ]
    assert handshake_records == []


def test_demo_import_has_no_global_logging_side_effect() -> None:
    """Importing the demo module leaves every logger untouched (isolated run)."""
    script = (
        "import logging\n"
        "names = ('websockets.server', 'synapse.hub.ws')\n"
        "def snap():\n"
        "    return {n: (logging.getLogger(n).level, list(logging.getLogger(n).filters))"
        " for n in names}\n"
        "before = snap()\n"
        "import synapse_channel.demo\n"
        "after = snap()\n"
        "assert before == after, (before, after)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr


async def test_demo_probe_deadline_holds_against_stalled_listener() -> None:
    """A listener that accepts TCP but never handshakes cannot stretch the probe.

    Each handshake attempt must be bounded by the remaining caller budget; a
    fixed per-attempt open timeout would hold a 0.2 s probe open for a full
    second against a stalled listener.
    """
    from synapse_channel import demo as demo_module

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
            await demo_module._await_listening(port, timeout=0.2)
    finally:
        release.set()
        listener.close()
        await listener.wait_closed()
    assert loop.time() - started < 0.9


async def test_demo_probe_leaves_logger_levels_untouched_on_both_exits() -> None:
    """The clean-handshake probe never mutates logger state, success or timeout."""
    from synapse_channel import demo as demo_module

    ws_logger = logging.getLogger("synapse.hub.ws")
    previous_level = ws_logger.level
    ws_logger.setLevel(logging.WARNING)
    try:
        with pytest.raises(TimeoutError, match="did not start listening"):
            await demo_module._await_listening(demo_module._free_port(), timeout=0.05)
        assert ws_logger.level == logging.WARNING
    finally:
        ws_logger.setLevel(previous_level)


async def test_demo_helpers_time_out_honestly(tmp_path: object) -> None:
    """The demo's waiters raise a named TimeoutError instead of hanging."""
    import pytest

    from synapse_channel import demo as demo_module

    inbox = demo_module.DemoInbox()
    with pytest.raises(TimeoutError, match="expected message did not arrive"):
        await inbox.wait_for(lambda _m: False, timeout=0.05)
    dead_port = demo_module._free_port()
    with pytest.raises(TimeoutError, match="did not start listening"):
        await demo_module._await_listening(dead_port, timeout=0.05)
