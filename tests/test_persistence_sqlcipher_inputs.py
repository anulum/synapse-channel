# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — SQLCipher driver absence, key inputs and raw-key rekey
"""Report a missing driver, refuse missing keys, and rekey with raw key bytes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from synapse_channel.core.at_rest import KEY_BYTES
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import (
    SQLCIPHER_INSTALL_HINT,
    SqlCipherKeyError,
    SqlCipherUnavailableError,
    import_sqlcipher_module,
    migrate_plaintext_to_sqlcipher,
    rekey_sqlcipher_store,
    sqlcipher_available,
)


def test_absent_drivers_are_reported_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """With neither SQLCipher package importable, availability is False and the hint is given."""
    monkeypatch.setitem(sys.modules, "sqlcipher3", None)
    monkeypatch.setitem(sys.modules, "pysqlcipher3", None)
    assert sqlcipher_available() is False
    with pytest.raises(SqlCipherUnavailableError) as caught:
        import_sqlcipher_module()
    assert str(caught.value) == SQLCIPHER_INSTALL_HINT
    assert isinstance(caught.value.__cause__, ImportError)


def test_rekey_and_migrate_refuse_missing_key_material(tmp_path: Path) -> None:
    """Each side of a rekey, and a migration's destination, needs a key or a key file."""
    store = tmp_path / "hub.db"
    store.write_bytes(b"pages")
    with pytest.raises(
        ValueError, match="^rekey_sqlcipher_store requires old_key or old_key_file$"
    ):
        rekey_sqlcipher_store(store, new_key=b"n" * KEY_BYTES)
    with pytest.raises(
        ValueError, match="^rekey_sqlcipher_store requires new_key or new_key_file$"
    ):
        rekey_sqlcipher_store(store, old_key=b"o" * KEY_BYTES)
    with pytest.raises(
        ValueError, match="^migrate_plaintext_to_sqlcipher requires key or key_file$"
    ):
        migrate_plaintext_to_sqlcipher(store, tmp_path / "encrypted.db")
    assert store.read_bytes() == b"pages"
    assert not (tmp_path / "encrypted.db").exists()


def test_rekey_with_raw_key_bytes_moves_the_store_to_the_new_key(tmp_path: Path) -> None:
    """Raw old and new keys rekey a real encrypted store; only the new key opens it after."""
    pytest.importorskip("sqlcipher3")
    old, new = os.urandom(KEY_BYTES), os.urandom(KEY_BYTES)
    old_file, new_file = tmp_path / "old.key", tmp_path / "new.key"
    for path, material in ((old_file, old), (new_file, new)):
        path.write_bytes(material)
        path.chmod(0o600)
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=old_file)
    store.append("chat", {"text": "kept across the rekey"})
    store.close()

    assert rekey_sqlcipher_store(db, old_key=old, new_key=new) == {
        "path": str(db),
        "status": "rekeyed",
    }
    with pytest.raises(SqlCipherKeyError):
        EventStore(db, key_file=old_file)
    reopened = EventStore(db, key_file=new_file)
    assert reopened.read_all()[0].payload == {"text": "kept across the rekey"}
    reopened.close()
