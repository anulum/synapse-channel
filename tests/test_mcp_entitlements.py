# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — MCP entitlement disclosure tests
"""Exercise the registered MCP tool against a real private SQLite ledger."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from synapse_channel.core.entitlement_store import append_event, default_entitlement_store
from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.registration import build_mcp_server


async def test_mcp_tool_withholds_private_account_and_pool_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "state"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_STATE_HOME", str(home))
    path = default_entitlement_store()
    assert append_event(
        path,
        {
            "event_id": "a1",
            "kind": "account",
            "recorded_at": "2026-09-19T10:00:00Z",
            "source": "operator:owner",
            "confidence": "operator",
            "account_id": "secret-id",
            "label": "Confidential Label",
            "status": "active",
            "credential_ref": "ref:private-key",
        },
    )
    assert append_event(
        path,
        {
            "event_id": "p1",
            "kind": "pool",
            "recorded_at": "2026-09-19T10:00:00Z",
            "source": "operator:owner",
            "confidence": "operator",
            "pool_id": "secret-pool",
            "account_id": "secret-id",
            "unit": "tokens",
        },
    )
    server = build_mcp_server(SynapseHubBridge(name="SYNAPSE-CHANNEL/untrusted-reader"))
    output = str(await server.call_tool("synapse_entitlements", {}))
    assert "private_details" in output
    assert "Confidential Label" not in output
    assert "secret-pool" not in output
    assert "ref:private-key" not in output


async def test_mcp_reports_corrupt_private_store_without_disclosing_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = default_entitlement_store()
    append_event(
        path,
        {
            "event_id": "a1",
            "kind": "account",
            "recorded_at": "2026-09-19T10:00:00Z",
            "source": "operator:owner",
            "confidence": "operator",
            "account_id": "secret-id",
            "label": "Confidential Label",
            "status": "active",
        },
    )
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE events SET payload='not-json'")
    server = build_mcp_server(SynapseHubBridge(name="SYNAPSE-CHANNEL/untrusted-reader"))
    output = str(await server.call_tool("synapse_entitlements", {}))
    assert "Confidential Label" not in output
    assert "private ledger unavailable" in output
