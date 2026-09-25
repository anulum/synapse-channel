# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected-write operation representation
"""Validate exact protected-write effect descriptions without filesystem access.

Validation checks the declared state chain, not actual content, root topology,
claim ownership, permissions, durability or the existence of an execution grant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class ProtectedWriteOperationsError(ValueError):
    """A declared effect or executable operation violates its representation."""


@dataclass(frozen=True)
class ProtectedWriteOperationLimits:
    """Explicit representation budgets and admitted root/domain/mode vocabulary.

    Parameters
    ----------
    max_identifier_chars:
        Upper bound for identifiers, handles and relative paths.
    max_operations:
        Combined upper bound for intended effects and executable operations.
    max_content_bytes:
        Upper bound for individual file/reference sizes and total execution bytes.
    root_ids:
        Names of separately enrolled roots; this value does not enroll a root.
    content_domains:
        Separately enrolled immutable content-validation domains.
    modes:
        Explicit allowed four-digit octal modes.
    """

    max_identifier_chars: int
    max_operations: int
    max_content_bytes: int
    root_ids: frozenset[str]
    content_domains: frozenset[str]
    modes: frozenset[str]

    def __post_init__(self) -> None:
        """Reject disabled budgets and malformed enrollment vocabularies."""
        for limit in (self.max_identifier_chars, self.max_operations, self.max_content_bytes):
            if type(limit) is not int or not 0 < limit <= (1 << 53) - 1:
                raise ProtectedWriteOperationsError("invalid operation budget")
        for vocabulary in (self.root_ids, self.content_domains, self.modes):
            if not isinstance(vocabulary, frozenset) or not vocabulary:
                raise ProtectedWriteOperationsError("explicit immutable vocabulary required")
        for name in self.root_ids | self.content_domains:
            _identifier(name, self)
        for mode in self.modes:
            if not isinstance(mode, str) or re.fullmatch(r"[0-7]{4}", mode) is None:
                raise ProtectedWriteOperationsError("invalid enrolled mode")


def validate_protected_write_operations(
    operations: object, auxiliary_operations: object, *, limits: ProtectedWriteOperationLimits
) -> None:
    """Check declared executable transitions and their intended final effects.

    Parameters
    ----------
    operations:
        Nonempty JSON array of non-executable create/replace final effects.
    auxiliary_operations:
        Nonempty JSON array containing every declared executable operation.
    limits:
        Explicit enrolled vocabulary and representation budgets.

    Raises
    ------
    ProtectedWriteOperationsError
        On unknown fields, inconsistent transitions, duplicate IDs/targets,
        undeclared roots/domains/modes, excess budgets or invalid lock ordering.

    Notes
    -----
    Success is only a representation check. The admission actor and isolated
    writer must independently validate claims, immutable content, enrolled
    descriptors, aliases, parent durability and real before/after bytes.
    """
    primary = _array(operations)
    auxiliary = _array(auxiliary_operations)
    if len(primary) + len(auxiliary) > limits.max_operations:
        raise ProtectedWriteOperationsError("operation count budget exceeded")
    identifiers: set[str] = set()
    intended: dict[tuple[str, str], tuple[dict[str, object], dict[str, object]]] = {}
    for value in primary:
        item = _object(
            value,
            {
                "operation_id",
                "root_id",
                "relative_path",
                "action",
                "claim_task_id",
                "before",
                "after_sha256",
                "after_size_bytes",
                "mode",
            },
        )
        _unique_id(item["operation_id"], identifiers, limits)
        _identifier(item["claim_task_id"], limits)
        path = _path(item, limits)
        before = _state(item["before"], limits)
        expected_kind = {"create": "absent", "replace": "file"}.get(str(item["action"]))
        if before["kind"] != expected_kind or path in intended:
            raise ProtectedWriteOperationsError("invalid or duplicate intended effect")
        after = _state(
            {
                "kind": "file",
                "sha256": item["after_sha256"],
                "size_bytes": item["after_size_bytes"],
                "mode": item["mode"],
            },
            limits,
        )
        intended[path] = before, after

    initial: dict[tuple[str, str], dict[str, object]] = {}
    current: dict[tuple[str, str], dict[str, object]] = {}
    locks: list[tuple[str, str]] = []
    total_bytes = 0
    for value in auxiliary:
        item = _object(value, {"operation_id", "opcode", "paths", "content_reference"})
        _unique_id(item["operation_id"], identifiers, limits)
        opcode = item["opcode"]
        if opcode not in (
            "mkdir",
            "create",
            "write",
            "rename",
            "unlink",
            "lock",
            "unlock",
            "fsync",
        ):
            raise ProtectedWriteOperationsError("unknown executable operation")
        paths = _array(item["paths"])
        if len(paths) != (2 if opcode == "rename" else 1):
            raise ProtectedWriteOperationsError("wrong executable path count")
        transitions: list[tuple[tuple[str, str], dict[str, object], dict[str, object]]] = []
        for value_path in paths:
            entry = _object(value_path, {"root_id", "relative_path", "before", "after"})
            path = _path(entry, limits)
            before, after = _state(entry["before"], limits), _state(entry["after"], limits)
            if path in current and current[path] != before:
                raise ProtectedWriteOperationsError("broken executable state chain")
            initial.setdefault(path, before)
            transitions.append((path, before, after))
        _transition(str(opcode), transitions, locks)
        reference = item["content_reference"]
        if opcode in ("create", "write"):
            size = validate_protected_write_content_reference(reference, limits=limits)
            if size != transitions[0][2]["size_bytes"]:
                raise ProtectedWriteOperationsError("content reference is not compatible")
            total_bytes += size
            if total_bytes > limits.max_content_bytes:
                raise ProtectedWriteOperationsError("aggregate content budget exceeded")
        elif reference is not None:
            raise ProtectedWriteOperationsError("unexpected content reference")
        for path, _, after in transitions:
            current[path] = after
    if locks:
        raise ProtectedWriteOperationsError("successful execution leaves a lock held")
    for path, (before, after) in intended.items():
        if initial.get(path) != before or current.get(path) != after:
            raise ProtectedWriteOperationsError("intended effect differs from execution")


def validate_protected_write_content_reference(
    reference: object, *, limits: ProtectedWriteOperationLimits
) -> int:
    """Check an immutable content reference's fields without resolving its handle.

    Parameters
    ----------
    reference:
        Exact domain, handle, sha256 and size_bytes JSON object.
    limits:
        Enrolled domain vocabulary and representation budgets.

    Returns
    -------
    int
        Declared byte size; not evidence of stored bytes or their integrity.

    Raises
    ------
    ProtectedWriteOperationsError
        For unknown fields/domain, malformed IDs/digest or invalid byte size.
    """
    ref = _object(reference, {"domain", "handle", "sha256", "size_bytes"})
    domain = _identifier(ref["domain"], limits)
    _identifier(ref["handle"], limits)
    _digest(ref["sha256"])
    size = _size(ref["size_bytes"], limits)
    if domain not in limits.content_domains:
        raise ProtectedWriteOperationsError("content reference domain is not enrolled")
    return size


def _transition(
    opcode: str,
    entries: list[tuple[tuple[str, str], dict[str, object], dict[str, object]]],
    locks: list[tuple[str, str]],
) -> None:
    path, before, after = entries[0]
    if opcode in ("rename", "unlink") and any(entry[0] in locks for entry in entries):
        raise ProtectedWriteOperationsError("cannot replace or remove a held lock object")
    kinds = before["kind"], after["kind"]
    valid = False
    if opcode in ("mkdir", "create", "write", "unlink"):
        valid = (
            kinds
            == {
                "mkdir": ("absent", "directory"),
                "create": ("absent", "file"),
                "write": ("file", "file"),
                "unlink": ("file", "absent"),
            }[opcode]
        )
        if opcode == "write":
            valid = valid and before.get("mode") == after.get("mode")
    elif opcode == "rename":
        target, target_before, target_after = entries[1]
        valid = (
            path != target
            and kinds == ("file", "absent")
            and target_before["kind"] in ("absent", "file")
            and target_after == before
        )
    else:
        valid = before == after and before["kind"] in ("file", "directory")
        if opcode == "lock":
            valid = valid and path not in locks
            locks.append(path)
        elif opcode == "unlock":
            valid = valid and bool(locks) and locks[-1] == path
            if valid:
                locks.pop()
    if not valid:
        raise ProtectedWriteOperationsError("invalid executable transition or lock order")


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ProtectedWriteOperationsError("incorrect object fields")
    return dict(value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list) or not value:
        raise ProtectedWriteOperationsError("nonempty operation array required")
    return list(value)


def _identifier(value: object, limits: ProtectedWriteOperationLimits) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limits.max_identifier_chars
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value) is None
    ):
        raise ProtectedWriteOperationsError("invalid operation identifier")
    return value


def _unique_id(value: object, seen: set[str], limits: ProtectedWriteOperationLimits) -> None:
    identifier = _identifier(value, limits)
    if identifier in seen:
        raise ProtectedWriteOperationsError("duplicate operation identifier")
    seen.add(identifier)


def _path(item: dict[str, object], limits: ProtectedWriteOperationLimits) -> tuple[str, str]:
    root = _identifier(item["root_id"], limits)
    relative = item["relative_path"]
    if (
        root not in limits.root_ids
        or not isinstance(relative, str)
        or not relative
        or len(relative) > limits.max_identifier_chars
        or any(part in ("", ".", "..") for part in relative.split("/"))
        or any(ord(c) < 32 or ord(c) == 127 or c in "\\*?[]" for c in relative)
    ):
        raise ProtectedWriteOperationsError("invalid enrolled operation path")
    return root, relative


def _digest(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ProtectedWriteOperationsError("invalid file or content digest")


def _size(value: object, limits: ProtectedWriteOperationLimits) -> int:
    if type(value) is not int or not 0 <= value <= min(limits.max_content_bytes, (1 << 53) - 1):
        raise ProtectedWriteOperationsError("invalid content size")
    return value


def _state(value: object, limits: ProtectedWriteOperationLimits) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProtectedWriteOperationsError("invalid path state")
    kind = value.get("kind")
    if kind == "absent":
        return _object(value, {"kind"})
    if kind == "directory":
        result = _object(value, {"kind", "mode"})
    elif kind == "file":
        result = _object(value, {"kind", "mode", "sha256", "size_bytes"})
        _digest(result["sha256"])
        _size(result["size_bytes"], limits)
    else:
        raise ProtectedWriteOperationsError("unknown path state")
    if not isinstance(result["mode"], str) or result["mode"] not in limits.modes:
        raise ProtectedWriteOperationsError("unenrolled path mode")
    return result
