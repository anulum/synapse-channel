# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — request bounded attachment content from its source hub
"""Negotiate the source protocol and validate each private attachment response."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import ssl
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from synapse_channel.core.attachment_store import (
    MAX_ATTACHMENT_BYTES,
    MAX_CHUNK_BYTES,
    AttachmentError,
    AttachmentStore,
)
from synapse_channel.core.peer_identity import PeerRegistrationSigner, signed
from synapse_channel.core.protocol import (
    MIN_ATTACHMENT_PEER_PROTOCOL_VERSION,
    MessageType,
    build_envelope,
    loads_bounded,
)


async def _receive(socket: ClientConnection, kind: str, source_hub_id: str) -> dict[str, Any]:
    while True:
        raw = await socket.recv()
        frame = loads_bounded(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        if not isinstance(frame, dict) or frame.get("hub_id") != source_hub_id:
            raise AttachmentError("invalid attachment source response")
        if frame.get("type") == kind:
            return frame
        if frame.get("type") in (
            MessageType.ERROR,
            MessageType.AUTH_DENIED,
            MessageType.NAME_CONFLICT,
        ):
            raise AttachmentError("attachment source refused the connection")


def _validate_result(
    frame: dict[str, Any], action: str, scope: str, digest: str, offset: int | None
) -> dict[str, Any]:
    if frame.get("ok") is not True:
        raise AttachmentError("attachment unavailable")
    if action == "info":
        metadata = frame.get("metadata")
        if not isinstance(metadata, dict) or set(metadata) != {
            "scope",
            "digest",
            "length",
            "media_type",
            "provenance",
            "expires_at",
        }:
            raise AttachmentError("invalid attachment source response")
        length, expiry = metadata["length"], metadata["expires_at"]
        if (
            metadata["scope"] != scope
            or metadata["digest"] != digest
            or type(length) is not int
            or not 0 <= length <= MAX_ATTACHMENT_BYTES
            or isinstance(expiry, bool)
            or not isinstance(expiry, (int, float))
            or not math.isfinite(expiry)
            or not isinstance(metadata["media_type"], str)
            or not isinstance(metadata["provenance"], str)
        ):
            raise AttachmentError("invalid attachment source response")
        return dict(metadata)
    encoded = frame.get("body")
    if (
        frame.get("scope") != scope
        or frame.get("digest") != digest
        or type(frame.get("offset")) is not int
        or frame["offset"] != offset
        or type(frame.get("eof")) is not bool
        or not isinstance(encoded, str)
        or len(encoded) > 4 * ((MAX_CHUNK_BYTES + 2) // 3)
    ):
        raise AttachmentError("invalid attachment source response")
    try:
        body = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise AttachmentError("invalid attachment source response") from exc
    if len(body) > MAX_CHUNK_BYTES or (not body and not frame["eof"]):
        raise AttachmentError("invalid attachment source response")
    return {"scope": scope, "digest": digest, "offset": offset, "body": body, "eof": frame["eof"]}


async def request_attachment(
    action: str,
    *,
    uri: str,
    local_id: str,
    source_hub_id: str,
    scope: str,
    digest: str,
    offset: int | None = None,
    token: str | None = None,
    signer: PeerRegistrationSigner | None = None,
    ssl_context: ssl.SSLContext | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Read metadata or one chunk after negotiating the named source hub's wire version.

    Parameters
    ----------
    action : str
        ``info`` returns source metadata; ``read`` returns bytes, offset and EOF.
    uri : str
        Source WebSocket URI. Use ``wss`` and a verifying SSL context across hosts.
    local_id : str
        Exact recipient hub id configured by the source owner.
    source_hub_id : str
        Expected source hub id, checked on every received frame.
    scope, digest : str
        Exact project and canonical SHA-256 digest granted by the source owner.
    offset : int or None
        Byte offset for a chunk read; absent for metadata.
    token : str or None
        Source connection credential.
    signer : PeerRegistrationSigner or None
        Identity registration proof for an identity-key serving grant.
    ssl_context : ssl.SSLContext or None
        Verifying TLS context, including the recipient client certificate for mTLS.
    timeout : float
        Finite positive deadline covering connect, registration and the request.

    Returns
    -------
    dict[str, Any]
        Validated metadata or one bounded chunk. Callers verify the complete digest
        before committing assembled bytes to their local attachment store.

    Raises
    ------
    AttachmentError
        For invalid arguments, unavailable grants, incompatible peers or transport faults.
    """
    AttachmentStore.validate_scope(scope)
    AttachmentStore.validate_digest(digest)
    if (
        action not in ("info", "read")
        or not math.isfinite(timeout)
        or timeout <= 0
        or (
            action == "read"
            and (type(offset) is not int or not 0 <= offset <= MAX_ATTACHMENT_BYTES)
        )
        or (action == "info" and offset is not None)
    ):
        raise AttachmentError("invalid attachment peer request")

    async def exchange() -> dict[str, Any]:
        options: dict[str, Any] = {"max_size": 131_072, "open_timeout": timeout}
        if ssl_context is not None:
            options["ssl"] = ssl_context
        async with connect(uri, **options) as socket:
            fields: dict[str, Any] = {"protocol_version": MIN_ATTACHMENT_PEER_PROTOCOL_VERSION}
            if token is not None:
                fields["token"] = token
            registration = signed(build_envelope(local_id, MessageType.HEARTBEAT, **fields), signer)
            await socket.send(json.dumps(registration))
            welcome = await _receive(socket, MessageType.WELCOME, source_hub_id)
            version = welcome.get("protocol_version")
            if type(version) is not int or version < MIN_ATTACHMENT_PEER_PROTOCOL_VERSION:
                raise AttachmentError("attachment peer protocol version six required")
            request = build_envelope(
                local_id,
                MessageType.ATTACHMENT_PEER_REQUEST,
                action=action,
                scope=scope,
                digest=digest,
                offset=offset,
            )
            await socket.send(json.dumps(request))
            result = await _receive(socket, MessageType.ATTACHMENT_PEER_RESULT, source_hub_id)
            if result.get("target") != local_id:
                raise AttachmentError("invalid attachment source response")
            return _validate_result(result, action, scope, digest, offset)

    try:
        return await asyncio.wait_for(exchange(), timeout)
    except asyncio.TimeoutError as exc:
        raise AttachmentError("attachment source request timed out") from exc
    except (OSError, WebSocketException, ValueError, TypeError, OverflowError) as exc:
        if isinstance(exc, AttachmentError):
            raise
        raise AttachmentError("attachment source request failed") from exc
