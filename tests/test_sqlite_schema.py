# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — SQLite schema text and digest tests
"""The schema text is the stored SQL in a fixed order, and its digest is what the CLI prints."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from cli_e2e_helpers import run_cli
from synapse_channel.core.sqlite_schema import schema_digest, schema_text


def test_the_text_orders_stored_sql_by_type_then_name_and_skips_internal_tables(
    tmp_path: Path,
) -> None:
    database = tmp_path / "store.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE zebra (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        connection.execute("CREATE TABLE apple (id INTEGER PRIMARY KEY, name TEXT UNIQUE)")
        connection.execute("CREATE INDEX apple_by_name ON apple(name)")
        connection.commit()

        text = schema_text(connection)
        digest = schema_digest(connection)

    assert text == (
        "index\tapple_by_name\nCREATE INDEX apple_by_name ON apple(name)\n"
        "table\tapple\nCREATE TABLE apple (id INTEGER PRIMARY KEY, name TEXT UNIQUE)\n"
        "table\tzebra\nCREATE TABLE zebra (id INTEGER PRIMARY KEY AUTOINCREMENT)\n"
    )
    assert "sqlite_" not in text
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_verify_result_command_prints_the_same_digest(tmp_path: Path) -> None:
    database = tmp_path / "store.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE readings (id INTEGER PRIMARY KEY)")
        connection.commit()
        expected = schema_digest(connection)

    completed = run_cli(
        "verify-result", "migration", "--database", str(database), "--print-schema-sha256"
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == expected
