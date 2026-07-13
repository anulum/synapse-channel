# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — participant process-error boundary tests
"""Tests for safe provider subprocess failure reporting."""

from __future__ import annotations

import subprocess
import sys

from synapse_channel.participants.envelope import TurnRequest
from synapse_channel.participants.headless_gemini import GeminiParticipant
from synapse_channel.participants.process_error import (
    format_process_failure,
    format_process_start_failure,
)


def test_empty_diagnostic_is_explicit() -> None:
    reason = format_process_failure(provider="codex", binary="codex", returncode=2, stderr=" \n")
    assert reason == "'codex' exited 2: no diagnostic output"


def test_unknown_diagnostic_is_withheld() -> None:
    secret = "token=secret"
    reason = format_process_failure(provider="claude", binary="claude", returncode=1, stderr=secret)
    assert reason == "'claude' exited 1: provider diagnostic withheld (12 characters)"
    assert secret not in reason


def test_gemini_consumer_refusal_has_safe_classification() -> None:
    secret = "private-policy-body"
    reason = format_process_failure(
        provider="gemini",
        binary="gemini",
        returncode=1,
        stderr=f"IneligibleTierError: individual account refused\n{secret}",
    )
    assert "no longer serves consumer accounts" in reason
    assert "Antigravity CLI" in reason
    assert secret not in reason


def test_process_start_failures_never_reflect_exception_text() -> None:
    secret = "/private/workspace/token-secret"
    cases = (
        (FileNotFoundError(secret), "executable not found"),
        (PermissionError(secret), "permission denied"),
        (OSError(secret), "operating-system error"),
        (subprocess.SubprocessError(secret), "subprocess error"),
    )
    for error, expected in cases:
        reason = format_process_start_failure(binary="provider", error=error)
        assert reason == f"failed to run 'provider': {expected}"
        assert secret not in reason


def test_real_subprocess_stderr_does_not_cross_turn_boundary() -> None:
    """Exercise the production runner against a real failing subprocess."""
    participant = GeminiParticipant(
        "seat/gemini-real-process",
        binary=sys.executable,
        timeout=5.0,
    )
    result = participant.run_turn(TurnRequest(topic_id="stderr-boundary", prompt="ping"))
    assert result["is_error"] is True
    assert "provider diagnostic withheld" in result["reason"]
    assert "--prompt" not in result["reason"]
