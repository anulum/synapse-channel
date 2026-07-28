# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded outbound A2A response and receipt boundaries
"""Exercise shared hostile-response and owner-only receipt handling."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from synapse_channel.a2a_outbound_response import (
    A2A_MAX_JSON_MEMBERS,
    A2AReceiptWriteError,
    A2AResponseShapeError,
    _fsync_parent,
    read_a2a_response,
    write_a2a_receipt,
)
from synapse_channel.core.http_response import BoundedReadError
from synapse_channel.core.protocol import MAX_JSON_DEPTH


class _Response:
    """Minimal bounded-reader response double."""

    def __init__(self, body: bytes, *, content_length: object | None = None):
        self.body = body
        self.read_amounts: list[int] = []
        self.headers: dict[str, object] = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def read(self, amount: int) -> bytes:
        self.read_amounts.append(amount)
        return self.body[:amount]


def test_reads_a_bounded_json_object() -> None:
    decoded, kind = read_a2a_response(
        _Response(b'{"task":{"id":"t-1"}}'),
        purpose="test response",
        max_bytes=64,
    )
    assert decoded == {"task": {"id": "t-1"}}
    assert kind == "object"


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        (b"", "empty"),
        (b"peer-secret-not-json", "non_json"),
        (b"\x80\x81", "non_json"),
        (b"[]", "json_array"),
        (b'"peer-secret-scalar"', "json_scalar"),
        (b"42", "json_scalar"),
    ],
)
def test_reduces_non_object_bodies_to_fixed_kinds(body: bytes, kind: str) -> None:
    decoded, observed_kind = read_a2a_response(
        _Response(body),
        purpose="test response",
        max_bytes=64,
    )
    assert decoded is None
    assert observed_kind == kind
    assert "secret" not in observed_kind


def test_reduces_too_deep_json_to_a_fixed_kind() -> None:
    body = ("[" * (MAX_JSON_DEPTH + 1) + "]" * (MAX_JSON_DEPTH + 1)).encode()
    assert read_a2a_response(_Response(body), purpose="test response")[1] == "non_json"


@pytest.mark.parametrize("body", [b'{"value":1e999}', b'{"value":-1e999}'])
def test_reduces_float_overflow_to_a_fixed_kind(body: bytes) -> None:
    decoded, kind = read_a2a_response(_Response(body), purpose="test response")
    assert decoded is None
    assert kind == "non_json"


def test_accepts_exact_cumulative_json_member_limit() -> None:
    body = json.dumps({"items": [0] * (A2A_MAX_JSON_MEMBERS - 1)}).encode()
    decoded, kind = read_a2a_response(_Response(body), purpose="test response")
    assert isinstance(decoded, dict)
    assert kind == "object"


def test_rejects_cumulative_json_members_past_limit_without_values() -> None:
    body = json.dumps({"items": ["peer-secret"] * A2A_MAX_JSON_MEMBERS}).encode()
    with pytest.raises(A2AResponseShapeError) as caught:
        read_a2a_response(_Response(body), purpose="test response")
    assert str(A2A_MAX_JSON_MEMBERS) in str(caught.value)
    assert "peer-secret" not in str(caught.value)


def test_counts_nested_object_and_array_members_cumulatively() -> None:
    body = b'{"outer":[{"a":1},{"b":2}]}'
    with pytest.raises(A2AResponseShapeError, match="4-member"):
        read_a2a_response(
            _Response(body),
            purpose="test response",
            max_members=4,
        )


def test_rejects_declared_oversize_before_reading() -> None:
    response = _Response(b"{}", content_length=65)
    with pytest.raises(BoundedReadError, match="64-byte limit"):
        read_a2a_response(response, purpose="test response", max_bytes=64)
    assert response.read_amounts == []


def test_rejects_streamed_oversize_with_limit_plus_one_read() -> None:
    response = _Response(b"x" * 65)
    with pytest.raises(BoundedReadError, match="64-byte limit"):
        read_a2a_response(response, purpose="test response", max_bytes=64)
    assert response.read_amounts == [65]


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_receipt_is_owner_only_under_permissive_umask(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "receipt.json"
    previous = os.umask(0)
    try:
        written = write_a2a_receipt(target, {"task": "t-1"})
    finally:
        os.umask(previous)
    assert written == target
    assert _mode(target) == 0o600
    assert target.read_text(encoding="utf-8").endswith("\n")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink and mode semantics")
def test_receipt_replaces_a_broad_mode_symlink_with_owner_only_file(tmp_path: Path) -> None:
    victim = tmp_path / "victim.json"
    victim.write_text("unchanged", encoding="utf-8")
    target = tmp_path / "receipt.json"
    target.symlink_to(victim)

    write_a2a_receipt(target, {"safe": True})

    assert not target.is_symlink()
    assert _mode(target) == 0o600
    assert victim.read_text(encoding="utf-8") == "unchanged"


def test_receipt_replace_failure_preserves_target_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "receipt.json"
    target.write_text("old\n", encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace blocked")

    monkeypatch.setattr("synapse_channel.a2a_outbound_response.os.replace", fail_replace)
    with pytest.raises(A2AReceiptWriteError, match="A2A receipt write failed") as caught:
        write_a2a_receipt(target, {"new": True})

    assert isinstance(caught.value.__cause__, OSError)
    assert target.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.glob(".receipt.json.*.tmp")) == []


def test_receipt_cancellation_cleans_temp_and_preserves_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "receipt.json"

    def cancel_replace(_source: Path, _target: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.a2a_outbound_response.os.replace", cancel_replace)
    with pytest.raises(KeyboardInterrupt):
        write_a2a_receipt(target, {"new": True})

    assert not target.exists()
    assert list(tmp_path.glob(".receipt.json.*.tmp")) == []


def test_receipt_serialization_failure_preserves_target_and_cleans_temp(tmp_path: Path) -> None:
    target = tmp_path / "receipt.json"
    target.write_text("old\n", encoding="utf-8")
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(A2AReceiptWriteError, match="serialization failed") as caught:
        write_a2a_receipt(target, cyclic)

    assert isinstance(caught.value.__cause__, ValueError)
    assert target.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.glob(".receipt.json.*.tmp")) == []


def test_receipt_rejects_non_finite_values_before_creating_output(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "receipt.json"

    with pytest.raises(A2AReceiptWriteError, match="serialization failed") as caught:
        write_a2a_receipt(target, {"value": float("inf")})

    assert isinstance(caught.value.__cause__, ValueError)
    assert not target.parent.exists()


def test_receipt_parent_setup_failure_uses_domain_error(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied"
    occupied.write_text("unchanged", encoding="utf-8")

    with pytest.raises(A2AReceiptWriteError, match="write failed") as caught:
        write_a2a_receipt(occupied / "receipt.json", {"safe": True})

    assert isinstance(caught.value.__cause__, FileExistsError)
    assert occupied.read_text(encoding="utf-8") == "unchanged"


def test_parent_fsync_noops_off_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("synapse_channel.a2a_outbound_response.os.name", "nt")
    monkeypatch.setattr(
        "synapse_channel.a2a_outbound_response.os.open",
        lambda *_args, **_kwargs: pytest.fail("must not open a directory"),
    )
    _fsync_parent(tmp_path / "receipt.json")


def test_parent_fsync_suppresses_unsupported_directory_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "synapse_channel.a2a_outbound_response.os.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("unsupported")),
    )
    _fsync_parent(tmp_path / "receipt.json")
