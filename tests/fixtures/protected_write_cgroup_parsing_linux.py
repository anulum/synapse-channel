# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — kernel cgroup parser input boundary tests
"""Exercise native cgroup snapshots and corrupt bytes at the public parser boundary."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from fixtures.protected_write_subtree_linux import delegated_subtree as delegated_subtree
from synapse_channel.core.protected_write_cgroup_parsing import (
    parse_protected_cgroup_events,
    parse_protected_cgroup_membership,
    parse_protected_cgroup_type,
)


def test_actual_live_events_parse_required_kernel_bits() -> None:
    """Decode the real running process's events, without certifying its custody."""
    membership = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
    raw = (Path("/sys/fs/cgroup") / membership.lstrip("/") / "cgroup.events").read_bytes()
    parsed = parse_protected_cgroup_events(raw)
    assert parsed.populated and not parsed.frozen
    assert parse_protected_cgroup_events(raw + b"future 1\n") == parsed


def test_actual_empty_frozen_events_parse_both_states(delegated_subtree: Path) -> None:
    """Parse both real kernel bits from an owned empty frozen cgroup."""
    raw = (delegated_subtree / "cgroup.events").read_bytes()
    assert not parse_protected_cgroup_events(raw).populated
    (delegated_subtree / "cgroup.freeze").write_text("1")
    try:
        parsed = parse_protected_cgroup_events((delegated_subtree / "cgroup.events").read_bytes())
        assert parsed.frozen and not parsed.populated
    finally:
        (delegated_subtree / "cgroup.freeze").write_text("0")


@pytest.mark.parametrize(
    "case",
    [
        "type",
        "empty",
        "budget",
        "encoding",
        "missing",
        "duplicate",
        "shape",
        "extra_value",
        "blank_line",
        "populated",
        "frozen",
    ],
)
def test_invalid_event_bytes_refuse_at_parser_boundary(case: str, delegated_subtree: Path) -> None:
    """Corrupted captured bytes prove parser refusal, never kernel provenance."""
    raw = (delegated_subtree / "cgroup.events").read_bytes()
    changes: dict[str, object] = {
        "type": bytearray(raw),
        "empty": b"",
        "budget": b"x" * 4097,
        "encoding": b"\xff",
        "missing": raw.splitlines()[0] + b"\n",
        "duplicate": raw + raw.splitlines()[0] + b"\n",
        "shape": raw + b"bad\n",
        "extra_value": raw + b"bad 1 2\n",
        "blank_line": raw + b"\n",
        "populated": raw.replace(b"populated 0", b"populated 2"),
        "frozen": raw.replace(b"frozen 0", b"frozen true"),
    }
    with pytest.raises(ValueError):
        parse_protected_cgroup_events(cast(bytes, changes[case]))


def test_actual_kernel_types_parse_all_supported_values(delegated_subtree: Path) -> None:
    """Capture domain, threaded, threaded-domain and invalid-domain kernel types."""
    assert parse_protected_cgroup_type((delegated_subtree / "cgroup.type").read_bytes()) == "domain"
    (delegated_subtree / "cgroup.type").write_text("threaded")
    assert (
        parse_protected_cgroup_type((delegated_subtree / "cgroup.type").read_bytes()) == "threaded"
    )
    assert (
        parse_protected_cgroup_type((delegated_subtree.parent / "cgroup.type").read_bytes())
        == "domain threaded"
    )
    invalid = delegated_subtree / "invalid"
    invalid.mkdir()
    assert parse_protected_cgroup_type((invalid / "cgroup.type").read_bytes()) == "domain invalid"


@pytest.mark.parametrize("raw", [b"", b"x" * 129, b"\xff", b"root", b"unknown", "domain"])
def test_invalid_type_bytes_do_not_become_kernel_root_markers(raw: object) -> None:
    """Refuse malformed type bytes without inventing a root marker."""
    with pytest.raises(ValueError):
        parse_protected_cgroup_type(cast(bytes, raw))


def test_actual_membership_parser_preserves_live_kernel_path() -> None:
    """Public parsing preserves the actual procfs unified membership."""
    raw = Path("/proc/self/cgroup").read_bytes()
    assert parse_protected_cgroup_membership(raw) == raw.decode("ascii").split("::", 1)[1].strip()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"x" * 4097,
        b"1:cpu:/group\n",
        b"0::/a\n0::/b\n",
        b"0::/a/../b\n",
        b"0::/a/./b\n",
        b"0::/\xff\n",
    ],
)
def test_membership_parser_refuses_unsupported_and_traversal_bytes(raw: bytes) -> None:
    """Malformed bytes test representation refusal, never process custody."""
    with pytest.raises((ValueError, UnicodeError)):
        parse_protected_cgroup_membership(raw)


def test_membership_parser_refuses_nonbytes_input() -> None:
    """A mutable representation is not accepted as an immutable proc snapshot."""
    with pytest.raises(ValueError, match="read budget"):
        parse_protected_cgroup_membership(cast(bytes, bytearray(b"0::/group\n")))
