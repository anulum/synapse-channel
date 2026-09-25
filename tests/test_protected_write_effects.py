# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parent-bound recovery admission
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, cast

import pytest

from synapse_channel.core.protected_write_effects import verify_protected_effects
from test_protected_write_proposal import LIMITS, _proposal


def _plan() -> dict[str, Any]:
    proposal = cast(dict[str, Any], _proposal())
    path = proposal["auxiliary_operations"][0]["paths"][0]
    proposal["auxiliary_operations"].extend(
        [
            {
                "operation_id": "flush-file",
                "opcode": "fsync",
                "content_reference": None,
                "paths": [{**path, "before": path["after"]}],
            },
            {
                "operation_id": "flush-parent",
                "opcode": "fsync",
                "content_reference": None,
                "paths": [
                    {
                        "root_id": "memory",
                        "relative_path": "records",
                        "before": {"kind": "directory", "mode": "0700"},
                        "after": {"kind": "directory", "mode": "0700"},
                    }
                ],
            },
        ]
    )
    return proposal


def _verify(proposal: dict[str, Any], **overrides: Any) -> str:
    options: dict[str, Any] = {
        "primary_claims": {("memory", "records/note.md"): frozenset({"task-1"})},
        "auxiliary_opcodes": {
            ("memory", "records/note.md"): frozenset({"create", "write", "rename", "fsync"}),
            ("memory", "records/staged"): frozenset({"create", "rename", "unlink", "fsync"}),
            ("memory", "records"): frozenset({"fsync"}),
        },
        "enrolled_parents": {
            ("memory", "records/note.md"): ("memory", "records"),
            ("memory", "records/staged"): ("memory", "records"),
        },
    }
    options.update(overrides)
    return verify_protected_effects(json.dumps(proposal), limits=LIMITS, **options)


def test_exact_effect_permissions_and_success_durability() -> None:
    assert len(_verify(_plan())) == 64


def test_enrolled_lock_lifetime_does_not_replace_file_or_parent_fsync() -> None:
    proposal = _plan()
    path = proposal["auxiliary_operations"][1]["paths"][0]
    lock = {
        "operation_id": "lock-file",
        "opcode": "lock",
        "content_reference": None,
        "paths": [deepcopy(path)],
    }
    unlock = {**lock, "operation_id": "unlock-file", "opcode": "unlock"}
    proposal["auxiliary_operations"].insert(1, lock)
    proposal["auxiliary_operations"].insert(3, unlock)
    assert (
        len(
            _verify(
                proposal,
                auxiliary_opcodes={
                    ("memory", "records/note.md"): frozenset({"create", "lock", "unlock", "fsync"}),
                    ("memory", "records"): frozenset({"fsync"}),
                },
            )
        )
        == 64
    )


@pytest.mark.parametrize(
    "case",
    [
        "primary",
        "mutable-primary",
        "opcode",
        "mutable-opcode",
        "parent",
        "file-fsync",
        "parent-fsync",
        "early-parent-fsync",
    ],
)
def test_missing_or_misordered_effect_permissions_fail_closed(case: str) -> None:
    proposal = _plan()
    options: dict[str, Any] = {}
    if case == "primary":
        options["primary_claims"] = {("memory", "records"): frozenset({"task-1"})}
    elif case == "mutable-primary":
        options["primary_claims"] = {("memory", "records/note.md"): {"task-1"}}
    elif case == "opcode":
        options["auxiliary_opcodes"] = {}
    elif case == "mutable-opcode":
        options["auxiliary_opcodes"] = {("memory", "records/note.md"): {"create", "fsync"}}
    elif case == "parent":
        options["enrolled_parents"] = {}
    elif case == "file-fsync":
        del proposal["auxiliary_operations"][1]
    elif case == "parent-fsync":
        proposal["auxiliary_operations"].pop()
    else:
        proposal["auxiliary_operations"].insert(0, proposal["auxiliary_operations"].pop())
    with pytest.raises(ValueError):
        _verify(proposal, **options)


@pytest.mark.parametrize("flush_source", [True, False])
def test_staged_rename_requires_file_flush_before_publication(flush_source: bool) -> None:
    proposal = _plan()
    create, flush, parent_flush = proposal["auxiliary_operations"]
    create["paths"][0]["relative_path"] = "records/staged"
    flush["paths"][0]["relative_path"] = "records/staged"
    file = create["paths"][0]["after"]
    rename = {
        "operation_id": "publish",
        "opcode": "rename",
        "content_reference": None,
        "paths": [
            {
                "root_id": "memory",
                "relative_path": "records/staged",
                "before": file,
                "after": {"kind": "absent"},
            },
            {
                "root_id": "memory",
                "relative_path": "records/note.md",
                "before": {"kind": "absent"},
                "after": file,
            },
        ],
    }
    proposal["auxiliary_operations"] = (
        [create] + ([flush] if flush_source else []) + [rename, parent_flush]
    )
    if flush_source:
        assert len(_verify(proposal)) == 64
    else:
        with pytest.raises(ValueError, match="before publication"):
            _verify(proposal)


def test_declared_success_only_discard_does_not_require_persisting_discarded_bytes() -> None:
    proposal = _plan()
    extra = deepcopy(proposal["auxiliary_operations"][0])
    extra["operation_id"] = "create-staged"
    extra["paths"][0]["relative_path"] = "records/staged"
    remove = {
        "operation_id": "discard-staged",
        "opcode": "unlink",
        "content_reference": None,
        "paths": [
            {**extra["paths"][0], "before": extra["paths"][0]["after"], "after": {"kind": "absent"}}
        ],
    }
    proposal["auxiliary_operations"][2:2] = [extra, remove]
    assert len(_verify(proposal)) == 64
