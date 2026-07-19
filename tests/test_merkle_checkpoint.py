# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — anti-rollback checkpoint regressions

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.merkle import build_root
from synapse_channel.core.merkle_checkpoint import (
    AntiRollbackError,
    MerkleCheckpointStore,
    checkpoint_path_for,
)
from synapse_channel.core.persistence import EventStore


def _seed(path: Path, count: int = 5) -> EventStore:
    store = EventStore(path)
    for seq in range(1, count + 1):
        # RECALL is telemetry: replay ignores it, so hub-startup tests can seed a
        # log without reconstructing coordination state.
        store.append(EventKind.RECALL, {"actor": "alice", "seq": seq}, ts=float(seq))
    return store


def _checkpoint(db: Path, ckpt_db: Path | None = None) -> MerkleCheckpointStore:
    return MerkleCheckpointStore(ckpt_db or checkpoint_path_for(db))


def _delete_from(db: Path, seq: int) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM events WHERE seq > ?", (seq,))
    conn.commit()
    conn.close()


def _rewrite_payload(db: Path, seq: int) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE events SET payload = ? WHERE seq = ?",
        (json.dumps({"actor": "mallory", "seq": "forged"}), seq),
    )
    conn.commit()
    conn.close()


def run_root_for(db: Path) -> str:
    store = EventStore(db)
    try:
        return build_root(store.read_all()).root
    finally:
        store.close()


# --- checkpoint path ---------------------------------------------------------


def test_checkpoint_path_lives_beside_the_log() -> None:
    assert checkpoint_path_for("/data/hub.db") == Path("/data/hub.db.checkpoint.db")


# --- MerkleCheckpointStore ---------------------------------------------------


def test_first_run_verify_is_clean(tmp_path: Path) -> None:
    store = _seed(tmp_path / "hub.db")
    try:
        _checkpoint(tmp_path / "hub.db").verify(store)
    finally:
        store.close()


def test_append_chains_and_refuses_non_advancing(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 3)
    try:
        ckpt = _checkpoint(db)
        first = ckpt.append(1, build_root(store.read_all(), through_seq=1).root)
        second = ckpt.append(3, build_root(store.read_all()).root)
        assert first.prev_hash == ""
        assert second.prev_hash == first.checkpoint_hash
        again = ckpt.append(3, "0" * 64)
        assert again.checkpoint_hash == second.checkpoint_hash
        assert again.root == second.root
        ckpt.close()
    finally:
        store.close()


def test_checkpoint_hash_binds_all_fields(tmp_path: Path) -> None:
    ckpt = MerkleCheckpointStore(tmp_path / "c.db", clock=lambda: 1234.5)
    entry = ckpt.append(7, "ab" * 32)
    expected = hashlib.sha256(f"7:{'ab' * 32}:{1234.5!r}:".encode()).hexdigest()
    assert entry.checkpoint_hash == expected
    ckpt.close()


def test_checkpoint_db_is_owner_only(tmp_path: Path) -> None:
    ckpt_db = tmp_path / "c.db"
    MerkleCheckpointStore(ckpt_db).close()
    assert os.stat(ckpt_db).st_mode & 0o777 == 0o600


def test_verify_detects_tail_truncation(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 5)
    ckpt = _checkpoint(db)
    ckpt.append(store.max_seq(), build_root(store.read_all()).root)
    store.close()
    _delete_from(db, 3)
    store = EventStore(db)
    try:
        with pytest.raises(AntiRollbackError, match="tail truncation"):
            ckpt.verify(store)
    finally:
        store.close()
        ckpt.close()


def test_verify_detects_log_rewrite(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 5)
    ckpt = _checkpoint(db)
    ckpt.append(store.max_seq(), build_root(store.read_all()).root)
    store.close()
    _rewrite_payload(db, 2)
    store = EventStore(db)
    try:
        with pytest.raises(AntiRollbackError, match="log rewrite"):
            ckpt.verify(store)
    finally:
        store.close()
        ckpt.close()


def test_verify_passes_when_log_matches_and_advanced(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 3)
    ckpt = _checkpoint(db)
    ckpt.append(store.max_seq(), build_root(store.read_all()).root)
    store.append(EventKind.RECALL, {"actor": "alice", "seq": 4}, ts=4.0)
    try:
        ckpt.verify(store)
    finally:
        store.close()
        ckpt.close()


# --- hub startup integration ---------------------------------------------------


def test_hub_anchors_checkpoint_on_startup(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 4)
    try:
        SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
        latest = _checkpoint(db).latest()
        assert latest is not None
        assert latest.seq == store.max_seq()
        assert latest.root == build_root(store.read_all()).root
    finally:
        store.close()


