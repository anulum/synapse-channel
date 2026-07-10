# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real webhook receiver tests for A2A push delivery

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from synapse_channel import a2a_push
from synapse_channel.a2a_validation import A2A_MEDIA_TYPE


def test_real_https_webhook_receiver_accepts_push_with_local_validation_policy(
    tmp_path: Path,
) -> None:
    certfile, keyfile = _write_localhost_cert(tmp_path)
    with _WebhookReceiver(certfile=certfile, keyfile=keyfile) as receiver:
        client = a2a_push.WebhookDeliveryClient(
            allow_local_targets=True,
            ca_file=str(certfile),
            timeout_seconds=2.0,
        )
        client(
            {
                "url": f"{receiver.url}/hook",
                "headers": {"Authorization": "Bearer push-token"},
                "payload": {
                    "task": {"id": "task-real", "status": {"state": "TASK_STATE_COMPLETED"}}
                },
            }
        )

    assert len(receiver.requests) == 1
    received = receiver.requests[0]
    assert received["method"] == "POST"
    assert received["path"] == "/hook"
    assert received["headers"]["Authorization"] == "Bearer push-token"
    assert received["headers"]["Content-Type"] == A2A_MEDIA_TYPE
    assert json.loads(received["body"].decode("utf-8")) == {
        "task": {"id": "task-real", "status": {"state": "TASK_STATE_COMPLETED"}}
    }


def test_real_reverse_proxy_redirect_preserves_post_to_https_receiver(
    tmp_path: Path,
) -> None:
    certfile, keyfile = _write_localhost_cert(tmp_path)
    with _WebhookReceiver(certfile=certfile, keyfile=keyfile) as receiver:
        with _RedirectProxy(location=f"{receiver.url}/proxied") as proxy:
            client = a2a_push.WebhookDeliveryClient(
                allow_local_targets=True,
                ca_file=str(certfile),
                timeout_seconds=2.0,
            )
            client(
                {
                    "url": f"{proxy.url}/start",
                    "headers": {},
                    "payload": {"task": {"id": "task-proxy"}},
                }
            )

    assert proxy.requests == [{"method": "POST", "path": "/start"}]
    assert len(receiver.requests) == 1
    assert receiver.requests[0]["method"] == "POST"
    assert receiver.requests[0]["path"] == "/proxied"
    assert json.loads(receiver.requests[0]["body"].decode("utf-8")) == {
        "task": {"id": "task-proxy"}
    }


def test_default_webhook_delivery_client_still_blocks_local_receivers() -> None:
    with _WebhookReceiver() as receiver:
        with pytest.raises(URLError, match="must not target local networks"):
            a2a_push.WebhookDeliveryClient()(
                {"url": f"{receiver.url}/hook", "headers": {}, "payload": {"task": {}}}
            )

    assert receiver.requests == []


def test_webhook_delivery_rechecks_dns_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _WebhookReceiver() as receiver:
        original_getaddrinfo = socket.getaddrinfo

        def resolve_rebound_host(
            host: str,
            port: int,
            *_args: object,
            **_kwargs: object,
        ) -> list[Any]:
            if host == "rebind.example":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
            return original_getaddrinfo(host, port, type=socket.SOCK_STREAM)

        monkeypatch.setattr(
            "synapse_channel.a2a_push.socket.getaddrinfo",
            resolve_rebound_host,
        )
        with pytest.raises(URLError, match="must not target local networks"):
            a2a_push.WebhookDeliveryClient()(
                {
                    "url": f"http://rebind.example:{receiver.port}/hook",
                    "headers": {},
                    "payload": {"task": {"id": "task-rebind"}},
                }
            )

    assert receiver.requests == []


class _WebhookReceiver:
    def __init__(self, *, certfile: Path | None = None, keyfile: Path | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.port = _free_port()
        self._scheme = "https" if certfile is not None else "http"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                owner._record(self)
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        if certfile is not None and keyfile is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            # PROTOCOL_TLS_SERVER still negotiates the deprecated TLS 1.0/1.1 by
            # default; pin the floor to 1.2 so even this test receiver never
            # offers an insecure protocol (CodeQL py/insecure-protocol).
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(certfile, keyfile)
            self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"{self._scheme}://localhost:{self.port}"

    def __enter__(self) -> _WebhookReceiver:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)

    def _record(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length") or "0")
        self.requests.append(
            {
                "method": handler.command,
                "path": handler.path,
                "headers": {str(key): str(value) for key, value in handler.headers.items()},
                "body": handler.rfile.read(length) if length else b"",
            }
        )


class _RedirectProxy:
    def __init__(self, *, location: str) -> None:
        self.location = location
        self.port = _free_port()
        self.requests: list[dict[str, str]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                owner.requests.append({"method": self.command, "path": self.path})
                self.send_response(HTTPStatus.TEMPORARY_REDIRECT)
                self.send_header("Location", owner.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def __enter__(self) -> _RedirectProxy:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_localhost_cert(tmp_path: Path) -> tuple[Path, Path]:
    certfile = tmp_path / "webhook-cert.pem"
    keyfile = tmp_path / "webhook-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(keyfile),
            "-out",
            str(certfile),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return certfile, keyfile
