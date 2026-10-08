# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — real startup refusals without opening a server
"""Drive invalid startup inputs and real storage faults through the hub command."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Coroutine
from dataclasses import replace
from pathlib import Path

import pytest

from cli_processes_hub_helpers import _write_identity_trust
from synapse_channel.cli import build_parser
from synapse_channel.cli_processes_hub import _cmd_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.persistence import EventStore


def test_embedded_invalid_sequence_mode_refuses_before_journal_creation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Embedding cannot bypass the sequence enum by supplying an invalid namespace."""
    database = tmp_path / "events.db"
    args = build_parser().parse_args(["hub", "--db", str(database)])
    args.message_auth_sequence_floor_mode = "invalid"
    assert _cmd_hub(args) == 2
    assert "SequenceFloorMode" in capsys.readouterr().err
    assert not database.exists()


def test_sequence_mode_without_authentication_refuses_before_journal_creation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid sequence mode cannot arm an unauthenticated replay contract."""
    database = tmp_path / "events.db"
    args = build_parser().parse_args(
        ["hub", "--db", str(database), "--message-auth-sequence-floor-mode", "compat"]
    )
    assert _cmd_hub(args) == 2
    assert "requires --require-message-auth" in capsys.readouterr().err
    assert not database.exists()


def test_invalid_at_rest_key_refuses_actual_store_initialization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real store rejects unusable key material without creating a database."""
    key = tmp_path / "key"
    key.write_bytes(b"invalid-key")
    key.chmod(0o600)
    database = tmp_path / "events.db"
    args = build_parser().parse_args(["hub", "--db", str(database), "--db-key-file", str(key)])
    assert _cmd_hub(args) == 2
    assert "synapse hub:" in capsys.readouterr().err
    assert not database.exists()


def test_corrupt_replay_database_refuses_real_replay_initialization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A corrupt durable replay ledger cannot silently become a memory-only cache."""
    replay = tmp_path / "replay.db"
    original = b"not a SQLite database"
    replay.write_bytes(original)
    replay.chmod(0o600)
    args = build_parser().parse_args(
        [
            "hub",
            "--require-message-auth",
            "--message-auth-key",
            "key:test-key:agent",
            "--message-auth-replay-db",
            str(replay),
        ]
    )
    assert _cmd_hub(args) == 2
    assert "cannot open message-auth replay ledger" in capsys.readouterr().err
    assert replay.read_bytes() == original


def test_malformed_operator_relay_route_refuses_before_serving(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An explicit namespace grant does not turn a malformed route into a peer."""
    args = build_parser().parse_args(
        [
            "hub",
            "--hub-id",
            "local",
            "--namespace-owner",
            "PROJECT=local",
            "--relay-peer",
            "malformed",
        ]
    )
    assert _cmd_hub(args) == 2
    assert "synapse hub:" in capsys.readouterr().err


def test_bind_enforces_the_actual_embedded_hub_exposure_policy(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stricter real factory hub still refuses the command's exposed opt-out."""
    args = build_parser().parse_args(
        [
            "hub",
            "--host",
            "0.0.0.0",
            "--port",
            "0",
            "--identity-pins",
            "",
            "--insecure-off-loopback",
            "--insecure-unbound-identity",
        ]
    )

    def strict_hub(**options: object) -> SynapseHub:
        """Return a real embedding hub with an explicitly stricter bind policy."""
        config = HubConfig.from_kwargs(options)
        return SynapseHub.from_config(
            replace(config, auth=replace(config.auth, insecure_off_loopback=False))
        )

    def runner(server: Coroutine[object, object, None]) -> None:
        """Drive the actual server so its independent exposure guard runs."""
        asyncio.run(server)

    assert _cmd_hub(args, runner=runner, hub_factory=strict_hub) == 2
    assert "Refusing to bind" in capsys.readouterr().err


def test_unusable_attachment_root_refuses_and_releases_the_real_journal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Complete trust material cannot turn an ordinary file into a private store."""
    trust = tmp_path / "identity.json"
    _write_identity_trust(trust)
    trust.chmod(0o600)
    acl = tmp_path / "acl.json"
    acl.write_text('{"rules": []}')
    acl.chmod(0o600)
    roles = tmp_path / "roles.json"
    roles.write_text('{"grants": {}}')
    roles.chmod(0o600)
    root = tmp_path / "attachments"
    root.write_text("ordinary-file")
    journal = tmp_path / "events.db"
    opened: list[EventStore] = []

    def open_store(path: str, *, key_file: str | None = None) -> EventStore:
        """Retain the actual SQLite allocation for its post-refusal close probe."""
        store = EventStore(path, key_file=key_file)
        opened.append(store)
        return store

    args = build_parser().parse_args(
        [
            "hub",
            "--db",
            str(journal),
            "--token",
            "test-token",
            "--identity-trust",
            str(trust),
            "--require-identity-binding",
            "--require-message-auth",
            "--message-auth-key",
            "key:test-key:proj/claude",
            "--require-acl",
            "--acl-policy",
            str(acl),
            "--role-grants",
            str(roles),
            "--attachment-root",
            str(root),
            "--identity-pins",
            "",
        ]
    )
    assert _cmd_hub(args, store_factory=open_store) == 2
    assert "cannot open attachment store" in capsys.readouterr().err
    assert root.read_text() == "ordinary-file"
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].count()
