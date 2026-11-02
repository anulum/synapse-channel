# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — small input-shape refusals: Claude hook tool, quote file, endpoint scheme
"""Refuse an unsupported Claude tool, a non-object price quote and a schemeless endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.a2a_bind_exposure import a2a_endpoint_scheme_warnings
from synapse_channel.claude_claim_guard import ClaimGuardError, parse_hook_request
from synapse_channel.client.provider_budget import ProviderQuote


@pytest.mark.parametrize("tool", ["Read", "MultiEdit", "NotebookEdit", "edit"])
def test_claude_hook_for_an_unguarded_tool_is_refused(tool: str) -> None:
    """Only Edit, Write and Bash are guarded; any other tool name fails closed."""
    raw = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "session_id": "s-1",
            "tool_use_id": "t-1",
            "cwd": "/repo",
            "tool_input": {"file_path": "/repo/a.py"},
        }
    )
    with pytest.raises(
        ClaimGuardError, match="^Claude claim guard accepts only Edit, Write, or Bash calls\\.$"
    ):
        parse_hook_request(raw)


@pytest.mark.parametrize("document", [[], "quote", 1.5, None])
def test_price_quote_that_is_not_a_json_object_is_refused(tmp_path: Path, document: object) -> None:
    """A quote file holding a list, string, number or null never becomes a quote."""
    path = tmp_path / "quote.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="^provider quote must be a JSON object$"):
        ProviderQuote.load(path)


@pytest.mark.parametrize("tls_active", [False, True])
def test_endpoint_without_a_scheme_is_reported_for_discovery(tls_active: bool) -> None:
    """A network-path URL without a scheme gets one advisory whatever the TLS state."""
    warnings = a2a_endpoint_scheme_warnings("//peer.example:8080/a2a", tls_active=tls_active)
    assert warnings == [
        "--endpoint-url '//peer.example:8080/a2a' has no scheme; use absolute "
        "http:// or https:// for Agent Card discovery"
    ]
