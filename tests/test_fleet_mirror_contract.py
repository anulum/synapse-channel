# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — versioned mirror contract regression tests
"""Check the public versioned JSON parser at its encoded input boundary."""

import json
from typing import Any

import pytest

from synapse_channel.fleet_mirror_contract import MirrorVersionError, parse_mirror


def document() -> dict[str, Any]:
    """Return one minimal complete version-one exported observation."""
    return {
        "version": 1,
        "source_id": "lab",
        "exported_at": 12.0,
        "advisory": True,
        "snapshot": {
            "generated_at": 11.0,
            "advisory": "observed views are advisory",
            "progress_notes": 0,
            "peers": [
                {
                    "peer_id": "hub-b",
                    "cursor": 1,
                    "events": 1,
                    "last_success_at": None,
                    "consecutive_failures": None,
                    "status_written_at": None,
                    "caught_up": None,
                    "budget_exhausted_reason": None,
                }
            ],
            "tasks": [
                {
                    "task_id": "task",
                    "status": "open",
                    "title": "title",
                    "claimed_by": None,
                    "claim_hub": None,
                    "board_provenance": None,
                    "board_conflict": None,
                }
            ],
        },
    }


def test_roundtrip_preserves_nullable_and_conflict_evidence() -> None:
    doc = document()
    doc["snapshot"]["tasks"][0]["board_conflict"] = {"contenders": [{"hub_id": "hub-b"}]}
    assert parse_mirror(json.dumps(doc)) == doc


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "{}",
        '{"version":1,"version":1}',
        "null",
        '{"version":1,"source_id":"lab","advisory":true,"exported_at":NaN,"snapshot":{}}',
        "[" * 2000 + "]" * 2000,
        '"' + "x" * (4 * 1024 * 1024) + '"',
    ],
)
def test_reject_invalid_encoded_documents(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_mirror(raw)


@pytest.mark.parametrize("version", [True, 2, "1"])
def test_reject_unknown_versions(version: object) -> None:
    doc = document()
    doc["version"] = version
    with pytest.raises(MirrorVersionError):
        parse_mirror(json.dumps(doc))


@pytest.mark.parametrize(
    "key,value",
    [
        ("source_id", "../lab"),
        ("source_id", 4),
        ("advisory", False),
        ("exported_at", -1),
        ("exported_at", True),
        ("snapshot", []),
    ],
)
def test_reject_invalid_envelope(key: str, value: object) -> None:
    doc = document()
    doc[key] = value
    with pytest.raises(ValueError):
        parse_mirror(json.dumps(doc))


@pytest.mark.parametrize(
    "key,value",
    [
        ("advisory", None),
        ("generated_at", "now"),
        ("progress_notes", True),
        ("peers", {}),
        ("tasks", [None]),
        ("peers", [{}]),
    ],
)
def test_reject_invalid_snapshot(key: str, value: object) -> None:
    doc = document()
    doc["snapshot"][key] = value
    with pytest.raises(ValueError):
        parse_mirror(json.dumps(doc))


@pytest.mark.parametrize(
    "key,value",
    [
        ("peer_id", ""),
        ("cursor", True),
        ("events", -1),
        ("consecutive_failures", 1.5),
        ("last_success_at", "never"),
        ("caught_up", 1),
        ("budget_exhausted_reason", "unknown"),
    ],
)
def test_reject_invalid_peer(key: str, value: object) -> None:
    doc = document()
    doc["snapshot"]["peers"][0][key] = value
    with pytest.raises(ValueError):
        parse_mirror(json.dumps(doc))


@pytest.mark.parametrize(
    "key,value",
    [
        ("status", 4),
        ("title", None),
        ("claimed_by", 4),
        ("claim_hub", []),
        ("board_provenance", []),
        ("board_conflict", "resolved"),
    ],
)
def test_reject_invalid_task(key: str, value: object) -> None:
    doc = document()
    doc["snapshot"]["tasks"][0][key] = value
    with pytest.raises(ValueError):
        parse_mirror(json.dumps(doc))


def test_missing_fields_duplicate_ids_and_row_limits() -> None:
    for section, field in (("peers", "cursor"), ("tasks", "status")):
        doc = document()
        del doc["snapshot"][section][0][field]
        with pytest.raises(ValueError):
            parse_mirror(json.dumps(doc))
        doc = document()
        doc["snapshot"][section] *= 2
        with pytest.raises(ValueError):
            parse_mirror(json.dumps(doc))
    doc = document()
    doc["snapshot"]["peers"] *= 4097
    with pytest.raises(ValueError):
        parse_mirror(json.dumps(doc))
