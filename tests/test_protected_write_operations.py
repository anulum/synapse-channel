# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected-write operation-chain regressions
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import cast

import pytest

from synapse_channel.core.protected_write_json import (
    ProtectedWriteJsonLimits,
    decode_protected_write_json,
)
from synapse_channel.core.protected_write_operations import (
    ProtectedWriteOperationLimits,
    ProtectedWriteOperationsError,
    validate_protected_write_operations,
)

LIMITS = ProtectedWriteOperationLimits(
    128, 100, 4096, frozenset({"records"}), frozenset({"text"}), frozenset({"0600", "0700"})
)


def _document() -> dict[str, object]:
    content = b"source-linked memory\n"
    file = {
        "kind": "file",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "mode": "0600",
    }
    absent = {"kind": "absent"}
    ref = {
        "domain": "text",
        "handle": "content-1",
        "sha256": file["sha256"],
        "size_bytes": len(content),
    }
    document = {
        "operations": [
            {
                "operation_id": "final",
                "root_id": "records",
                "relative_path": "memory/file",
                "action": "create",
                "claim_task_id": "task",
                "before": absent,
                "after_sha256": file["sha256"],
                "after_size_bytes": len(content),
                "mode": "0600",
            }
        ],
        "auxiliary_operations": [
            {
                "operation_id": "stage",
                "opcode": "create",
                "paths": [
                    {
                        "root_id": "records",
                        "relative_path": "memory/stage",
                        "before": absent,
                        "after": file,
                    }
                ],
                "content_reference": ref,
            },
            {
                "operation_id": "replace",
                "opcode": "rename",
                "paths": [
                    {
                        "root_id": "records",
                        "relative_path": "memory/stage",
                        "before": file,
                        "after": absent,
                    },
                    {
                        "root_id": "records",
                        "relative_path": "memory/file",
                        "before": absent,
                        "after": file,
                    },
                ],
                "content_reference": None,
            },
        ],
    }
    return decode_protected_write_json(
        json.dumps(document), limits=ProtectedWriteJsonLimits(16384, 20, 1024, 1024)
    )


def test_staged_create_and_rename_match_intended_bytes() -> None:
    doc = _document()
    validate_protected_write_operations(
        doc["operations"], doc["auxiliary_operations"], limits=LIMITS
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ('"opcode": "rename"', '"opcode": "shell"'),
        ('"relative_path": "memory/file"', '"relative_path": "../file"'),
        ('"root_id": "records"', '"root_id": "foreign"'),
        ('"domain": "text"', '"domain": "foreign"'),
        ('"operation_id": "replace"', '"operation_id": "stage"'),
        ('"mode": "0600"', '"mode": "0777"'),
        ('"action": "create"', '"action": "replace"'),
        ('"opcode": "create"', '"opcode": "write"'),
        ('"claim_task_id": "task"', '"claim_task_id": ""'),
        ('"kind": "absent"', '"kind": "absent", "extra": null'),
    ],
)
def test_invalid_wire_effects_refuse(old: str, new: str) -> None:
    original = json.dumps(_document())
    raw = original.replace(old, new)
    assert raw != original
    doc = decode_protected_write_json(raw, limits=ProtectedWriteJsonLimits(16384, 20, 1024, 1024))
    with pytest.raises(ProtectedWriteOperationsError):
        validate_protected_write_operations(
            doc["operations"], doc["auxiliary_operations"], limits=LIMITS
        )


def test_unexecuted_final_target_refuses() -> None:
    doc = _document()
    with pytest.raises(ProtectedWriteOperationsError):
        validate_protected_write_operations(doc["operations"], [], limits=LIMITS)


def _steps(doc: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], doc["auxiliary_operations"])


def _entry(step: dict[str, object], index: int = 0) -> dict[str, object]:
    return cast(list[dict[str, object]], step["paths"])[index]


def _check(doc: dict[str, object], limits: ProtectedWriteOperationLimits = LIMITS) -> None:
    validate_protected_write_operations(
        doc["operations"], doc["auxiliary_operations"], limits=limits
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_operations", 0),
        ("max_content_bytes", True),
        ("max_identifier_chars", -1),
        ("root_ids", frozenset()),
        ("modes", frozenset({"bad"})),
        ("content_domains", frozenset({"not an id"})),
    ],
)
def test_invalid_enrollment_refuses(field: str, value: object) -> None:
    with pytest.raises(ProtectedWriteOperationsError):
        if field == "max_operations":
            replace(LIMITS, max_operations=cast(int, value))
        elif field == "max_content_bytes":
            replace(LIMITS, max_content_bytes=cast(int, value))
        elif field == "max_identifier_chars":
            replace(LIMITS, max_identifier_chars=cast(int, value))
        elif field == "root_ids":
            replace(LIMITS, root_ids=cast(frozenset[str], value))
        elif field == "modes":
            replace(LIMITS, modes=cast(frozenset[str], value))
        else:
            replace(LIMITS, content_domains=cast(frozenset[str], value))


