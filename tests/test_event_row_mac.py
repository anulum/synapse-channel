# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — K4-REPLAY: a forged event row is quarantined, not replayed
"""A row appended to the log by anyone but the hub does not become hub state.

Every case uses real SQLite files, real key files and, for the end-to-end cases, a
real hub. The forgery is what the finding describes: a writer with access to the
database file appends (or edits) an event row directly.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_processes
from synapse_channel.core.event_row_mac import (
    RowMacError,
    RowMacKey,
    load_or_create_row_mac_key,
    row_mac_key_path,
)
from synapse_channel.core.event_row_recovery import CORRUPT_EVENT_KIND, CorruptEventReason
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind, record_claim
from synapse_channel.core.merkle_checkpoint import MerkleCheckpointStore, checkpoint_path_for
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import (
    migrate_plaintext_to_sqlcipher,
    sqlcipher_available,
)
from synapse_channel.core.state import TaskClaim

_FORGED = (
    '{"task_id":"FORGED","owner":"P/mallory","note":"","claimed_at":1000.0,'
    '"lease_expires_at":9999999999.0,"status":"claimed","data_ref":"","worktree":"wt",'
    '"paths":["src"],"epoch":99}'
)


def _claim(task_id: str, owner: str = "P/owner") -> TaskClaim:
    return TaskClaim(
        task_id=task_id,
        owner=owner,
        note="",
        claimed_at=1000.0,
        lease_expires_at=9_999_999_999.0,
        status="claimed",
        data_ref="",
        worktree="wt",
        paths=("src",),
        epoch=1,
    )


def _append_raw(db: Path, kind: str, payload: str) -> int:
    """Append a row the way an attacker with file access would: no MAC."""
    conn = sqlite3.connect(str(db))
    cursor = conn.execute(
        "INSERT INTO events (ts, kind, payload) VALUES (?, ?, ?)", (1000.0, kind, payload)
    )
    conn.commit()
    seq = int(cursor.lastrowid or 0)
    conn.close()
    return seq


def _open(db: Path) -> tuple[EventStore, RowMacKey]:
    store = EventStore(db)
    key = load_or_create_row_mac_key(
        row_mac_key_path(db),
        current_max_seq=store.max_seq(),
        log_has_macs=store.has_row_macs(),
    )
    store.enable_row_mac(key)
    return store, key


def test_the_mac_binds_every_stored_field() -> None:
    key = RowMacKey(key=b"k" * 32, since_seq=0)
    base = key.mac(5, 1.5, "claim", "{}")
    assert base == key.mac(5, 1.5, "claim", "{}")
    assert len({base, key.mac(6, 1.5, "claim", "{}"), key.mac(5, 2.5, "claim", "{}")}) == 3
    assert base not in (key.mac(5, 1.5, "release", "{}"), key.mac(5, 1.5, "claim", "{ }"))
    assert base != RowMacKey(key=b"x" * 32, since_seq=0).mac(5, 1.5, "claim", "{}")
    assert key.verify(5, 1.5, "claim", "{}", base)
    for ts, kind, payload, mac in (
        (True, "claim", "{}", base),
        ("1.5", "claim", "{}", base),
        (1.5, None, "{}", base),
        (1.5, "claim", b"{}", base),
        (1.5, "claim", "{}", None),
    ):
        assert not key.verify(5, ts, kind, payload, mac)


def test_the_key_file_is_created_once_owner_only_and_reloaded(tmp_path: Path) -> None:
    path = tmp_path / "hub.db.rowmac.key"
    created = load_or_create_row_mac_key(path, current_max_seq=7, log_has_macs=False)
    assert created.since_seq == 7
    assert os.stat(path).st_mode & 0o777 == 0o600
    again = load_or_create_row_mac_key(path, current_max_seq=50, log_has_macs=True)
    assert again == created


@pytest.mark.parametrize(
    "content",
    [
        "not-a-key 1 AAAA\n",
        "synapse-row-mac-v1 x AAAA\n",
        "synapse-row-mac-v1 1 !!!!\n",
        "synapse-row-mac-v1 1 AAAA\n",
        "synapse-row-mac-v1 -1 " + "A" * 44 + "\n",
    ],
)
def test_a_malformed_key_file_is_refused(tmp_path: Path, content: str) -> None:
    path = tmp_path / "key"
    path.write_text(content, encoding="ascii")
    path.chmod(0o600)
    with pytest.raises(RowMacError, match="malformed|is not a"):
        load_or_create_row_mac_key(path, current_max_seq=0, log_has_macs=False)


def test_a_readable_by_others_or_lost_or_uncreatable_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "key"
    load_or_create_row_mac_key(path, current_max_seq=0, log_has_macs=False)
    path.chmod(0o644)
    with pytest.raises(RowMacError, match="cannot read"):
        load_or_create_row_mac_key(path, current_max_seq=0, log_has_macs=False)
    with pytest.raises(RowMacError, match="key .* is missing"):
        load_or_create_row_mac_key(tmp_path / "gone", current_max_seq=3, log_has_macs=True)
    with pytest.raises(RowMacError, match="cannot create"):
        load_or_create_row_mac_key(
            tmp_path / "no-dir" / "key", current_max_seq=0, log_has_macs=False
        )


def test_a_forged_or_edited_row_is_quarantined_and_legacy_rows_are_not(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    legacy = EventStore(db)
    record_claim(legacy, _claim("LEGACY"))
    legacy.close()

    store, key = _open(db)
    assert key.since_seq == 1
    record_claim(store, _claim("AUTHENTIC"))
    authentic = store.max_seq()
    store.close()
    forged = _append_raw(db, EventKind.CLAIM, _FORGED)
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE events SET payload = ? WHERE seq = ?", (_FORGED, authentic))
    conn.commit()
    conn.close()

    reopened, _ = _open(db)
    try:
        quarantine = {row.seq: row.reasons for row in reopened.enable_row_mac(key)}
        assert quarantine == {
            authentic: (CorruptEventReason.ROW_MAC_INVALID,),
            forged: (CorruptEventReason.ROW_MAC_MISSING,),
        }
        assert reopened.row_quarantine == frozenset({authentic, forged})
        kinds = [event.kind for event in reopened.iter_events()]
        assert kinds == [EventKind.CLAIM, CORRUPT_EVENT_KIND, CORRUPT_EVENT_KIND]
        raw = [event.kind for event in reopened.iter_events(apply_row_quarantine=False)]
        assert raw == [EventKind.CLAIM, EventKind.CLAIM, EventKind.CLAIM]
    finally:
        reopened.close()


async def test_a_real_hub_does_not_replay_a_forged_claim_and_refuses_mutations(
    tmp_path: Path,
) -> None:
    db = tmp_path / "hub.db"
    store, _ = _open(db)
    hub = SynapseHub(journal=store)
    async with running_hub(hub) as (_hub, uri):
        owner = await connect_agent("P/owner", uri)
        try:
            await owner.agent.claim("REAL", worktree="wt", paths=["src"])
            await owner.recorder.wait_for(lambda m: m.get("type") == "claim_granted")
        finally:
            await close_agents(owner)
    store.close()
    forged = _append_raw(db, EventKind.CLAIM, _FORGED)

    restarted_store, _ = _open(db)
    restarted = SynapseHub(journal=restarted_store)
    try:
        assert "REAL" in restarted.state.claims
        assert "FORGED" not in restarted.state.claims
        assert [row.seq for row in restarted.journal_corrupt_rows] == [forged]
        async with running_hub(restarted) as (_hub, uri):
            owner = await connect_agent("P/owner", uri)
            try:
                await owner.agent.claim("NEW", worktree="wt", paths=["docs"])
                refusal = await owner.recorder.wait_for(
                    lambda m: m.get("journal_recovery_required") is True
                )
            finally:
                await close_agents(owner)
        assert refusal["first_corrupt_seq"] == forged
    finally:
        restarted_store.close()


def test_the_checkpoint_and_the_row_mac_catch_different_attacks(tmp_path: Path) -> None:
    """A row appended after the last anchor passes the checkpoint; the MAC stops it."""
    db = tmp_path / "hub.db"
    store, _ = _open(db)
    record_claim(store, _claim("REAL"))
    chain = MerkleCheckpointStore(checkpoint_path_for(db))
    chain.anchor(store)
    store.close()
    _append_raw(db, EventKind.CLAIM, _FORGED)
    reopened, _ = _open(db)
    try:
        chain.verify(reopened)  # a longer log is not a rollback
        assert len(reopened.row_quarantine) == 1
    finally:
        chain.close()
        reopened.close()


def test_the_hub_cli_creates_the_key_reports_quarantine_and_refuses_a_lost_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    assert cli_processes._cmd_hub(_hub_ns(db=str(db)), runner=_close_runner) == 0
    assert row_mac_key_path(db).is_file()
    store = EventStore(db)
    store.enable_row_mac(
        load_or_create_row_mac_key(row_mac_key_path(db), current_max_seq=0, log_has_macs=True)
    )
    record_claim(store, _claim("REAL"))
    store.close()
    _append_raw(db, EventKind.CLAIM, _FORGED)
    capsys.readouterr()

    assert cli_processes._cmd_hub(_hub_ns(db=str(db)), runner=_close_runner) == 0
    assert "1 event row(s) failed row authentication and are quarantined" in (
        capsys.readouterr().err
    )

    row_mac_key_path(db).rename(tmp_path / "moved.key")
    assert cli_processes._cmd_hub(_hub_ns(db=str(db)), runner=_close_runner) == 2
    assert "carries authenticated rows but the key" in capsys.readouterr().err

    custom = tmp_path / "custody" / "row.key"
    custom.parent.mkdir()
    (tmp_path / "moved.key").rename(custom)
    ns = _hub_ns(db=str(db), row_mac_key_file=str(custom))
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 0
    assert cli.build_parser().parse_args(["hub"]).row_mac_key_file is None


def test_the_quarantine_warning_is_bounded_for_many_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    assert cli_processes._cmd_hub(_hub_ns(db=str(db)), runner=_close_runner) == 0
    for _ in range(12):
        _append_raw(db, EventKind.RECALL, "{}")
    capsys.readouterr()
    assert cli_processes._cmd_hub(_hub_ns(db=str(db)), runner=_close_runner) == 0
    err = capsys.readouterr().err
    assert "12 event row(s) failed row authentication" in err
    assert "and 2 more" in err


@pytest.mark.skipif(not sqlcipher_available(), reason="SQLCipher driver not installed")
def test_row_macs_survive_the_sqlcipher_migration(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store, key = _open(db)
    record_claim(store, _claim("REAL"))
    store.close()
    encrypted = tmp_path / "hub.enc.db"
    migrate_plaintext_to_sqlcipher(db, encrypted, key=b"s" * 32)

    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy))
    conn.execute("CREATE TABLE events (seq INTEGER PRIMARY KEY, ts REAL, kind TEXT, payload TEXT)")
    conn.execute("INSERT INTO events VALUES (1, 1.0, 'recall', '{}')")
    conn.commit()
    conn.close()
    assert migrate_plaintext_to_sqlcipher(legacy, tmp_path / "legacy.enc.db", key=b"s" * 32) == {
        "rows": 1
    }

    reopened = EventStore(encrypted, key=b"s" * 32)
    try:
        assert reopened.enable_row_mac(key) == ()
    finally:
        reopened.close()
