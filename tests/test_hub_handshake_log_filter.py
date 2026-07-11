# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for quieting benign aborted-handshake log spam

from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest
from websockets.exceptions import ConnectionClosedError, WebSocketException
from websockets.frames import Close, CloseCode

from synapse_channel.core.logging_setup import HandshakeAbortFilter, configure_logging


@pytest.fixture(autouse=True)
def _restore_synapse_logging() -> Iterator[None]:
    # configure_logging mutates the global `synapse` logger; restore it after.
    lg = logging.getLogger("synapse")
    saved = (list(lg.handlers), lg.propagate, lg.level)
    try:
        yield
    finally:
        lg.handlers[:] = saved[0]
        lg.propagate = saved[1]
        lg.setLevel(saved[2])


def _record(
    message: str, exc: BaseException | None, level: int = logging.ERROR
) -> logging.LogRecord:
    exc_info = (type(exc), exc, None) if exc is not None else None
    return logging.LogRecord("synapse.hub.ws", level, __file__, 0, message, (), exc_info)


def test_drops_a_handshake_aborted_by_a_client_disconnect() -> None:
    # A load-balancer TCP probe, a port scan, or a dropped client aborts the
    # handshake with EOFError; the full-traceback ERROR is spam and is dropped.
    record = _record("opening handshake failed", EOFError("stream ends after 0 bytes"))

    assert HandshakeAbortFilter().filter(record) is False


def test_drops_a_handshake_aborted_by_a_connection_error() -> None:
    record = _record("opening handshake failed", ConnectionResetError("peer reset"))

    assert HandshakeAbortFilter().filter(record) is False


def test_drops_a_handshake_whose_cause_chain_is_a_benign_disconnect() -> None:
    # websockets wraps the underlying disconnect (an EOFError) in its own
    # InvalidMessage raised ``from`` it, so the benign case is only visible through
    # the __cause__ chain, not the top exception.
    wrapper = RuntimeError("did not receive a valid HTTP request")
    wrapper.__cause__ = EOFError("connection closed while reading HTTP request line")
    record = _record("opening handshake failed", wrapper)

    assert HandshakeAbortFilter().filter(record) is False


def test_drops_a_frameless_connection_closed_error() -> None:
    # The 0.99.3 reconnect-storm signature: the live hub logged four full
    # tracebacks of ConnectionClosedError("no close frame received or sent").
    # The real class subclasses WebSocketException, NOT ConnectionError, and
    # carries no cause chain, so only the frameless-close rule can match it.
    exc = ConnectionClosedError(None, None)
    assert str(exc) == "no close frame received or sent"
    assert not isinstance(exc, ConnectionError)
    assert isinstance(exc, WebSocketException)
    assert exc.__cause__ is None and exc.__context__ is None
    record = _record("opening handshake failed", exc)

    assert HandshakeAbortFilter().filter(record) is False


def test_keeps_a_framed_connection_closed_error() -> None:
    # A close frame in either direction carries a genuine close code — that is
    # a completed conversation, not a mid-handshake vanish, and must stay
    # loggable in both orientations.
    received = ConnectionClosedError(Close(CloseCode.INTERNAL_ERROR, "server error"), None)
    sent = ConnectionClosedError(None, Close(CloseCode.PROTOCOL_ERROR, "protocol error"))

    assert HandshakeAbortFilter().filter(_record("opening handshake failed", received)) is True
    assert HandshakeAbortFilter().filter(_record("opening handshake failed", sent)) is True


def test_drops_a_wrapper_whose_chain_ends_in_a_frameless_close() -> None:
    # The frameless close must also be recognised through the cause chain,
    # exactly like the other benign aborts.
    wrapper = RuntimeError("connection closed during handshake")
    wrapper.__cause__ = ConnectionClosedError(None, None)
    record = _record("opening handshake failed", wrapper)

    assert HandshakeAbortFilter().filter(record) is False


def test_keeps_a_handshake_wrapped_over_a_genuine_fault() -> None:
    # A wrapper whose cause is not a connection abort is a real error — keep it.
    wrapper = RuntimeError("did not receive a valid HTTP request")
    wrapper.__cause__ = ValueError("malformed but complete request line")
    record = _record("opening handshake failed", wrapper)

    assert HandshakeAbortFilter().filter(record) is True


def test_keeps_a_genuine_handshake_error() -> None:
    # A completed-but-invalid request fails with something other than a benign
    # disconnect; that is a real error and must still be logged.
    record = _record("opening handshake failed", ValueError("malformed but complete request"))

    assert HandshakeAbortFilter().filter(record) is True


def test_keeps_a_handshake_failure_without_an_exception() -> None:
    # With no exception attached the benign-abort cannot be confirmed, so keep it.
    record = _record("opening handshake failed", None)

    assert HandshakeAbortFilter().filter(record) is True


def test_keeps_an_unrelated_server_log() -> None:
    record = _record("connection open", None, level=logging.INFO)

    assert HandshakeAbortFilter().filter(record) is True


def test_configure_logging_installs_the_filter_on_the_handler() -> None:
    logger = configure_logging(stream=io.StringIO())

    assert any(
        isinstance(filt, HandshakeAbortFilter) for h in logger.handlers for filt in h.filters
    )


def test_the_hub_ws_logger_reaches_the_configured_handler() -> None:
    # websockets logs through synapse.hub.ws (a descendant of the synapse logger),
    # so its records must reach the app's single handler where the filter lives.
    stream = io.StringIO()
    configure_logging(stream=stream)

    logging.getLogger("synapse.hub.ws").warning("a real websockets warning")

    assert "a real websockets warning" in stream.getvalue()


def test_a_benign_handshake_abort_is_suppressed_end_to_end() -> None:
    # THE regression this fixes: websockets raises InvalidMessage ``from`` a plain
    # disconnect and logs it at ERROR with a full traceback. Through the real
    # handler the record must be dropped — matched via the __cause__ chain.
    stream = io.StringIO()
    configure_logging(stream=stream)
    ws_logger = logging.getLogger("synapse.hub.ws")

    try:
        try:
            raise EOFError("connection closed while reading HTTP request line")
        except EOFError as exc:
            raise RuntimeError("did not receive a valid HTTP request") from exc
    except RuntimeError:
        ws_logger.error("opening handshake failed", exc_info=True)

    assert "opening handshake failed" not in stream.getvalue()


def test_a_frameless_close_is_suppressed_end_to_end() -> None:
    # Through the real configured handler the reconnect-storm record must be
    # dropped while the stream stays usable for everything else.
    stream = io.StringIO()
    configure_logging(stream=stream)
    ws_logger = logging.getLogger("synapse.hub.ws")

    try:
        raise ConnectionClosedError(None, None)
    except ConnectionClosedError:
        ws_logger.error("opening handshake failed", exc_info=True)

    assert "opening handshake failed" not in stream.getvalue()


def test_a_child_loggers_genuine_error_still_reaches_the_stream() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)
    child = logging.getLogger("synapse.hub.ws").getChild("conn-xyz")

    try:
        raise ValueError("a real problem")
    except ValueError:
        child.error("opening handshake failed", exc_info=True)

    assert "opening handshake failed" in stream.getvalue()