@pytest.mark.parametrize(
    "mutation",
    [
        "path-count",
        "chain",
        "content-on-rename",
        "final-mismatch",
        "bad-digest",
        "bool-size",
        "nonobject-state",
        "unknown-state",
        "missing-fields",
        "operation-count",
        "reference-size",
    ],
)
def test_inconsistent_effect_proposals_refuse(mutation: str) -> None:
    doc = _document()
    steps = _steps(doc)
    limits = LIMITS
    if mutation == "path-count":
        steps[1]["paths"] = [_entry(steps[1])]
    elif mutation == "chain":
        _entry(steps[1])["before"] = {"kind": "absent"}
    elif mutation == "content-on-rename":
        steps[1]["content_reference"] = steps[0]["content_reference"]
    elif mutation == "final-mismatch":
        doc["auxiliary_operations"] = steps[:1]
    elif mutation == "bad-digest":
        cast(dict[str, object], _entry(steps[0])["after"])["sha256"] = "BAD"
    elif mutation == "bool-size":
        cast(dict[str, object], _entry(steps[0])["after"])["size_bytes"] = True
    elif mutation == "nonobject-state":
        _entry(steps[0])["before"] = None
    elif mutation == "unknown-state":
        _entry(steps[0])["before"] = {"kind": "symlink"}
    elif mutation == "missing-fields":
        del steps[0]["opcode"]
    elif mutation == "operation-count":
        limits = replace(limits, max_operations=2)
    else:
        cast(dict[str, object], steps[0]["content_reference"])["size_bytes"] = 20
    with pytest.raises(ProtectedWriteOperationsError):
        _check(doc, limits)


def _unchanged(identifier: str, opcode: str, path: str, state: object) -> dict[str, object]:
    return {
        "operation_id": identifier,
        "opcode": opcode,
        "paths": [{"root_id": "records", "relative_path": path, "before": state, "after": state}],
        "content_reference": None,
    }


def test_ordered_directory_locks_fsync_write_and_unlink() -> None:
    doc = _document()
    steps = _steps(doc)
    directory = {"kind": "directory", "mode": "0700"}
    mkdir = {
        "operation_id": "mkdir",
        "opcode": "mkdir",
        "paths": [
            {
                "root_id": "records",
                "relative_path": "memory",
                "before": {"kind": "absent"},
                "after": directory,
            }
        ],
        "content_reference": None,
    }
    file = _entry(steps[0])["after"]
    write = _unchanged("write", "write", "memory/file", file)
    write["content_reference"] = steps[0]["content_reference"]
    spare = json.loads(json.dumps(steps[0]))
    spare["operation_id"] = "spare"
    spare["paths"][0]["relative_path"] = "memory/spare"
    unlink = {
        "operation_id": "unlink",
        "opcode": "unlink",
        "paths": [
            {
                "root_id": "records",
                "relative_path": "memory/spare",
                "before": file,
                "after": {"kind": "absent"},
            }
        ],
        "content_reference": None,
    }
    doc["auxiliary_operations"] = [
        mkdir,
        _unchanged("lock", "lock", "memory", directory),
        *steps,
        write,
        spare,
        unlink,
        _unchanged("sync", "fsync", "memory", directory),
        _unchanged("unlock", "unlock", "memory", directory),
    ]
    _check(doc)
    with pytest.raises(ProtectedWriteOperationsError, match="aggregate"):
        _check(doc, replace(LIMITS, max_content_bytes=21))


@pytest.mark.parametrize(
    "operations",
    [
        ("lock",),
        ("unlock",),
        ("lock", "lock"),
        ("lock", "other-lock", "unlock"),
    ],
)
def test_unbalanced_repeated_and_out_of_order_locks_refuse(operations: tuple[str, ...]) -> None:
    doc = _document()
    steps = _steps(doc)
    directory = {"kind": "directory", "mode": "0700"}
    for index, opcode in enumerate(operations):
        other = opcode == "other-lock"
        steps.append(
            _unchanged(
                f"lock-{index}",
                "lock" if other else opcode,
                "other" if other else "memory",
                directory,
            )
        )
    with pytest.raises(ProtectedWriteOperationsError, match="lock"):
        _check(doc)


def test_renaming_a_held_lock_object_refuses_even_when_bytes_match() -> None:
    doc = _document()
    steps = _steps(doc)
    steps.insert(1, _unchanged("lock-stage", "lock", "memory/stage", _entry(steps[0])["after"]))
    with pytest.raises(ProtectedWriteOperationsError, match="held lock object"):
        _check(doc)
