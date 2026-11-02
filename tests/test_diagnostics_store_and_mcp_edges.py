# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — doctor edges: encrypted-store refusals and MCP posture downgrades
"""The SQLCipher store check fails closed; the MCP posture warns on each weak setting."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from synapse_channel.client.diagnostics import check_mcp_posture, check_sqlcipher_event_store
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available
from synapse_channel.mcp.registration import MCP_EXTRA_HINT, REQUIRED_MCP_CLAIM_TOOLS

_REJECTED = (
    "cannot open encrypted event store with key file: "
    "SQLCipher key rejected (wrong key or not an encrypted store)"
)
_TOOLS = "claim tools registered: " + ", ".join(sorted(REQUIRED_MCP_CLAIM_TOOLS))


@pytest.mark.skipif(not sqlcipher_available(), reason="sqlcipher3-binary not installed")
def test_a_wrong_key_or_a_plaintext_store_fails_the_store_check(tmp_path: Path) -> None:
    """Another key, or a key file pointed at a plaintext store, is a closed failure."""
    key = generate_key_file(tmp_path / "hub.key")
    other = generate_key_file(tmp_path / "other.key")
    encrypted = tmp_path / "encrypted.db"
    plaintext = tmp_path / "plain.db"
    EventStore(encrypted, key_file=key).close()
    EventStore(plaintext).close()
    for store, key_file in ((encrypted, other), (plaintext, key)):
        diagnosis = check_sqlcipher_event_store(db_path=store, key_file=key_file)
        assert (diagnosis.status, diagnosis.detail) == ("fail", _REJECTED)
        assert "rekey" in diagnosis.remedy
    passing = check_sqlcipher_event_store(db_path=encrypted, key_file=key)
    assert (passing.status, passing.detail) == (
        "pass",
        "SQLCipher event store opens with key file (max_seq=0)",
    )


def test_a_missing_driver_fails_the_store_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a key file and a store but no driver, the check names the extra."""
    key = generate_key_file(tmp_path / "hub.key")
    store = tmp_path / "hub.db"
    EventStore(store).close()
    for driver in ("sqlcipher3", "pysqlcipher3"):  # the driver and its import fallback
        monkeypatch.setitem(sys.modules, driver, None)
    diagnosis = check_sqlcipher_event_store(db_path=store, key_file=key)
    assert (diagnosis.status, diagnosis.detail, diagnosis.remedy) == (
        "fail",
        "SQLCipher driver not installed",
        "pip install 'synapse-channel[sqlcipher]'",
    )


def test_an_argv_token_downgrades_an_otherwise_passing_posture() -> None:
    """A token on argv alone turns a clean MCP posture into a warning."""
    diagnosis = check_mcp_posture(token="secret", git_toplevel="/repo", mcp_extra_available=True)
    assert diagnosis.status == "warn"
    assert diagnosis.detail == (
        f"mcp extra importable; {_TOOLS}; git worktree resolvable (/repo); "
        "hub token present on argv (process-visible)"
    )
    assert diagnosis.remedy == "prefer --token-file with mode 0600; avoid --token SECRET"


def test_no_mcp_extra_and_no_git_binary_both_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unimportable MCP SDK and a PATH without git each add a warning and a remedy."""
    # Block the exact module the probe imports: a cached parent import must not mask it.
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    monkeypatch.setenv("PATH", str(tmp_path))
    diagnosis = check_mcp_posture(cwd=tmp_path)
    assert diagnosis.status == "warn"
    assert diagnosis.detail == (
        f"mcp extra not installed; {_TOOLS}; git worktree not resolvable from cwd; "
        "no hub token configured (ok for open loopback)"
    )
    assert diagnosis.remedy == (
        f"{MCP_EXTRA_HINT}; run doctor from a git checkout before using MCP git-claim tools"
    )
