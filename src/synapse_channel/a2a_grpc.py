# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — optional gRPC binding for the A2A bridge
"""gRPC transport for representative A2A message-send and task-get operations.

Uses JSON payloads over gRPC with explicit request/response serializers so the
core package does not require checked-in protobuf stubs. The optional
``grpcio`` dependency is imported lazily; install with
``pip install synapse-channel[a2a-grpc]`` (or ``grpcio`` directly).

Service: ``synapse.a2a.v1.A2ABridge``
Methods: ``SendMessage``, ``GetTask``
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any, Protocol

from synapse_channel.a2a import JsonMap

SERVICE_NAME = "synapse.a2a.v1.A2ABridge"
"""Fully-qualified gRPC service name advertised to clients."""

METHOD_SEND = f"/{SERVICE_NAME}/SendMessage"
METHOD_GET = f"/{SERVICE_NAME}/GetTask"


class SupportsA2AGrpcOps(Protocol):
    """Bridge surface required by the gRPC handlers."""

    def send_message(self, payload: JsonMap, *, protocol_version: str | None = None) -> JsonMap:
        """Handle message send."""

    def get_task(self, task_id: str, *, history_length: int | None = None) -> JsonMap | None:
        """Look up one task."""


def grpc_available() -> bool:
    """Return whether the optional ``grpc`` package is importable."""
    try:
        import importlib

        importlib.import_module("grpc")
    except ImportError:
        return False
    return True


def _require_grpc() -> Any:
    try:
        import importlib

        return importlib.import_module("grpc")
    except ImportError as exc:  # pragma: no cover - exercised via availability helper
        raise RuntimeError(
            "gRPC A2A binding requires grpcio; install with "
            "pip install 'synapse-channel[a2a-grpc]' or pip install grpcio"
        ) from exc


def _json_serializer(value: JsonMap) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_deserializer(raw: bytes) -> JsonMap:
    data = json.loads(raw.decode("utf-8") if raw else "{}")
    if not isinstance(data, dict):
        raise ValueError("gRPC request body must be a JSON object")
    return data


def _send_handler(bridge: SupportsA2AGrpcOps) -> Callable[[JsonMap, Any], JsonMap]:
    def handle(request: JsonMap, context: Any) -> JsonMap:
        try:
            return bridge.send_message(request, protocol_version="1.0")
        except Exception as exc:  # map to gRPC error without leaking stack
            grpc = _require_grpc()
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return {}

    return handle


def _get_handler(bridge: SupportsA2AGrpcOps) -> Callable[[JsonMap, Any], JsonMap]:
    def handle(request: JsonMap, context: Any) -> JsonMap:
        grpc = _require_grpc()
        task_id = str(request.get("id") or request.get("taskId") or "")
        if not task_id:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("task id is required")
            return {}
        history_length = request.get("historyLength")
        task = bridge.get_task(
            task_id,
            history_length=int(history_length) if history_length is not None else None,
        )
        if task is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Unknown task: {task_id}")
            return {}
        return task

    return handle


def build_a2a_grpc_server(
    bridge: SupportsA2AGrpcOps,
    *,
    host: str = "127.0.0.1",
    port: int = 50051,
    server_credentials: Any | None = None,
) -> Any:
    """Build and start a gRPC server exposing SendMessage and GetTask.

    Parameters
    ----------
    bridge :
        Production :class:`~synapse_channel.a2a_server.A2ABridge` (or test double).
    host, port :
        Bind address.
    server_credentials :
        Optional ``grpc.ServerCredentials`` for TLS/mTLS.

    Returns
    -------
    grpc.Server
        A started server; caller must call ``stop`` / ``wait_for_termination``.
    """
    grpc = _require_grpc()
    from concurrent.futures import ThreadPoolExecutor

    server = grpc.server(ThreadPoolExecutor(max_workers=8))
    handlers = {
        "SendMessage": grpc.unary_unary_rpc_method_handler(
            _send_handler(bridge),
            request_deserializer=_json_deserializer,
            response_serializer=_json_serializer,
        ),
        "GetTask": grpc.unary_unary_rpc_method_handler(
            _get_handler(bridge),
            request_deserializer=_json_deserializer,
            response_serializer=_json_serializer,
        ),
    }
    generic = grpc.method_handlers_generic_handler(SERVICE_NAME, handlers)
    server.add_generic_rpc_handlers((generic,))
    bind = f"{host}:{port}"
    if server_credentials is not None:
        server.add_secure_port(bind, server_credentials)
    else:
        server.add_insecure_port(bind)
    server.start()
    return server


def serve_a2a_grpc(
    bridge: SupportsA2AGrpcOps,
    *,
    host: str = "127.0.0.1",
    port: int = 50051,
    server_credentials: Any | None = None,
) -> None:
    """Block serving the A2A gRPC binding until the process is interrupted."""
    server = build_a2a_grpc_server(
        bridge, host=host, port=port, server_credentials=server_credentials
    )
    try:
        server.wait_for_termination()
    finally:
        server.stop(grace=None)


class A2AGrpcClient:
    """Outbound client for the SYNAPSE A2A gRPC binding.

    Parameters
    ----------
    target : str
        ``host:port`` of the gRPC peer.
    channel_credentials : object or None, optional
        Optional ``grpc.ChannelCredentials`` for TLS.
    """

    def __init__(self, target: str, *, channel_credentials: Any | None = None) -> None:
        grpc = _require_grpc()
        self._grpc = grpc
        if channel_credentials is not None:
            self._channel = grpc.secure_channel(target, channel_credentials)
        else:
            self._channel = grpc.insecure_channel(target)
        self._send = self._channel.unary_unary(
            METHOD_SEND,
            request_serializer=_json_serializer,
            response_deserializer=_json_deserializer,
        )
        self._get = self._channel.unary_unary(
            METHOD_GET,
            request_serializer=_json_serializer,
            response_deserializer=_json_deserializer,
        )

    def send_message(self, payload: JsonMap) -> JsonMap:
        """Call ``SendMessage`` with an A2A send payload."""
        result = self._send(payload)
        if not isinstance(result, dict):
            raise TypeError("SendMessage response must be a JSON object")
        return result

    def get_task(self, task_id: str, *, history_length: int | None = None) -> JsonMap:
        """Call ``GetTask`` for ``task_id``."""
        body: JsonMap = {"id": task_id}
        if history_length is not None:
            body["historyLength"] = history_length
        result = self._get(body)
        if not isinstance(result, dict):
            raise TypeError("GetTask response must be a JSON object")
        return result

    def close(self) -> None:
        """Close the underlying channel."""
        self._channel.close()

    def __enter__(self) -> A2AGrpcClient:
        """Enter the client context manager."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Exit the client context manager and close the channel."""
        self.close()


def start_grpc_in_background(
    bridge: SupportsA2AGrpcOps,
    *,
    host: str,
    port: int,
    server_credentials: Any | None = None,
) -> tuple[Any, threading.Thread]:
    """Start the gRPC server on a daemon thread; return ``(server, thread)``."""
    server = build_a2a_grpc_server(
        bridge, host=host, port=port, server_credentials=server_credentials
    )

    def _wait() -> None:
        server.wait_for_termination()

    thread = threading.Thread(target=_wait, name="a2a-grpc", daemon=True)
    thread.start()
    return server, thread
