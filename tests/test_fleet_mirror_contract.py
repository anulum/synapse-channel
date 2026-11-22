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


def writer_generation() -> dict[str, Any]:
    """Return one valid version-one daemon writer generation record."""
    return {
        "version": 1,
        "device": 42,
        "inode": 99,
        "marker": "0123456789abcdef0123456789abcdef",
    }


def document_with_writer_generation() -> dict[str, Any]:
    """Return one complete version-one daemon export carrying matching writer_generation."""
    doc = document()
    gen = writer_generation()
    doc["writer_generation"] = dict(gen)
    doc["snapshot"]["writer_generation"] = dict(gen)
    return doc


def test_roundtrip_preserves_matching_writer_generation_and_uint64_bounds() -> None:
    doc = document_with_writer_generation()
    assert parse_mirror(json.dumps(doc)) == doc
    for device, inode in ((0, 0), (2**64 - 1, 2**64 - 1)):
        bounded = document_with_writer_generation()
        bounded["writer_generation"]["device"] = device
        bounded["writer_generation"]["inode"] = inode
        bounded["snapshot"]["writer_generation"]["device"] = device
        bounded["snapshot"]["writer_generation"]["inode"] = inode
        assert parse_mirror(json.dumps(bounded)) == bounded


@pytest.mark.parametrize("with_generation", [False, True])
def test_reject_unknown_extra_key_at_envelope_and_snapshot(with_generation: bool) -> None:
    base = document_with_writer_generation() if with_generation else document()
    envelope_extra = json.loads(json.dumps(base))
    envelope_extra["unexpected"] = 1
    with pytest.raises(ValueError, match="invalid mirror envelope"):
        parse_mirror(json.dumps(envelope_extra))

    snapshot_extra = json.loads(json.dumps(base))
    snapshot_extra["snapshot"]["unexpected"] = 1
    with pytest.raises(ValueError, match="invalid mirror snapshot"):
        parse_mirror(json.dumps(snapshot_extra))


@pytest.mark.parametrize("missing_from", ["envelope", "snapshot"])
def test_reject_writer_generation_present_at_only_one_level(missing_from: str) -> None:
    doc = document_with_writer_generation()
    if missing_from == "envelope":
        del doc["writer_generation"]
    else:
        del doc["snapshot"]["writer_generation"]
    with pytest.raises(ValueError, match="both envelope and snapshot"):
        parse_mirror(json.dumps(doc))


@pytest.mark.parametrize(
    "field,other",
    [
        ("device", 43),
        ("inode", 100),
        ("marker", "fedcba9876543210fedcba9876543210"),
    ],
)
def test_reject_unequal_writer_generation_between_envelope_and_snapshot(
    field: str, other: object
) -> None:
    doc = document_with_writer_generation()
    doc["snapshot"]["writer_generation"][field] = other
    with pytest.raises(ValueError, match="disagrees"):
        parse_mirror(json.dumps(doc))


@pytest.mark.parametrize(
    "bad_generation",
    [
        None,
        [],
        "not-a-dict",
        {"version": 1, "device": 42, "inode": 99},
        {
            "version": 1,
            "device": 42,
            "inode": 99,
            "marker": "0123456789abcdef0123456789abcdef",
            "extra": True,
        },
        {**writer_generation(), "version": 2},
        {**writer_generation(), "version": True},
        {**writer_generation(), "version": "1"},
        {**writer_generation(), "version": 1.0},
        {**writer_generation(), "device": -1},
        {**writer_generation(), "device": True},
        {**writer_generation(), "device": 42.0},
        {**writer_generation(), "device": "42"},
        {**writer_generation(), "device": 2**64},
        {**writer_generation(), "inode": -1},
        {**writer_generation(), "inode": False},
        {**writer_generation(), "inode": 99.0},
        {**writer_generation(), "inode": "99"},
        {**writer_generation(), "inode": 2**64},
        {**writer_generation(), "marker": ""},
        {**writer_generation(), "marker": "0123456789abcdef0123456789abcde"},
        {**writer_generation(), "marker": "0123456789abcdef0123456789abcdef0"},
        {**writer_generation(), "marker": "0123456789ABCDEF0123456789ABCDEF"},
        {**writer_generation(), "marker": "0123456789abcdef0123456789abcdeg"},
        {**writer_generation(), "marker": "0123456789abcdef0123456789abcdef\n"},
        {**writer_generation(), "marker": 12345},
    ],
)
def test_reject_invalid_writer_generation_fields(bad_generation: object) -> None:
    doc = document()
    doc["writer_generation"] = bad_generation
    doc["snapshot"]["writer_generation"] = bad_generation
    with pytest.raises(ValueError, match="writer generation"):
        parse_mirror(json.dumps(doc))


def test_reject_valid_envelope_generation_with_invalid_snapshot_generation() -> None:
    doc = document_with_writer_generation()
    doc["snapshot"]["writer_generation"]["marker"] = "invalid"
    with pytest.raises(ValueError, match="writer generation marker"):
        parse_mirror(json.dumps(doc))
