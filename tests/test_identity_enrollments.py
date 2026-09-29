# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the enrolment store, the trust merge and the ordered denial policy
"""Tests for :mod:`synapse_channel.core.identity_enrollments` on real files."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest

from _platform_caps import requires_posix_mode_bits
from synapse_channel.core.identity_enrollments import (
    EnrollmentRateLimiter,
    EnrollmentRequest,
    IdentityEnrollmentError,
    decode_public_key,
    enrollment_denial,
    live_enrolled_key,
    load_enrolled_keys,
    merge_enrolled_keys,
    write_enrolled_keys,
)
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
)

PUBLIC = bytes(range(32))
PUBLIC_B64 = base64.b64encode(PUBLIC).decode("ascii")


def _key(key_id: str, *senders: str, revoked: bool = False, expires: float | None = None) -> Any:
    return EventSignatureKey(
        key_id=key_id,
        public_key=PUBLIC,
        senders=frozenset(senders),
        expires_at=expires,
        revoked=revoked,
    )


def _bundle(*keys: EventSignatureKey) -> EventSignatureTrustBundle:
    return EventSignatureTrustBundle(
        keys={key.key_id: key for key in keys},
        replay_cache=MessageReplayCache(window_seconds=30, max_entries=8),
    )


def test_an_absent_store_holds_no_keys(tmp_path: Path) -> None:
    assert load_enrolled_keys(tmp_path / "absent.json") == {}


def test_the_store_round_trips_every_field(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "enrolled.json"
    keys = {
        "a": _key("a", "P/a", expires=4_000_000_000.0),
        "b": _key("b", "P/b", revoked=True),
        "c": EventSignatureKey(
            key_id="c", public_key=PUBLIC, senders=frozenset({"P/c"}), projects=frozenset({"P"})
        ),
    }
    write_enrolled_keys(path, keys)
    assert load_enrolled_keys(path) == keys
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert [entry["key_id"] for entry in stored["keys"]] == ["a", "b", "c"]


@requires_posix_mode_bits
def test_a_store_others_can_read_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "enrolled.json"
    write_enrolled_keys(path, {"a": _key("a", "P/a")})
    os.chmod(path, 0o644)
    with pytest.raises(IdentityEnrollmentError, match="enrolment store"):
        load_enrolled_keys(path)


@pytest.mark.parametrize(
    "content",
    [b"not json", b'{"keys": 7}', b'{"keys": [{"key_id": "a"}]}', b"\xff\xfe"],
)
def test_a_malformed_store_is_refused(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "enrolled.json"
    write_enrolled_keys(path, {})
    path.write_bytes(content)
    with pytest.raises(IdentityEnrollmentError):
        load_enrolled_keys(path)


def test_a_failed_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "enrolled.json"
    target.mkdir()  # the atomic replace cannot land on a directory
    with pytest.raises(OSError):
        write_enrolled_keys(target, {"a": _key("a", "P/a")})
    assert sorted(path.name for path in tmp_path.iterdir()) == ["enrolled.json"]


def test_the_merge_keeps_the_replay_cache_and_the_static_names() -> None:
    static = _bundle(_key("s", "P/s"))
    merged = merge_enrolled_keys(static, {"e": _key("e", "P/e")})
    assert set(merged.keys) == {"s", "e"}
    assert merged.replay_cache is static.replay_cache
    with pytest.raises(IdentityEnrollmentError, match="in both"):
        merge_enrolled_keys(static, {"s": _key("s", "P/x")})
    with pytest.raises(IdentityEnrollmentError, match="stays authoritative"):
        merge_enrolled_keys(static, {"e": _key("e", "P/s")})
    # revoked history for a name the static bundle later took over is tolerated
    assert "e" in merge_enrolled_keys(static, {"e": _key("e", "P/s", revoked=True)}).keys


def test_only_a_live_unexpired_key_counts_as_current() -> None:
    keys = {
        "old": _key("old", "P/a", revoked=True),
        "past": _key("past", "P/a", expires=10.0),
        "now": _key("now", "P/a", expires=100.0),
    }
    assert live_enrolled_key(keys, "P/a", now=50.0) == keys["now"]
    assert live_enrolled_key(keys, "P/a", now=200.0) is None
    assert live_enrolled_key({"x": _key("x", "P/a")}, "P/a", now=1e12) == _key("x", "P/a")
    assert live_enrolled_key(keys, "P/b", now=0.0) is None


def test_public_keys_decode_only_as_32_raw_bytes() -> None:
    assert decode_public_key(PUBLIC_B64) == PUBLIC
    assert decode_public_key(f"  {PUBLIC_B64} ") == PUBLIC
    for value in ("AAAA", "!!", 7, None):
        assert decode_public_key(value) is None


def _request(**fields: Any) -> EnrollmentRequest:
    values: dict[str, Any] = {
        "requester": "OPS/op",
        "name": "P/new",
        "key_id": "k-new",
        "public_key": PUBLIC,
        "reason": "new seat",
        "expected_key_id": "",
        "expires_at": None,
    }
    values.update(fields)
    return EnrollmentRequest(**values)


def _deny(request: EnrollmentRequest, **observed: Any) -> str:
    gates: dict[str, Any] = {
        "enabled": True,
        "requester_bound": True,
        "acl_allowed": True,
        "role_granted": True,
        "namespace_allowed": True,
        "rate_allowed": True,
        "static": {"s": _key("s", "P/static", "OPS/op")},
        "enrolled": {},
        "now": 1_000.0,
    }
    gates.update(observed)
    return enrollment_denial(request, **gates)


def test_the_authority_gates_refuse_in_order_before_the_request_is_read() -> None:
    bad = _request(name="", key_id="", public_key=None, reason="")
    order = [
        ("enabled", "disabled"),
        ("requester_bound", "cryptographically proven"),
        ("acl_allowed", "not authorised"),
        ("role_granted", "role grant"),
        ("namespace_allowed", "namespace"),
        ("rate_allowed", "rate limit"),
    ]
    for index, (gate, detail) in enumerate(order):
        closed = {name: False for name, _ in order[index:]}
        assert detail in _deny(bad, **closed), gate
    assert "<project>/<id>" in _deny(bad)


def test_the_request_checks() -> None:
    assert _deny(_request()) == ""
    assert _deny(_request(expires_at=2_000.0)) == ""
    assert "future time" in _deny(_request(expires_at=float("inf")))
    assert "future time" in _deny(_request(expires_at=float("nan")))
    assert "already covers" in _deny(_request(name="P/static"))
    assert "already in use" in _deny(_request(key_id="s"))
    assert "only rotate its own key" in _deny(_request(name="OPS/op2", requester="OPS/op2"))
    current = {"cur": _key("cur", "P/new")}
    assert "rotate it by naming" in _deny(_request(), enrolled=current)
    assert "rotate it by naming" in _deny(_request(expected_key_id="other"), enrolled=current)
    assert _deny(_request(expected_key_id="cur"), enrolled=current) == ""
    assert _deny(_request(requester="P/new", expected_key_id="cur"), enrolled=current) == ""


def test_the_rate_window_slides() -> None:
    limiter = EnrollmentRateLimiter(limit=2, window_seconds=10.0)
    assert limiter.allows("a", now=0.0)
    limiter.record("a", now=0.0)
    limiter.record("a", now=5.0)
    assert not limiter.allows("a", now=9.0)
    assert limiter.allows("b", now=9.0)  # per requester
    assert limiter.allows("a", now=10.5)  # the first change left the window
    assert not EnrollmentRateLimiter(limit=0).allows("a", now=0.0)
