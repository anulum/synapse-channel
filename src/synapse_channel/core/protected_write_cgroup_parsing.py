# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded kernel cgroup byte parsing
"""Parse bounded kernel cgroup bytes without certifying their origin or custody."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectedCgroupEvents:
    """Parsed recursive population/freeze bits, not a recovery permit."""

    populated: bool
    frozen: bool


def parse_protected_cgroup_events(raw: bytes) -> ProtectedCgroupEvents:
    """Parse a bounded cgroup.events snapshot with unique required state fields.

    Parameters
    ----------
    raw:
        Original event bytes, bounded to 4096 bytes. Unknown kernel fields may be
        retained by callers; populated/frozen each require an exact binary value.

    Returns
    -------
    ProtectedCgroupEvents
        Parsed required bits, independent of filesystem or process identity.

    Raises
    ------
    ValueError
        On invalid budget/type, encoding, duplicate/malformed fields or missing bits.

    Notes
    -----
    Parsing proves representation only. Descriptor-backed kernel capture,
    independent enrollment and physical custody are required before any authority
    decision. Caller-supplied bytes cannot establish a stopped writer.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= 4096:
        raise ValueError("writer subtree events exceed read budget")
    values: dict[str, str] = {}
    for line in raw.decode("ascii").splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] in values:
            raise ValueError("invalid writer subtree kernel events")
        values[fields[0]] = fields[1]
    if any(values.get(name) not in {"0", "1"} for name in ("populated", "frozen")):
        raise ValueError("incomplete writer subtree kernel events")
    return ProtectedCgroupEvents(values["populated"] == "1", values["frozen"] == "1")


def parse_protected_cgroup_type(raw: bytes) -> str:
    """Parse one bounded nonroot cgroup.type value from the kernel vocabulary.

    Parameters
    ----------
    raw:
        Original nonroot cgroup.type bytes, bounded to 128 bytes.

    Returns
    -------
    str
        Domain, threaded domain, invalid domain or threaded value exactly.

    Raises
    ------
    ValueError
        On invalid budget/type, encoding or an unsupported kernel type.

    Notes
    -----
    This does not accept the kernel-root marker: that marker requires an actual
    descriptor identity comparison in control ancestry capture. Valid type bytes
    alone cannot prove namespace or process custody.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= 128:
        raise ValueError("kernel cgroup type exceeds budget")
    kind = raw.decode("ascii").strip()
    if kind not in {"domain", "domain threaded", "domain invalid", "threaded"}:
        raise ValueError("unsupported kernel cgroup type")
    return kind


def parse_protected_cgroup_membership(raw: bytes) -> str:
    """Parse a bounded unified membership path without establishing kernel origin.

    Parameters
    ----------
    raw:
        Original process membership bytes, bounded to 4096 bytes.

    Returns
    -------
    str
        Absolute unified cgroup path without dot traversal components.

    Raises
    ------
    ValueError
        On an invalid budget, non-unified membership or path traversal.
    UnicodeError
        On non-ASCII membership bytes.

    Notes
    -----
    Actual capture must independently read procfs, bind retained directory
    identity and check membership again. Parsed caller bytes grant no authority.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= 4096:
        raise ValueError("writer cgroup membership exceeds read budget")
    if not raw.startswith(b"0::/") or len(raw.splitlines()) != 1:
        raise ValueError("writer requires one unified cgroup membership")
    path = raw.decode("ascii").split("::", 1)[1].strip()
    if any(part in {".", ".."} for part in path[1:].split("/")):
        raise ValueError("invalid writer cgroup membership path")
    return path
