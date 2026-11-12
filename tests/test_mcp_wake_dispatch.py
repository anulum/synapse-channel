# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — signed pointer transport and process recovery tests
"""Exercise owned attempts against independent real HTTP and TLS receivers."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Literal

import pytest
from pydantic import ValidationError

from mcp_http_helpers import tls_files
from synapse_channel.mcp.wake_dispatch import WakeHttpResult, deliver_pointer
from synapse_channel.mcp.wake_http import (
    MAX_WAKE_INPUT_BYTES,
    WakeEndpoint,
    WakeHttpRequest,
    send_signed_pointer,
)
from synapse_channel.mcp.wake_ledger import WakeLedger
from synapse_channel.mcp.wake_pointer import WakeAttempt, WakeLedgerError, WakeScope


class _Receiver:
    def __init__(self, directory: Path, *, tls: bool = True) -> None:
        self.key = b"independent-local-receiver-key-32"
        self.status = 202
        self.drop = 0
        self.stall = False
        self.requests: list[tuple[str, bytes]] = []
        self.effects: dict[str, bytes] = {}
        self.signatures: list[str] = []
        self.seen = threading.Event()
        self.release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                identifier = self.headers.get("webhook-id", "")
                timestamp = self.headers.get("webhook-timestamp", "")
                signature = self.headers.get("webhook-signature", "")
                expected = hmac.digest(
                    owner.key,
                    identifier.encode() + b"." + timestamp.encode() + b"." + body,
                    "sha256",
                )
                valid = signature.startswith("v1,") and hmac.compare_digest(
                    signature[3:], base64.b64encode(expected).decode()
                )
                valid = valid and timestamp.isdecimal() and abs(int(timestamp) - time.time()) <= 300
                owner.requests.append((identifier, body))
                owner.signatures.append(signature)
                owner.seen.set()
                if owner.stall:
                    owner.release.wait(5)
                    self.close_connection = True
                    return
                status = owner.status if valid else 401
                if 200 <= status <= 299:
                    owner.effects.setdefault(identifier, body)
                if owner.drop:
                    owner.drop -= 1
                    self.close_connection = True
                    return
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.ca_file: str | None = None
        if tls:
            cert, key, _ = tls_files(directory)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
            self.ca_file = str(cert)
        self.url = f"{'https' if tls else 'http'}://127.0.0.1:{self.server.server_port}/wake"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _Receiver:
        self.thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def endpoint(self, path: Path) -> WakeEndpoint:
        path.write_text("whsec_" + base64.b64encode(self.key).decode())
        path.chmod(0o600)
        return WakeEndpoint(
            url=self.url,
            signing_key_file=str(path),
            ca_file=self.ca_file,
            allow_local=True,
        )


def _ledger(directory: Path) -> tuple[WakeLedger, WakeAttempt]:
    scope = WakeScope("subject-1", "ALPHA", "ALPHA/bot", "a" * 64, "hub-1")
    ledger = WakeLedger(directory, scope)
    ledger.observe("messages", None, 5, relevant=True)
    attempt = ledger.acquire(100)
    assert attempt is not None
    return ledger, attempt


@pytest.mark.asyncio
async def test_signed_tls_delivery_preserves_committed_body_and_receipt(tmp_path: Path) -> None:
    ledger, attempt = _ledger(tmp_path / "ledger")
    with _Receiver(tmp_path) as receiver:
        endpoint = receiver.endpoint(tmp_path / "secret")
        result = await deliver_pointer(endpoint, attempt)
        assert result == WakeHttpResult("accepted", 202)
        assert receiver.requests == [(attempt.delivery_id, attempt.body)]
        assert receiver.effects == {attempt.delivery_id: attempt.body}
        pointer = json.loads(attempt.body)
        assert pointer["seat"] == "ALPHA/bot"
        assert "subject" not in pointer
        assert ledger.finish(attempt, 102, result.outcome, http_status=result.http_status)
        assert ledger.position("messages") == 5
        assert ledger.summary()["transport_accepted"] == 1
        assert ledger.acquire(103) is None


@pytest.mark.asyncio
async def test_lost_reply_retry_and_key_rotation_keep_id_and_body(tmp_path: Path) -> None:
    ledger, attempt = _ledger(tmp_path / "ledger")
    with _Receiver(tmp_path) as receiver:
        secret = tmp_path / "secret"
        endpoint = receiver.endpoint(secret)
        receiver.drop = 1
        first = await deliver_pointer(endpoint, attempt)
        assert first == WakeHttpResult("retry", 0)
        assert ledger.finish(attempt, 101, first.outcome)
        reopened = WakeLedger(tmp_path / "ledger", ledger.scope)
        retried = reopened.acquire(102)
        assert retried is not None
        assert (retried.delivery_id, retried.body) == (attempt.delivery_id, attempt.body)
        receiver.key = b"a-distinct-rotated-local-key-0001"
        replacement = tmp_path / "replacement"
        replacement.write_text("whsec_" + base64.b64encode(receiver.key).decode())
        replacement.chmod(0o600)
        replacement.replace(secret)
        second = await deliver_pointer(endpoint, retried)
        assert second == WakeHttpResult("accepted", 202)
        assert receiver.requests == [(attempt.delivery_id, attempt.body)] * 2
        assert receiver.signatures[0] != receiver.signatures[1]
        assert len(receiver.effects) == 1
        assert reopened.finish(retried, 103, second.outcome, http_status=second.http_status)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (200, "accepted"),
        (204, "accepted"),
        (400, "rejected"),
        (401, "rejected"),
        (403, "rejected"),
        (408, "retry"),
        (425, "retry"),
        (429, "retry"),
        (500, "retry"),
        (503, "retry"),
        (600, "retry"),
    ],
)
async def test_actual_receiver_status_classifies_transport(
    tmp_path: Path, status: int, outcome: Literal["accepted", "retry", "rejected"]
) -> None:
    _, attempt = _ledger(tmp_path / "ledger")
    with _Receiver(tmp_path, tls=False) as receiver:
        endpoint = receiver.endpoint(tmp_path / "secret")
        receiver.status = status
        result = await deliver_pointer(endpoint, attempt)
        assert result == WakeHttpResult(outcome, status if status < 600 else 0)
        assert len(receiver.requests) == 1


@pytest.mark.asyncio
async def test_wrong_key_is_refused_by_independent_receiver(tmp_path: Path) -> None:
    _, attempt = _ledger(tmp_path / "ledger")
    with _Receiver(tmp_path) as receiver:
        endpoint = receiver.endpoint(tmp_path / "secret")
        receiver.key = b"receiver-different-signing-key-001"
        assert await deliver_pointer(endpoint, attempt) == WakeHttpResult("rejected", 401)
        assert receiver.effects == {}


@pytest.mark.asyncio
async def test_untrusted_tls_and_private_default_do_not_deliver(tmp_path: Path) -> None:
    _, attempt = _ledger(tmp_path / "ledger")
    with _Receiver(tmp_path) as receiver:
        endpoint = receiver.endpoint(tmp_path / "secret")
        variations: list[dict[str, object]] = [{"ca_file": None}, {"allow_local": False}]
        for changes in variations:
            changed = WakeEndpoint.model_validate(endpoint.model_dump() | changes)
            assert await deliver_pointer(changed, attempt) == WakeHttpResult("retry", 0)
        assert receiver.requests == []


@pytest.mark.asyncio
async def test_attempt_deadline_reaps_worker_while_receiver_is_stalled(tmp_path: Path) -> None:
    _, attempt = _ledger(tmp_path / "ledger")
    with _Receiver(tmp_path, tls=False) as receiver:
        endpoint = receiver.endpoint(tmp_path / "secret")
        receiver.stall = True
        started = time.monotonic()
        result = await deliver_pointer(endpoint, attempt, timeout=0.8)
        assert result == WakeHttpResult("retry", 0)
        assert receiver.seen.is_set()
        assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_cancellation_propagates_after_worker_teardown(tmp_path: Path) -> None:
    _, attempt = _ledger(tmp_path / "ledger")
    with _Receiver(tmp_path, tls=False) as receiver:
        endpoint = receiver.endpoint(tmp_path / "secret")
        receiver.stall = True
        task = asyncio.create_task(deliver_pointer(endpoint, attempt))
        try:
            assert await asyncio.to_thread(receiver.seen.wait, 4)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_worker_owner_pipe_loss_exits_during_blocked_http(tmp_path: Path) -> None:
    _, attempt = _ledger(tmp_path / "ledger")
    with _Receiver(tmp_path, tls=False) as receiver:
        endpoint = receiver.endpoint(tmp_path / "secret")
        receiver.stall = True
        request = WakeHttpRequest(
            endpoint=endpoint, delivery_id=attempt.delivery_id, body=attempt.body.decode()
        )
        with subprocess.Popen(
            [sys.executable, "-m", "synapse_channel.mcp.wake_http"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as process:
            assert process.stdin is not None
            process.stdin.write(request.model_dump_json().encode() + b"\n")
            process.stdin.flush()
            assert receiver.seen.wait(4)
            process.stdin.close()
            assert process.wait(2) == 1
            assert process.stdout is not None and process.stdout.read() == b""
            assert process.stderr is not None and process.stderr.read() == b""


@pytest.mark.parametrize("raw", [b"{}\n", b"x" * (MAX_WAKE_INPUT_BYTES + 1), b"{}"])
def test_worker_invalid_pipe_input_is_fixed_and_bounded(raw: bytes) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "synapse_channel.mcp.wake_http"],
        input=raw,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == b"0\n"
    assert result.stderr == b""


@pytest.mark.parametrize("secret", ["not-prefixed", "whsec_%%%%", "whsec_eA=="])
def test_signing_file_refusal_does_not_touch_receiver(tmp_path: Path, secret: str) -> None:
    _, attempt = _ledger(tmp_path / "ledger")
    with _Receiver(tmp_path, tls=False) as receiver:
        endpoint = receiver.endpoint(tmp_path / "secret")
        Path(endpoint.signing_key_file).write_text(secret)
        request = WakeHttpRequest(
            endpoint=endpoint, delivery_id=attempt.delivery_id, body=attempt.body.decode()
        )
        assert send_signed_pointer(request) == 0
        assert receiver.requests == []


def test_real_signing_entry_handles_nonascii_and_http_refusal(tmp_path: Path) -> None:
    with _Receiver(tmp_path, tls=False) as receiver:
        endpoint = receiver.endpoint(tmp_path / "secret")
        request = WakeHttpRequest(endpoint=endpoint, delivery_id="msg_" + "a" * 32, body="é")
        assert send_signed_pointer(request) == 0
        valid = WakeHttpRequest(endpoint=endpoint, delivery_id="msg_" + "a" * 32, body="{}")
        assert send_signed_pointer(valid) == 202
        receiver.status = 403
        assert send_signed_pointer(valid) == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "http://example.test/hook"},
        {"url": "https://user@example.test/hook"},
        {"url": "https://example.test/hook?secret=value"},
        {"url": "https://example.test/hook#fragment"},
        {"url": "https://example.test/with space"},
        {"signing_key_file": "relative"},
        {"ca_file": "relative"},
    ],
)
def test_endpoint_rejects_ambiguous_or_unsecured_profile(changes: dict[str, object]) -> None:
    with pytest.raises((ValueError, ValidationError)):
        WakeEndpoint.model_validate(
            {"url": "https://example.test/hook", "signing_key_file": "/private/signing"} | changes
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, 9, True, float("nan"), float("inf")])
async def test_invalid_deadline_refuses_before_process_spawn(
    tmp_path: Path, timeout: float
) -> None:
    endpoint = WakeEndpoint(url="https://example.test/hook", signing_key_file="/private/signing")
    _, attempt = _ledger(tmp_path / "ledger")
    with pytest.raises(WakeLedgerError, match="invalid wake attempt deadline"):
        await deliver_pointer(endpoint, attempt, timeout=timeout)


@pytest.mark.asyncio
async def test_invalid_pointer_refuses_without_dispatch(tmp_path: Path) -> None:
    endpoint = WakeEndpoint(url="https://example.test/hook", signing_key_file="/private/signing")
    attempt = WakeAttempt("msg_" + "a" * 32, 1, b"\xff", 1, 110)
    with pytest.raises(WakeLedgerError, match="invalid wake attempt input"):
        await deliver_pointer(endpoint, attempt)


@pytest.mark.asyncio
async def test_serialized_pointer_budget_refuses_before_dispatch() -> None:
    endpoint = WakeEndpoint(url="https://example.test/hook", signing_key_file="/private/signing")
    attempt = WakeAttempt("msg_" + "a" * 32, 1, b"\x00" * 16384, 1, 110)
    with pytest.raises(WakeLedgerError, match="invalid wake attempt input"):
        await deliver_pointer(endpoint, attempt)
