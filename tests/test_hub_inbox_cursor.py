# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable inbox cursor filesystem contracts
"""Check public cursor persistence against real files and invalid state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.hub_inbox_cursor import (
    HubInboxCursor,
    hub_inbox_cursor_path,
    load_hub_inbox_cursor,
    save_hub_inbox_cursor,
)


def test_sources_and_identities_have_independent_owner_only_cursors(tmp_path: Path) -> None:
    first = hub_inbox_cursor_path(tmp_path, "ws://localhost:8876", "P/reader")
    other_uri = hub_inbox_cursor_path(tmp_path, "wss://remote:8876", "P/reader")
    other_identity = hub_inbox_cursor_path(tmp_path, "ws://localhost:8876", "P/other")
    assert len({first, other_uri, other_identity}) == 3
    assert load_hub_inbox_cursor(first) == HubInboxCursor()
    save_hub_inbox_cursor(first, HubInboxCursor("hub", 17))
    assert load_hub_inbox_cursor(first) == HubInboxCursor("hub", 17)
    assert load_hub_inbox_cursor(other_uri) == HubInboxCursor()
    assert load_hub_inbox_cursor(other_identity) == HubInboxCursor()
    save_hub_inbox_cursor(first, HubInboxCursor("hub", 18))
    assert load_hub_inbox_cursor(first).seq == 18
    assert first.stat().st_mode & 0o777 == 0o600
    assert list(first.parent.iterdir()) == [first]
    assert "P/reader" not in first.name


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost",
        "ws://",
        "ws://user@localhost",
        "ws://user:secret@localhost",
        "ws://localhost?token=secret",
        "ws://localhost#fragment",
        "ws://[",
    ],
)
def test_inline_credentials_and_invalid_sources_refuse(tmp_path: Path, uri: str) -> None:
    with pytest.raises(ValueError):
        hub_inbox_cursor_path(tmp_path, uri, "P/reader")
    assert not list(tmp_path.iterdir())


def test_blank_identity_refuses(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        hub_inbox_cursor_path(tmp_path, "ws://localhost", " ")


@pytest.mark.parametrize(
    "document",
    [
        [],
        {},
        {"version": True},
        {"version": 2},
        {"version": 1, "hub_id": "", "seq": 0},
        {"version": 1, "hub_id": 5, "seq": 0},
        {"version": 1, "hub_id": "hub", "seq": True},
        {"version": 1, "hub_id": "hub", "seq": -1},
        {"version": 1, "hub_id": "hub", "seq": 2**63},
    ],
)
def test_corrupt_existing_state_never_resets_to_zero(tmp_path: Path, document: Any) -> None:
    path = tmp_path / "cursor"
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        load_hub_inbox_cursor(path)
    assert path.read_bytes() == before


def test_malformed_json_and_unreadable_path_refuse(tmp_path: Path) -> None:
    path = tmp_path / "cursor"
    path.write_text("{bad")
    with pytest.raises(ValueError):
        load_hub_inbox_cursor(path)
    with pytest.raises(OSError):
        load_hub_inbox_cursor(tmp_path)


@pytest.mark.parametrize(
    "cursor",
    [
        HubInboxCursor("", 0),
        HubInboxCursor("hub", -1),
        HubInboxCursor("hub", True),
        HubInboxCursor("hub", 2**63),
    ],
)
def test_invalid_writes_preserve_last_durable_cursor(
    tmp_path: Path, cursor: HubInboxCursor
) -> None:
    path = tmp_path / "cursor"
    save_hub_inbox_cursor(path, HubInboxCursor("hub", 17))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        save_hub_inbox_cursor(path, cursor)
    assert path.read_bytes() == before


def test_write_failure_does_not_leave_temporary_files(tmp_path: Path) -> None:
    path = tmp_path / "cursor"
    path.mkdir()
    with pytest.raises(OSError):
        save_hub_inbox_cursor(path, HubInboxCursor("hub", 17))
    assert list(tmp_path.iterdir()) == [path]
