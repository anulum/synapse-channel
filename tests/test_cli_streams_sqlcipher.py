# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — ingest/compact open encrypted event stores

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available

pytestmark = pytest.mark.skipif(
    not sqlcipher_available(),
    reason="sqlcipher3-binary not installed",
)


def test_ingest_reads_encrypted_event_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    store = EventStore(db, key_file=key)
    store.append("chat", {"text": "enc-ingest"})
    store.close()

    code = cli.main(["ingest", str(db), "--db-key-file", str(key)])
    assert code == 0
    out = capsys.readouterr().out.strip()
    row = json.loads(out.splitlines()[0])
    assert row["payload"]["text"] == "enc-ingest"


def test_ingest_without_key_fails_closed_on_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = generate_key_file(tmp_path / "hub.key")
    db = tmp_path / "hub.db"
    EventStore(db, key_file=key).close()
    code = cli.main(["ingest", str(db)])
    assert code == 2
    err = capsys.readouterr().err.lower()
    assert "cannot open" in err or "key" in err or "sqlcipher" in err or "database" in err