def test_hub_restart_is_clean_and_does_not_duplicate_checkpoint(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 4)
    try:
        SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
        first = _checkpoint(db).latest()
        SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
        second = _checkpoint(db).latest()
        assert first is not None and second is not None
        assert second.checkpoint_hash == first.checkpoint_hash
    finally:
        store.close()


def test_hub_restart_after_growth_chains_new_link(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 4)
    try:
        SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
        first = _checkpoint(db).latest()
        store.append(EventKind.RECALL, {"actor": "alice", "seq": 5}, ts=5.0)
        SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
        second = _checkpoint(db).latest()
        assert first is not None and second is not None
        assert second.seq == store.max_seq()
        assert second.prev_hash == first.checkpoint_hash
    finally:
        store.close()


def test_hub_startup_fails_closed_on_tail_truncation(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 5)
    SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    store.close()
    _delete_from(db, 3)
    store = EventStore(db)
    try:
        with pytest.raises(AntiRollbackError, match="tail truncation"):
            SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    finally:
        store.close()


def test_hub_startup_fails_closed_on_rewrite(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 5)
    SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    store.close()
    _rewrite_payload(db, 2)
    store = EventStore(db)
    try:
        with pytest.raises(AntiRollbackError, match="log rewrite"):
            SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    finally:
        store.close()


def test_hub_checkpoint_can_be_disabled(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 2)
    try:
        SynapseHub(
            default_ttl_seconds=300.0,
            hub_id="syn-test",
            journal=store,
            anti_rollback_checkpoint=False,
        )
        assert not checkpoint_path_for(db).exists()
    finally:
        store.close()


def test_hub_checkpoint_store_path_override(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    override = tmp_path / "elsewhere" / "ckpt.db"
    override.parent.mkdir()
    store = _seed(db, 2)
    try:
        SynapseHub(
            default_ttl_seconds=300.0,
            hub_id="syn-test",
            journal=store,
            checkpoint_store_path=override,
        )
        assert override.exists()
        assert not checkpoint_path_for(db).exists()
    finally:
        store.close()


# --- CLI ---------------------------------------------------------------------


def test_parser_wires_checkpoint_action() -> None:
    args = cli.build_parser().parse_args(
        ["merkle", "checkpoint", "hub.db", "--verify", "--json", "--checkpoint-db", "c.db"]
    )
    assert args.command == "merkle"
    assert args.merkle_command == "checkpoint"
    assert args.verify is True
    assert args.json is True
    assert args.checkpoint_db == "c.db"


def test_cli_checkpoint_show_first_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed(db).close()
    assert cli.main(["merkle", "checkpoint", str(db)]) == 1
    assert "no checkpoint store" in capsys.readouterr().err
    assert not checkpoint_path_for(db).exists()


def test_cli_checkpoint_verify_first_run_is_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed(db).close()
    assert cli.main(["merkle", "checkpoint", str(db), "--verify"]) == 0
    assert "first run" in capsys.readouterr().out
    assert not checkpoint_path_for(db).exists()


def test_cli_checkpoint_show_after_anchor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 4)
    SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    store.close()
    assert cli.main(["merkle", "checkpoint", str(db), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["seq"] == 4
    assert payload["root"] == run_root_for(db)
    assert payload["checkpoint_hash"]


def test_cli_checkpoint_verify_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 4)
    SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    store.close()
    assert cli.main(["merkle", "checkpoint", str(db), "--verify", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"root": run_root_for(db), "seq": 4, "valid": True}


def test_cli_checkpoint_verify_detects_truncation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 5)
    SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    store.close()
    _delete_from(db, 3)
    assert cli.main(["merkle", "checkpoint", str(db), "--verify", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert "tail truncation" in payload["reason"]


def test_cli_checkpoint_verify_detects_rewrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    store = _seed(db, 5)
    SynapseHub(default_ttl_seconds=300.0, hub_id="syn-test", journal=store)
    store.close()
    _rewrite_payload(db, 2)
    assert cli.main(["merkle", "checkpoint", str(db), "--verify"]) == 2
    assert "log rewrite" in capsys.readouterr().err


def test_cli_checkpoint_db_override(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    override = tmp_path / "custom.db"
    store = _seed(db, 3)
    SynapseHub(
        default_ttl_seconds=300.0,
        hub_id="syn-test",
        journal=store,
        checkpoint_store_path=override,
    )
    store.close()
    assert (
        cli.main(["merkle", "checkpoint", str(db), "--checkpoint-db", str(override), "--json"]) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["seq"] == 3


def test_cli_checkpoint_missing_event_store(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["merkle", "checkpoint", "/nonexistent/hub.db", "--verify"]) == 2
    assert "missing event store" in capsys.readouterr().err
