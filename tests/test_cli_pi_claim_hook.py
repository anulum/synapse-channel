# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pi native claim-hook CLI tests
"""Exercise the public CLI's bounded, JSON-only refusal contract."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

from synapse_channel import cli_pi_claim_hook
from synapse_channel.cli_participants_pi import (
    add_pi_connection_arguments,
    build_cli_participant,
)
from synapse_channel.file_claim_guard import GuardVerdict
from synapse_channel.participants.headless_pi import PiParticipant
from synapse_channel.pi_claim_guard import MAX_PI_HOOK_BYTES


def _run_hook(root: Path, payload: bytes) -> subprocess.CompletedProcess[bytes]:
    """Invoke the real parser and hook handler with stdin as pi supplies it."""
    source = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, PYTHONPATH=str(source))
    argv = [
        sys.executable,
        "-c",
        "from synapse_channel.cli import main; raise SystemExit(main())",
        "adapters",
        "pi-claim-hook",
        "--identity",
        "PROJECT/seat",
        "--project",
        "PROJECT",
        "--repository",
        str(root),
        "--task-id",
        "TASK-1",
        "--epoch",
        "7",
        "--session-id",
        "session-1",
        "--uri",
        "ws://127.0.0.1:1",
    ]
    return subprocess.run(  # nosec B603
        argv,
        input=payload,
        capture_output=True,
        check=False,
        env=env,
        timeout=15,
    )


def test_invalid_and_oversize_pi_events_emit_one_denial(tmp_path: Path) -> None:
    """Malformed input never leaks a traceback or hangs on an unavailable hub."""
    for payload in (b"not json", b"x" * (MAX_PI_HOOK_BYTES + 1)):
        completed = _run_hook(tmp_path, payload)
        assert completed.returncode == 0
        lines = completed.stdout.decode("utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["allowed"] is False
        assert not completed.stderr


def _args(root: Path) -> argparse.Namespace:
    """Construct the same parsed fields as the public native hook parser."""
    return argparse.Namespace(
        identity="PROJECT/seat",
        project="PROJECT",
        repository=str(root),
        task_id="TASK-1",
        epoch=7,
        session_id="session-1",
        uri="ws://unused",
        token=None,
        ready_timeout=1.0,
    )


def test_hook_cli_renders_exactly_one_live_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The public hook handler renders a bounded allow response without event content."""

    async def allow(*_args: object, **_kwargs: object) -> GuardVerdict:
        return GuardVerdict(True)

    monkeypatch.setattr(cli_pi_claim_hook, "evaluate_pi_hook", allow)
    payload = io.TextIOWrapper(io.BytesIO(b'{"event":"tool_call"}'), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", payload)
    assert cli_pi_claim_hook._cmd_pi_claim_hook(_args(tmp_path)) == 0
    assert json.loads(capsys.readouterr().out) == {"allowed": True}


def test_claim_status_reports_exact_epoch_or_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The launch helper never invents an epoch for a missing claim."""
    claim = {
        "task_id": "TASK-1",
        "owner": "PROJECT/seat",
        "status": "claimed",
        "worktree": str(tmp_path),
        "epoch": 7,
    }

    async def active(**_kwargs: object) -> dict[str, object]:
        return {"active_claims": [claim]}

    monkeypatch.setattr(cli_pi_claim_hook, "fetch_state_snapshot", active)
    assert cli_pi_claim_hook._cmd_pi_claim_status(_args(tmp_path)) == 0
    assert json.loads(capsys.readouterr().out) == {"eligible": True, "epoch": 7}

    async def unavailable(**_kwargs: object) -> dict[str, object]:
        raise ConnectionError("hub down")

    monkeypatch.setattr(cli_pi_claim_hook, "fetch_state_snapshot", unavailable)
    assert cli_pi_claim_hook._cmd_pi_claim_status(_args(tmp_path)) == 1
    assert json.loads(capsys.readouterr().out) == {"eligible": False, "epoch": None}


def test_pi_cli_requires_complete_claim_binding(tmp_path: Path) -> None:
    """A partial coding-tool recipe cannot silently fall back to unguarded tools."""
    parser = argparse.ArgumentParser()
    add_pi_connection_arguments(parser)
    args = parser.parse_args(["--pi-directory", str(tmp_path), "--pi-project", "PROJECT"])

    def fallback(*_args: object, **_kwargs: object) -> PiParticipant:
        raise AssertionError("pi must not delegate to another provider")

    with pytest.raises(ValueError, match="require extension"):
        build_cli_participant(
            "pi",
            identity="PROJECT/seat",
            model="local",
            timeout=5,
            args=args,
            fallback=fallback,
        )
    empty = parser.parse_args(["--pi-directory", str(tmp_path)])
    participant = build_cli_participant(
        "pi",
        identity="PROJECT/seat",
        model="local",
        timeout=5,
        args=empty,
        fallback=fallback,
    )
    assert isinstance(participant, PiParticipant)

    extension = tmp_path / "guard.ts"
    extension.write_text("export default function() {}", encoding="utf-8")
    complete = parser.parse_args(
        [
            "--pi-directory",
            str(tmp_path),
            "--pi-extension",
            str(extension),
            "--pi-project",
            "PROJECT",
            "--pi-repository",
            str(tmp_path),
            "--pi-task-id",
            "TASK-1",
            "--pi-epoch",
            "7",
            "--pi-hub-uri",
            "ws://unused",
        ]
    )
    guarded = build_cli_participant(
        "pi",
        identity="PROJECT/seat",
        model="local",
        timeout=5,
        args=complete,
        fallback=fallback,
    )
    assert isinstance(guarded, PiParticipant)

    assert (
        build_cli_participant(
            "other",
            identity="PROJECT/seat",
            model="local",
            timeout=5,
            args=empty,
            fallback=lambda *_args, **_kwargs: participant,
        )
        is participant
    )


def test_hook_cli_error_path_is_one_generic_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An internal failure or oversize stdin never emits traceback or allowance."""

    async def broken(*_args: object, **_kwargs: object) -> GuardVerdict:
        raise RuntimeError("private provider detail")

    monkeypatch.setattr(cli_pi_claim_hook, "evaluate_pi_hook", broken)
    stream = io.TextIOWrapper(io.BytesIO(b'{"event":"tool_call"}'), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stream)
    assert cli_pi_claim_hook._cmd_pi_claim_hook(_args(tmp_path)) == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["allowed"] is False
    assert "private provider detail" not in json.dumps(outcome)

    oversized = io.TextIOWrapper(io.BytesIO(b"x" * (MAX_PI_HOOK_BYTES + 1)))
    monkeypatch.setattr(sys, "stdin", oversized)
    assert cli_pi_claim_hook._cmd_pi_claim_hook(_args(tmp_path)) == 0
    assert json.loads(capsys.readouterr().out)["allowed"] is False


def test_public_pi_hook_and_status_parsers_require_claim_identity() -> None:
    """Both public adapter commands reject missing binding fields at parsing time."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)
    cli_pi_claim_hook.add_pi_claim_hook_parser(subparsers)
    cli_pi_claim_hook.add_pi_claim_status_parser(subparsers)
    with pytest.raises(SystemExit):
        parser.parse_args(["pi-claim-hook", "--identity", "PROJECT/seat"])
    with pytest.raises(SystemExit):
        parser.parse_args(["pi-claim-status", "--identity", "PROJECT/seat"])
    hook = parser.parse_args(
        [
            "pi-claim-hook",
            "--identity",
            "PROJECT/seat",
            "--project",
            "PROJECT",
            "--repository",
            "/repo",
            "--task-id",
            "TASK-1",
            "--epoch",
            "7",
            "--session-id",
            "session-1",
        ]
    )
    status = parser.parse_args(
        [
            "pi-claim-status",
            "--identity",
            "PROJECT/seat",
            "--project",
            "PROJECT",
            "--repository",
            "/repo",
            "--task-id",
            "TASK-1",
        ]
    )
    assert hook.func is cli_pi_claim_hook._cmd_pi_claim_hook
    assert status.func is cli_pi_claim_hook._cmd_pi_claim_status
