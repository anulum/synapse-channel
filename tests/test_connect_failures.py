# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for hub connection-failure classification

from __future__ import annotations

import asyncio
from typing import Any, cast

from websockets.exceptions import ConnectionClosed

from synapse_channel import connect_failures
from synapse_channel.client.agent_dispatch import MessageCallback
from synapse_channel.connect_failures import (
    AUTH_TIMEOUT_CLOSE_CODE,
    CAPACITY_CLOSE_CODE,
    NAME_CONFLICT_CLOSE_CODE,
    PER_HOST_CAP_CLOSE_CODE,
    SUPERSEDED_CLOSE_CODE,
    TAKEOVER_COOLDOWN_CLOSE_CODE,
    closed_after_ready,
    describe_connect_failure,
    explain_silent_outcome,
)
from synapse_channel.core.protocol import MessageType


def test_absent_hub_keeps_the_generic_unreachable_line() -> None:
    message = describe_connect_failure("A", "ws://localhost:8876", close_code=None)

    assert message == "[A] Could not reach hub at ws://localhost:8876."


def test_capacity_close_explains_the_cap_and_remedy() -> None:
    message = describe_connect_failure(
        "A", "ws://localhost:8876", close_code=CAPACITY_CLOSE_CODE, close_reason="hub at capacity"
    )

    assert "hub at capacity" in message
    assert "--max-clients" in message
    assert f"code {CAPACITY_CLOSE_CODE}" in message
    # The reason matches the guidance, so it is not duplicated as a suffix.
    assert "hub said" not in message


def test_name_conflict_close_directs_to_a_unique_name() -> None:
    message = describe_connect_failure(
        "A/dup", "ws://localhost:8876", close_code=NAME_CONFLICT_CLOSE_CODE
    )

    assert "unique --name" in message
    assert f"code {NAME_CONFLICT_CLOSE_CODE}" in message


def test_reserved_identity_close_explains_that_retry_cannot_succeed() -> None:
    message = describe_connect_failure(
        "System",
        "ws://localhost:8876",
        close_code=NAME_CONFLICT_CLOSE_CODE,
        close_reason="reserved identity",
    )

    assert "reserved for hub protocol provenance" in message
    assert "cannot be registered" in message
    assert "project-scoped identity" in message
    assert "already online" not in message
    assert f"code {NAME_CONFLICT_CLOSE_CODE}" in message


def test_superseded_and_cooldown_codes_are_recognised() -> None:
    superseded = describe_connect_failure(
        "A", "ws://h", close_code=SUPERSEDED_CLOSE_CODE, close_reason="superseded"
    )
    cooldown = describe_connect_failure(
        "A", "ws://h", close_code=TAKEOVER_COOLDOWN_CLOSE_CODE, close_reason="takeover cooldown"
    )

    assert "superseded" in superseded
    assert "cooldown" in cooldown


def test_4010_auth_reason_is_not_reported_as_superseded() -> None:
    # The hub reuses 4010 for both a takeover and an authentication refusal.
    auth = describe_connect_failure(
        "A", "ws://h", close_code=SUPERSEDED_CLOSE_CODE, close_reason="auth denied"
    )

    assert "authentication" in auth.lower()
    assert "--token" in auth
    assert "superseded" not in auth


def test_4014_unauth_cap_is_not_reported_as_cooldown() -> None:
    # The hub reuses 4014 for both a takeover cooldown and the unauth-socket cap.
    unauth = describe_connect_failure(
        "A",
        "ws://h",
        close_code=TAKEOVER_COOLDOWN_CLOSE_CODE,
        close_reason="too many unauthenticated connections",
    )

    assert "unauthenticated" in unauth
    assert "cooldown" not in unauth


def test_auth_timeout_and_per_host_cap_codes_are_recognised() -> None:
    timeout = describe_connect_failure(
        "A", "ws://h", close_code=AUTH_TIMEOUT_CLOSE_CODE, close_reason="auth timeout"
    )
    per_host = describe_connect_failure(
        "A", "ws://h", close_code=PER_HOST_CAP_CLOSE_CODE, close_reason="too many connections"
    )

    assert "authentication timed out" in timeout.lower()
    assert "per-host" in per_host
    assert "--max-connections-per-host" in per_host


def test_unknown_close_code_reports_code_and_reason() -> None:
    message = describe_connect_failure(
        "A", "ws://h", close_code=4099, close_reason="experimental drain"
    )

    assert "code 4099" in message
    assert "experimental drain" in message


def test_unknown_close_code_without_reason_omits_the_detail_suffix() -> None:
    message = describe_connect_failure("A", "ws://h", close_code=4099)

    assert message == "[A] Hub closed the connection (code 4099)."


