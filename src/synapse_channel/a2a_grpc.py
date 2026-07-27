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
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_http_protocol import bearer_token_matches
from synapse_channel.core.protocol import loads_bounded

SERVICE_NAME = "synapse.a2a.v1.A2ABridge"
"""Fully-qualified gRPC service name advertised to clients."""

METHOD_SEND = f"/{SERVICE_NAME}/SendMessage"
METHOD_GET = f"/{SERVICE_NAME}/GetTask"

DEFAULT_MAX_GRPC_MESSAGE_BYTES = 1024 * 1024
"""Default receive and send ceiling for one JSON-over-gRPC message."""

DEFAULT_MAX_CONCURRENT_GRPC_RPCS = 32
"""Default number of concurrent gRPC calls admitted by one server."""

DEFAULT_GRPC_TIMEOUT_SECONDS = 30.0
"""Default and maximum call duration for the shipped gRPC client/server policy."""


@dataclass(frozen=True)
class A2AGrpcPolicy:
    """Effective security and resource policy for the optional gRPC binding.

    Parameters
    ----------
    bearer_token : str or None, optional
        Shared bearer required in ``authorization`` metadata. ``None`` retains
        the explicitly unauthenticated profile.
    max_receive_message_bytes : int, optional
        Maximum encoded request size accepted by gRPC.
    max_send_message_bytes : int, optional
        Maximum encoded response size emitted by gRPC.
    max_concurrent_rpcs : int, optional
        Maximum concurrent calls admitted before gRPC returns resource exhaustion.
    max_rpc_seconds : float, optional
        Maximum client-supplied call deadline. Calls without a finite deadline,
        or with a longer deadline, are refused before bridge dispatch.
    """

    bearer_token: str | None = None
    max_receive_message_bytes: int = DEFAULT_MAX_GRPC_MESSAGE_BYTES
    max_send_message_bytes: int = DEFAULT_MAX_GRPC_MESSAGE_BYTES
    max_concurrent_rpcs: int = DEFAULT_MAX_CONCURRENT_GRPC_RPCS
    max_rpc_seconds: float = DEFAULT_GRPC_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        """Reject non-positive resource ceilings before a listener is created."""
        if self.bearer_token is not None and len(self.bearer_token) == 0:
            raise ValueError("bearer_token must not be empty")
        if self.max_receive_message_bytes < 1:
            raise ValueError("max_receive_message_bytes must be >= 1")
        if self.max_send_message_bytes < 1:
            raise ValueError("max_send_message_bytes must be >= 1")
        if self.max_concurrent_rpcs < 1:
            raise ValueError("max_concurrent_rpcs must be >= 1")
        if not math.isfinite(self.max_rpc_seconds) or self.max_rpc_seconds <= 0.0:
            raise ValueError("max_rpc_seconds must be finite and > 0")


class SupportsA2AGrpcOps(Protocol):
    """Bridge surface required by the gRPC handlers."""

    def send_message(
        self,
        payload: JsonMap,
        *,
        protocol_version: str | None = None,
        operation_timeout_seconds: float | None = None,
    ) -> JsonMap:
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


def _json_deserializer(max_message_bytes: int) -> Callable[[bytes], JsonMap]:
    def deserialize(raw: bytes) -> JsonMap:
        if len(raw) > max_message_bytes:
            raise ValueError("gRPC request body is too large")
        data = loads_bounded(raw if raw else b"{}")
        if not isinstance(data, dict):
            raise ValueError("gRPC request body must be a JSON object")
        return data

    return deserialize


def _metadata_authorized(context: Any, token: str) -> bool:
    values = [
        value
        for key, value in context.invocation_metadata()
        if str(key).lower() == "authorization" and isinstance(value, str)
    ]
    return len(values) == 1 and bearer_token_matches(values[0], token)


def _request_allowed(context: Any, policy: A2AGrpcPolicy) -> bool:
    if policy.bearer_token is not None and not _metadata_authorized(context, policy.bearer_token):
        grpc = _require_grpc()
        context.set_code(grpc.StatusCode.UNAUTHENTICATED)
        context.set_details("authentication required")
        return False
    remaining = context.time_remaining()
    deadline_grace = min(0.1, policy.max_rpc_seconds * 0.01)
    if remaining is None or remaining > policy.max_rpc_seconds + deadline_grace:
        grpc = _require_grpc()
        context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
        context.set_details("finite call deadline required")
        return False
    if remaining <= 0.0:
        grpc = _require_grpc()
        context.set_code(grpc.StatusCode.DEADLINE_EXCEEDED)
        context.set_details("call deadline exceeded")
        return False
    return True


