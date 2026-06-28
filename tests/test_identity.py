# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the agent identity inventory

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.identity import Identity, IdentityError, IdentityInventory


def _write(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_identity_audit_subject_and_serialisation() -> None:
    identity = Identity(agent_id="claude-e57b", project="SYNAPSE-CHANNEL", credential_id="k1")
    assert identity.audit_subject == "SYNAPSE-CHANNEL/claude-e57b"
    payload = identity.as_dict()
    assert payload["audit_subject"] == "SYNAPSE-CHANNEL/claude-e57b"
    assert json.loads(json.dumps(payload)) == payload


def test_inventory_loads_and_lists_subjects(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "ids.json",
        [
            {"agent_id": "b", "project": "P", "credential_id": "k"},
            {"agent_id": "a", "project": "P", "credential_id": "k"},
        ],
    )
    inventory = IdentityInventory.from_file(path)
    assert inventory.subjects() == ["P/a", "P/b"]
    assert len(inventory.identities()) == 2


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("{}", "must contain a JSON list"),
        ("[1, 2]", "must be an object"),
        ('[{"agent_id": "", "project": "P"}]', "non-empty agent_id and project"),
        ('[{"agent_id": "a"}]', "non-empty agent_id and project"),
        ("{not json", "invalid identity JSON"),
    ],
)
def test_inventory_rejects_malformed_files(tmp_path: Path, content: str, match: str) -> None:
    path = tmp_path / "ids.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(IdentityError, match=match):
        IdentityInventory.from_file(path)


def test_inventory_missing_file(tmp_path: Path) -> None:
    with pytest.raises(IdentityError, match="does not exist"):
        IdentityInventory.from_file(tmp_path / "absent.json")


def test_audit_flags_duplicate_subjects(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "ids.json",
        [
            {"agent_id": "a", "project": "P", "credential_id": "k1"},
            {"agent_id": "a", "project": "P", "credential_id": "k2"},
        ],
    )
    findings = IdentityInventory.from_file(path).audit()
    duplicates = [f for f in findings if "duplicate" in f.message]
    assert duplicates
    assert duplicates[0].severity == "fail"
    assert duplicates[0].subject == "P/a"


def test_audit_warns_on_missing_credential(tmp_path: Path) -> None:
    path = _write(tmp_path / "ids.json", [{"agent_id": "a", "project": "P"}])
    findings = IdentityInventory.from_file(path).audit()
    assert any(f.severity == "warn" and "no credential" in f.message for f in findings)
    assert findings[0].as_dict()["subject"] == "P/a"


def test_audit_warns_on_shared_seat(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "ids.json",
        [
            {"agent_id": "a", "project": "P", "credential_id": "k", "seat_id": "seat-1"},
            {"agent_id": "b", "project": "P", "credential_id": "k", "seat_id": "seat-1"},
        ],
    )
    findings = IdentityInventory.from_file(path).audit()
    assert any("seat runs 2 agent ids" in f.message for f in findings)


def test_clean_inventory_has_no_findings(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "ids.json",
        [
            {"agent_id": "a", "project": "P", "credential_id": "k1", "seat_id": "s1"},
            {"agent_id": "b", "project": "P", "credential_id": "k2", "seat_id": "s2"},
        ],
    )
    assert IdentityInventory.from_file(path).audit() == []
