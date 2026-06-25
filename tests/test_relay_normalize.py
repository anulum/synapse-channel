# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the NDJSON relay log and compact wire format

from __future__ import annotations

import pytest

from synapse_channel.relay import (
    normalize_core_command,
)


def test_normalize_requires_kind() -> None:
    with pytest.raises(ValueError, match="Missing command kind"):
        normalize_core_command({})


def test_normalize_rejects_unsupported_kind() -> None:
    with pytest.raises(ValueError, match="Unsupported command kind"):
        normalize_core_command({"k": "explode"})


def test_normalize_chat_and_claim_short_aliases() -> None:
    assert normalize_core_command({"kind": "chat", "payload": " hi ", "target": "USER"}) == {
        "k": "chat",
        "p": "hi",
        "to": "USER",
    }
    assert normalize_core_command({"k": "claim", "task_id": "H1", "note": "x"}) == {
        "k": "claim",
        "id": "H1",
        "n": "x",
    }


def test_normalize_release_and_who_and_state() -> None:
    assert normalize_core_command({"k": "release", "id": "H2"}) == {"k": "release", "id": "H2"}
    assert normalize_core_command({"k": "who"}) == {"k": "who"}
    assert normalize_core_command({"k": "state"}) == {"k": "state"}


def test_normalize_history_variants() -> None:
    assert normalize_core_command({"k": "history", "limit": "999"}) == {"k": "history", "n": 999}
    assert normalize_core_command({"k": "history", "limit": "all"}) == {"k": "history", "n": "all"}
    assert normalize_core_command({"k": "history"}) == {"k": "history", "n": 20}
    assert normalize_core_command({"k": "history", "n": "bad"}) == {"k": "history", "n": 20}
    assert normalize_core_command({"k": "history", "n": -3}) == {"k": "history", "n": 1}


def test_normalize_task_update_full_and_minimal() -> None:
    full = normalize_core_command(
        {"k": "task_update", "task_id": "T", "status": "done", "note": "n", "data_ref": "r"}
    )
    assert full == {"k": "task_update", "id": "T", "status": "done", "note": "n", "data_ref": "r"}

    minimal = normalize_core_command({"k": "task_update", "id": "T", "status": ""})
    assert minimal == {"k": "task_update", "id": "T"}


def test_normalize_resource_with_and_without_meta() -> None:
    with_meta = normalize_core_command(
        {"k": "resource", "kind": "llm", "name": "m", "capacity": 2, "meta": {"vram": "8G"}}
    )
    assert with_meta == {
        "k": "resource",
        "kind": "llm",
        "name": "m",
        "capacity": 2,
        "meta": {"vram": "8G"},
    }

    without_meta = normalize_core_command(
        {"k": "resource_offer", "resource_kind": "fs", "resource_name": "disk"}
    )
    assert without_meta == {
        "k": "resource_offer",
        "kind": "fs",
        "name": "disk",
        "capacity": 1,
    }