def _response_allowed(response: JsonMap, context: Any, policy: A2AGrpcPolicy) -> bool:
    if len(_json_serializer(response)) <= policy.max_send_message_bytes:
        return True
    grpc = _require_grpc()
    context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
    context.set_details("response exceeds configured limit")
    return False


def _send_handler(
    bridge: SupportsA2AGrpcOps,
    policy: A2AGrpcPolicy,
) -> Callable[[JsonMap, Any], JsonMap]:
    def handle(request: JsonMap, context: Any) -> JsonMap:
        if not _request_allowed(context, policy):
            return {}
        try:
            remaining = context.time_remaining()
            if remaining is None or remaining <= 0.0:
                raise TimeoutError
            response = bridge.send_message(
                request,
                protocol_version="1.0",
                operation_timeout_seconds=min(remaining, policy.max_rpc_seconds),
            )
        except TimeoutError:
            grpc = _require_grpc()
            context.set_code(grpc.StatusCode.DEADLINE_EXCEEDED)
            context.set_details("call deadline exceeded")
            return {}
        except (TypeError, ValueError):
            grpc = _require_grpc()
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("request was invalid")
            return {}
        except Exception:
            grpc = _require_grpc()
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("request failed")
            return {}
        return response if _response_allowed(response, context, policy) else {}

    return handle


def _get_handler(
    bridge: SupportsA2AGrpcOps,
    policy: A2AGrpcPolicy,
) -> Callable[[JsonMap, Any], JsonMap]:
    def handle(request: JsonMap, context: Any) -> JsonMap:
        grpc = _require_grpc()
        if not _request_allowed(context, policy):
            return {}
        task_id = str(request.get("id") or request.get("taskId") or "")
        if not task_id:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("task id is required")
            return {}
        history_length = request.get("historyLength")
        try:
            task = bridge.get_task(
                task_id,
                history_length=int(history_length) if history_length is not None else None,
            )
        except TimeoutError:
            context.set_code(grpc.StatusCode.DEADLINE_EXCEEDED)
            context.set_details("call deadline exceeded")
            return {}
        except (TypeError, ValueError):
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("request was invalid")
            return {}
        except Exception:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("request failed")
            return {}
        if task is None:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("task not found")
            return {}
        return task if _response_allowed(task, context, policy) else {}

    return handle


def build_grpc_server_credentials(
    *,
    certfile: str | Path,
    keyfile: str | Path,
    client_ca_file: str | Path | None = None,
) -> Any:
    """Build gRPC TLS or mutual-TLS credentials from validated PEM files.

    Parameters
    ----------
    certfile : str or pathlib.Path
        PEM certificate chain presented by the server.
    keyfile : str or pathlib.Path
        PEM private key matching ``certfile``.
    client_ca_file : str or pathlib.Path or None, optional
        PEM CA roots used to require and verify client certificates.

    Returns
    -------
    grpc.ServerCredentials
        Credentials accepted by :func:`build_a2a_grpc_server`.
    """
    grpc = _require_grpc()
    certificate_chain = Path(certfile).read_bytes()
    private_key = Path(keyfile).read_bytes()
    client_roots = Path(client_ca_file).read_bytes() if client_ca_file is not None else None
    return grpc.ssl_server_credentials(
        ((private_key, certificate_chain),),
        root_certificates=client_roots,
        require_client_auth=client_roots is not None,
    )