def test_recognised_code_appends_a_distinct_hub_reason() -> None:
    message = describe_connect_failure(
        "A",
        "ws://h",
        close_code=CAPACITY_CLOSE_CODE,
        close_reason="evicted: maintenance window",
    )

    assert "hub said: evicted: maintenance window" in message


def test_silent_outcome_keeps_the_fallback_when_socket_stayed_open() -> None:
    message = explain_silent_outcome(
        "A", "ws://h", close_code=None, close_reason="", fallback="no response from hub"
    )

    assert message == "no response from hub"


def test_silent_outcome_surfaces_the_close_code_when_present() -> None:
    message = explain_silent_outcome(
        "A",
        "ws://h",
        close_code=NAME_CONFLICT_CLOSE_CODE,
        close_reason="name conflict",
        fallback="no response from hub",
    )

    assert "already online" in message
    assert "code 4009" in message
    assert message != "no response from hub"


class _FakeConn:
    """A one-shot client whose roster request is answered by a scripted hub.

    ``answers`` are the frames the hub sends after the request, delivered in
    order in one burst once the request is out. Each one is either a frame for
    the callback, or an ``int`` close code that the client records with
    ``close_reason`` and stops on, as a real hub close does.
    """

    def __init__(
        self,
        *,
        answers: tuple[dict[str, Any] | int, ...] = (),
        running: bool = True,
        close: int | None = None,
        close_reason: str = "",
        send_error: BaseException | None = None,
        callback: MessageCallback | None = None,
    ) -> None:
        self.running = running
        self.last_close_code = close
        self.last_close_reason = ""
        self.callback = callback
        self.requests = 0
        self._answers = answers
        self._close_reason = close_reason
        self._send_error = send_error

    async def request_who(self, hub: str | None = None) -> None:
        assert hub is None
        self.requests += 1
        if self._send_error is not None:
            raise self._send_error
        asyncio.get_running_loop().create_task(self._answer())

    async def _answer(self) -> None:
        for answer in self._answers:
            if isinstance(answer, int):
                self.last_close_code, self.last_close_reason = answer, self._close_reason
                self.running = False
                return
            assert self.callback is not None
            await self.callback(answer)


def _recorder() -> tuple[list[dict[str, Any]], MessageCallback]:
    seen: list[dict[str, Any]] = []

    async def record(data: dict[str, Any]) -> None:
        seen.append(data)

    return seen, record


_WHO = {"type": MessageType.WHO_SNAPSHOT, "online_agents": ["A"]}
_PRESENCE = {"type": "presence_update", "agent": "B"}


_HEARTBEAT_ERROR = {"type": MessageType.ERROR, "payload": "journal recovery refuses mutations"}
_ROSTER_ERROR = {"type": MessageType.ERROR, "payload": "Rate limit exceeded."}


async def test_closed_after_ready_accepts_on_the_roster_answer_and_consumes_answers() -> None:
    seen, record = _recorder()
    conn = _FakeConn(answers=(_PRESENCE, _WHO, dict(_WHO)), callback=record)

    assert await closed_after_ready(conn, timeout=2.0) is False
    assert conn.requests == 1
    # Nothing the command sent is in flight yet, so no roster or error frame
    # reaches its callback; every other frame does.
    assert seen == [_PRESENCE]
    assert conn.callback is record


async def test_closed_after_ready_forwards_to_a_sync_callback() -> None:
    seen: list[dict[str, Any]] = []
    conn = _FakeConn(answers=(_PRESENCE, _WHO))
    conn.callback = cast(MessageCallback, seen.append)

    assert await closed_after_ready(conn, timeout=2.0) is False
    assert seen == [_PRESENCE]


async def test_closed_after_ready_without_a_callback_drops_other_frames() -> None:
    conn = _FakeConn(answers=(_PRESENCE, _WHO))

    assert await closed_after_ready(conn, timeout=2.0) is False
    assert conn.callback is None


async def test_closed_after_ready_waits_past_a_heartbeat_error_for_the_roster_answer() -> None:
    seen, record = _recorder()
    conn = _FakeConn(answers=(_HEARTBEAT_ERROR, _WHO), callback=record)

    # The roster answer ends the wait; the long budget is never used up.
    assert await asyncio.wait_for(closed_after_ready(conn, timeout=60.0), 10) is False
    assert seen == []
    assert conn.last_close_reason == ""


async def test_closed_after_ready_consumes_a_roster_error_after_a_heartbeat_error() -> None:
    seen, record = _recorder()
    conn = _FakeConn(answers=(_HEARTBEAT_ERROR, _ROSTER_ERROR), callback=record)
    loop = asyncio.get_running_loop()
    started = loop.time()

    # Neither error can be the operation's result, so neither reaches the
    # callback. With no roster answer the gate counts as passed at the timeout.
    assert await closed_after_ready(conn, timeout=0.1) is False
    assert loop.time() - started >= 0.1
    assert seen == []
    assert conn.last_close_reason == ""


