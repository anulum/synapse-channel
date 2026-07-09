# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — synapse sqlcipher CLI regressions

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.cli_sqlcipher import _cmd_migrate, _cmd_rekey
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import SqlCipherKeyError, sqlcipher_available
from synapse_channel.core.state import TaskClaim


def test_parser_registers_sqlcipher_rekey_and_migrate() -> None:
    rekey = cli.build_parser().parse_args(
        [
            "sqlcipher",
            "rekey",
            "--db",
            "hub.db",
            "--old-key",
            "old.key",
            "--new-key",
            "new.key",
        ]
    )
    assert rekey.func is _cmd_rekey
    migrate = cli.build_parser().parse_args(
        [
            "sqlcipher",
            "migrate",
            "--key",
            "k.key",
            "--source",
            "plain.db",
            "--destination",
            "enc.db",
        ]
    )
    assert migrate.func is _cmd_migrate


@pytest.mark.skipif(not sqlcipher_available(), reason="sqlcipher3-binary not installed")
def test_sqlcipher_rekey_cli_rotates_real_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Production ``synapse sqlcipher rekey`` uses PRAGMA rekey on a real store."""
    old = generate_key_file(tmp_path / "old.key")
    new = generate_key_file(tmp_path / "new.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=old)
    claim = TaskClaim(
        task_id="T-REKEY",
        owner="agent/a",
        note="sqlcipher rekey probe",
        claimed_at=1.0,
        lease_expires_at=9999.0,
        status="claimed",
        data_ref="",
        worktree="repo",
        paths=("a.py",),
        epoch=1,
        checkpoint="",
    )
    store.append(EventKind.CLAIM, claim.as_dict(), ts=1.0, durable=True)
    store.close()
    code = cli.main(
        [
            "sqlcipher",
            "rekey",
            "--db",
            str(db),
            "--old-key",
            str(old),
            "--new-key",
            str(new),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "rekeyed" in out.lower()
    with pytest.raises(SqlCipherKeyError):
        EventStore(db, key_file=old)
    opened = EventStore(db, key_file=new)
    try:
        events = list(opened.read_all())
    finally:
        opened.close()
    assert any(event.payload.get("task_id") == "T-REKEY" for event in events)


@pytest.mark.skipif(not sqlcipher_available(), reason="sqlcipher3-binary not installed")
def test_sqlcipher_migrate_cli_copies_real_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = generate_key_file(tmp_path / "hub.key")
    source = tmp_path / "plain.db"
    dest = tmp_path / "enc.db"
    store = EventStore(source)
    store.append(
        EventKind.CLAIM,
        TaskClaim(
            task_id="T-MIG",
            owner="agent/b",
            note="migrate",
            claimed_at=1.0,
            lease_expires_at=9.0,
            status="claimed",
            data_ref="",
            worktree="r",
            paths=(),
            epoch=1,
            checkpoint="",
        ).as_dict(),
        ts=1.0,
        durable=True,
    )
    store.close()
    code = cli.main(
        [
            "sqlcipher",
            "migrate",
            "--key",
            str(key),
            "--source",
            str(source),
            "--destination",
            str(dest),
        ]
    )
    assert code == 0
    assert "migrated" in capsys.readouterr().out.lower()
    enc = EventStore(dest, key_file=key)
    try:
        assert any(e.payload.get("task_id") == "T-MIG" for e in enc.read_all())
    finally:
        enc.close()
