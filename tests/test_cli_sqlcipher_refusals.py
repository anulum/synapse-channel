# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — synapse sqlcipher rekey/migrate refusals exit 1 with the cause on stderr
"""Every refused rekey or migrate exits 1, names its cause and leaves the stores unchanged."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available

requires_sqlcipher = pytest.mark.skipif(
    not sqlcipher_available(), reason="sqlcipher3-binary not installed"
)

_EXTRA = (
    "SQLCipher support requires the optional sqlcipher extra: "
    "pip install 'synapse-channel[sqlcipher]' (or sqlcipher3-binary)"
)


def _encrypted_store(tmp_path: Path) -> tuple[Path, Path]:
    key = generate_key_file(tmp_path / "old.key")
    db = tmp_path / "hub.db"
    EventStore(db, key_file=key).close()
    return db, key


def _refused(capsys: pytest.CaptureFixture[str], argv: list[str]) -> str:
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    return captured.err


@requires_sqlcipher
def test_rekey_refusals_name_the_cause(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A wrong old key, an unchanged key and a missing store are each refused."""
    db, old = _encrypted_store(tmp_path)
    new = generate_key_file(tmp_path / "new.key")
    wrong = generate_key_file(tmp_path / "wrong.key")
    before = db.read_bytes()
    missing = tmp_path / "missing.db"
    cases = [
        (db, wrong, new, "SQLCipher key rejected (wrong key or not an encrypted store)"),
        (db, old, old, "old and new SQLCipher keys must differ"),
        (missing, old, new, f"encrypted event store not found: {missing}"),
    ]
    for store, old_key, new_key, reason in cases:
        argv = ["sqlcipher", "rekey", "--db", str(store)]
        argv += ["--old-key", str(old_key), "--new-key", str(new_key)]
        assert _refused(capsys, argv) == f"synapse sqlcipher rekey: {reason}\n"
    assert db.read_bytes() == before
    assert not missing.exists()
    EventStore(db, key_file=old).close()  # the original key still opens the store


@requires_sqlcipher
def test_migrate_refusals_name_the_cause(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An existing destination is never overwritten; a missing source is named."""
    destination, _ = _encrypted_store(tmp_path)
    key = generate_key_file(tmp_path / "new.key")
    source = tmp_path / "plain.db"
    EventStore(source).close()
    before = destination.read_bytes()
    argv = ["sqlcipher", "migrate", "--key", str(key), "--source", str(source)]
    assert _refused(capsys, [*argv, "--destination", str(destination)]) == (
        f"synapse sqlcipher migrate: encrypted destination already exists: {destination}\n"
    )
    assert destination.read_bytes() == before
    absent = tmp_path / "absent.db"
    fresh = tmp_path / "fresh.db"
    assert _refused(
        capsys,
        ["sqlcipher", "migrate", "--key", str(key), "--source", str(absent)]
        + ["--destination", str(fresh)],
    ) == (f"synapse sqlcipher migrate: plaintext event store not found: {absent}\n")
    assert not fresh.exists()


def test_both_commands_name_the_extra_when_the_driver_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With existing inputs but no SQLCipher driver, both commands name the extra."""
    key = generate_key_file(tmp_path / "hub.key")
    new_key = generate_key_file(tmp_path / "new.key")
    store = tmp_path / "hub.db"
    EventStore(store).close()
    before = sorted((path.name, path.read_bytes()) for path in tmp_path.iterdir())
    monkeypatch.setitem(sys.modules, "sqlcipher3", None)
    rekey = ["sqlcipher", "rekey", "--db", str(store)]
    rekey += ["--old-key", str(key), "--new-key", str(new_key)]
    assert _refused(capsys, rekey) == f"synapse sqlcipher rekey: {_EXTRA}\n"
    migrate = ["sqlcipher", "migrate", "--key", str(key)]
    migrate += ["--source", str(store), "--destination", str(tmp_path / "e.db")]
    assert _refused(capsys, migrate) == f"synapse sqlcipher migrate: {_EXTRA}\n"
    assert sorted((path.name, path.read_bytes()) for path in tmp_path.iterdir()) == before