async def test_closed_after_ready_lets_a_verification_error_outrank_a_roster_answer() -> None:
    seen, record = _recorder()
    refusal = {
        "type": MessageType.ERROR,
        "payload": "frame signature refused: invalid",
        "verification_result": "invalid",
    }
    conn = _FakeConn(answers=(refusal, _WHO), callback=record)

    assert await closed_after_ready(conn, timeout=0.1) is True
    assert conn.last_close_reason == "frame signature refused: invalid"
    assert seen == []


async def test_closed_after_ready_waits_for_the_close_after_an_identity_refusal() -> None:
    refusal = {
        "type": MessageType.ERROR,
        "payload": "identity binding failed",
        "verification_result": "invalid_signature",
    }
    conn = _FakeConn(answers=(refusal, CAPACITY_CLOSE_CODE), close_reason="identity binding failed")

    # The close ends the wait; the long budget is never used up.
    assert await asyncio.wait_for(closed_after_ready(conn, timeout=60.0), 10) is True
    assert conn.last_close_code == CAPACITY_CLOSE_CODE
    assert "identity proof refused" in describe_connect_failure(
        "A", "ws://h", close_code=conn.last_close_code, close_reason=conn.last_close_reason
    )


async def test_closed_after_ready_records_a_refusal_that_leaves_the_socket_open() -> None:
    refusal = {
        "type": MessageType.ERROR,
        "payload": "frame signature refused: missing",
        "verification_result": "missing",
    }
    conn = _FakeConn(answers=(refusal,))

    assert await closed_after_ready(conn, timeout=0.1) is True
    assert conn.last_close_code is None
    assert conn.last_close_reason == "frame signature refused: missing"
    assert describe_connect_failure(
        "A", "ws://h", close_code=None, close_reason=conn.last_close_reason
    ) == ("[A] Hub refused this sender without closing: frame signature refused: missing.")

    bare = _FakeConn(answers=({"type": MessageType.ERROR, "verification_result": "x"},))
    assert await closed_after_ready(bare, timeout=0.1) is True
    assert bare.last_close_reason == "frame refused"


async def test_closed_after_ready_reports_a_close_instead_of_an_answer() -> None:
    conn = _FakeConn(answers=(NAME_CONFLICT_CLOSE_CODE,), close_reason="name conflict")

    assert await asyncio.wait_for(closed_after_ready(conn, timeout=60.0), 10) is True
    assert conn.last_close_code == NAME_CONFLICT_CLOSE_CODE


async def test_closed_after_ready_detects_a_close_recorded_before_the_request() -> None:
    for conn in (_FakeConn(close=NAME_CONFLICT_CLOSE_CODE), _FakeConn(running=False)):
        assert await asyncio.wait_for(closed_after_ready(conn, timeout=60.0), 10) is True


async def test_closed_after_ready_refuses_when_the_request_cannot_be_sent() -> None:
    for error in (ConnectionClosed(None, None), OSError("broken pipe")):
        seen, record = _recorder()
        conn = _FakeConn(send_error=error, callback=record)

        assert await closed_after_ready(conn, timeout=2.0) is True
        assert conn.callback is record


async def test_closed_after_ready_never_reads_silence_as_acceptance() -> None:
    conn = _FakeConn()
    loop = asyncio.get_running_loop()
    started = loop.time()

    assert await closed_after_ready(conn, timeout=0.1) is True
    assert loop.time() - started >= 0.1
    assert conn.last_close_code is None
    assert conn.last_close_reason == ""
    assert describe_connect_failure("A", "ws://h", close_code=None) == (
        "[A] Could not reach hub at ws://h."
    )
    # A negative budget is clamped to zero, which still refuses.
    assert await closed_after_ready(_FakeConn(), timeout=-1.0) is True


# --- the yield verdicts a re-arming waiter acts on ---


def test_superseded_close_is_recognised_and_auth_overload_excluded() -> None:
    assert connect_failures.is_superseded_close(4010, "superseded")
    assert not connect_failures.is_superseded_close(4010, "auth denied")
    assert not connect_failures.is_superseded_close(4010, "auth required")
    assert not connect_failures.is_superseded_close(4013, "superseded")
    assert not connect_failures.is_superseded_close(None, "superseded")


def test_takeover_refused_close_is_recognised_and_unauth_cap_excluded() -> None:
    assert connect_failures.is_takeover_refused_close(4014, "takeover cooldown")
    assert connect_failures.is_takeover_refused_close(4014, "takeover quarantine")
    assert not connect_failures.is_takeover_refused_close(
        4014, "too many unauthenticated connections"
    )
    assert not connect_failures.is_takeover_refused_close(4010, "takeover cooldown")
    assert not connect_failures.is_takeover_refused_close(None, "takeover cooldown")