def build_a2a_grpc_server(
    bridge: SupportsA2AGrpcOps,
    *,
    host: str = "127.0.0.1",
    port: int = 50051,
    server_credentials: Any | None = None,
    policy: A2AGrpcPolicy | None = None,
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
    policy :
        Authentication, message, concurrency, deadline, and error policy.

    Returns
    -------
    grpc.Server
        A started server; caller must call ``stop`` / ``wait_for_termination``.
    """
    grpc = _require_grpc()
    from concurrent.futures import ThreadPoolExecutor

    effective_policy = policy or A2AGrpcPolicy()
    server = grpc.server(
        ThreadPoolExecutor(max_workers=effective_policy.max_concurrent_rpcs),
        options=(
            ("grpc.max_receive_message_length", effective_policy.max_receive_message_bytes),
            ("grpc.max_send_message_length", effective_policy.max_send_message_bytes),
        ),
        maximum_concurrent_rpcs=effective_policy.max_concurrent_rpcs,
    )
    handlers = {
        "SendMessage": grpc.unary_unary_rpc_method_handler(
            _send_handler(bridge, effective_policy),
            request_deserializer=_json_deserializer(effective_policy.max_receive_message_bytes),
            response_serializer=_json_serializer,
        ),
        "GetTask": grpc.unary_unary_rpc_method_handler(
            _get_handler(bridge, effective_policy),
            request_deserializer=_json_deserializer(effective_policy.max_receive_message_bytes),
            response_serializer=_json_serializer,
        ),
    }
    generic = grpc.method_handlers_generic_handler(SERVICE_NAME, handlers)
    server.add_generic_rpc_handlers((generic,))
    bind = f"{host}:{port}"
    try:
        if server_credentials is not None:
            bound_port = server.add_secure_port(bind, server_credentials)
        else:
            bound_port = server.add_insecure_port(bind)
        if bound_port == 0:
            raise RuntimeError("gRPC listener could not bind")
    except Exception:
        server.stop(grace=None)
        raise
    server.start()
    return server


def serve_a2a_grpc(
    bridge: SupportsA2AGrpcOps,
    *,
    host: str = "127.0.0.1",
    port: int = 50051,
    server_credentials: Any | None = None,
    policy: A2AGrpcPolicy | None = None,
) -> None:
    """Block serving the A2A gRPC binding until the process is interrupted."""
    server = build_a2a_grpc_server(
        bridge,
        host=host,
        port=port,
        server_credentials=server_credentials,
        policy=policy,
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
    bearer_token : str or None, optional
        Shared bearer sent in ``authorization`` request metadata.
    timeout_seconds : float, optional
        Finite deadline applied to every call.
    """

    def __init__(
        self,
        target: str,
        *,
        channel_credentials: Any | None = None,
        bearer_token: str | None = None,
        timeout_seconds: float = DEFAULT_GRPC_TIMEOUT_SECONDS,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0.0:
            raise ValueError("timeout_seconds must be finite and > 0")
        if bearer_token is not None and len(bearer_token) == 0:
            raise ValueError("bearer_token must not be empty")
        grpc = _require_grpc()
        self._grpc = grpc
        self._timeout_seconds = float(timeout_seconds)
        self._metadata = (
            (("authorization", f"Bearer {bearer_token}"),) if bearer_token is not None else ()
        )
        if channel_credentials is not None:
            self._channel = grpc.secure_channel(target, channel_credentials)
        else:
            self._channel = grpc.insecure_channel(target)
        self._send = self._channel.unary_unary(
            METHOD_SEND,
            request_serializer=_json_serializer,
            response_deserializer=_json_deserializer(DEFAULT_MAX_GRPC_MESSAGE_BYTES),
        )
        self._get = self._channel.unary_unary(
            METHOD_GET,
            request_serializer=_json_serializer,
            response_deserializer=_json_deserializer(DEFAULT_MAX_GRPC_MESSAGE_BYTES),
        )

    def send_message(self, payload: JsonMap) -> JsonMap:
        """Call ``SendMessage`` with an A2A send payload."""
        result = self._send(
            payload,
            timeout=self._timeout_seconds,
            metadata=self._metadata,
        )
        if not isinstance(result, dict):
            raise TypeError("SendMessage response must be a JSON object")
        return result

    def get_task(self, task_id: str, *, history_length: int | None = None) -> JsonMap:
        """Call ``GetTask`` for ``task_id``."""
        body: JsonMap = {"id": task_id}
        if history_length is not None:
            body["historyLength"] = history_length
        result = self._get(
            body,
            timeout=self._timeout_seconds,
            metadata=self._metadata,
        )
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
    policy: A2AGrpcPolicy | None = None,
) -> tuple[Any, threading.Thread]:
    """Start the gRPC server on a daemon thread; return ``(server, thread)``."""
    server = build_a2a_grpc_server(
        bridge,
        host=host,
        port=port,
        server_credentials=server_credentials,
        policy=policy,
    )

    def _wait() -> None:
        server.wait_for_termination()

    thread = threading.Thread(target=_wait, name="a2a-grpc", daemon=True)
    thread.start()
    return server, thread
