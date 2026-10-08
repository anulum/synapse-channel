# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authorised attachment API over bounded WebSocket frames
"""Attachment frame handler; all policy decisions precede storage lookup."""

from __future__ import annotations

import base64
import binascii
import html
import sqlite3
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import (
    ATTACHMENT_ADMIN,
    ATTACHMENT_READ,
    ATTACHMENT_WRITE,
    WOULD_ALLOW,
    Target,
    evaluate_access,
)
from synapse_channel.core.acl_enforcement import project_of
from synapse_channel.core.attachment_store import MAX_CHUNK_BYTES, AttachmentError
from synapse_channel.core.protocol import MIN_ATTACHMENT_PROTOCOL_VERSION, MessageType

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.acl import AclPolicy
    from synapse_channel.core.attachment_store import AttachmentStore
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.role_grants import RoleGrants

    class AttachmentsContext(HandlerContext, Protocol):
        """Capabilities consumed by attachments handlers and their callees."""

        @property
        def acl_policy(self) -> AclPolicy | None:
            """Return the acl policy used by this handler family."""
            ...

        @property
        def attachment_store(self) -> AttachmentStore | None:
            """Return the attachment store used by this handler family."""
            ...

        @property
        def role_grants(self) -> RoleGrants | None:
            """Return the role grants used by this handler family."""
            ...


_PERMISSIONS = {
    MessageType.ATTACHMENT_BEGIN: ATTACHMENT_WRITE,
    MessageType.ATTACHMENT_CHUNK: ATTACHMENT_WRITE,
    MessageType.ATTACHMENT_COMMIT: ATTACHMENT_WRITE,
    MessageType.ATTACHMENT_ABORT: ATTACHMENT_WRITE,
    MessageType.ATTACHMENT_REF: ATTACHMENT_WRITE,
    MessageType.ATTACHMENT_INFO: ATTACHMENT_READ,
    MessageType.ATTACHMENT_READ: ATTACHMENT_READ,
    MessageType.ATTACHMENT_GC: ATTACHMENT_ADMIN,
}


async def handle_attachment(
    hub: AttachmentsContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Authorise one project-local operation and return a private result frame."""
    kind = str(data["type"])
    scope = data.get("scope")
    result: dict[str, Any] = {"operation": kind, "ok": False}
    try:
        store = hub.attachment_store
        if store is None:
            raise AttachmentError("attachments disabled")
        if hub.clients.protocol_version_of(sender) < MIN_ATTACHMENT_PROTOCOL_VERSION:
            raise AttachmentError("attachment protocol version four required")
        project = project_of(sender)
        if not isinstance(scope, str) or scope != project:
            raise AttachmentError("attachment access denied")
        permission = _PERMISSIONS[kind]
        role = f"{scope}/{permission}"
        grants = hub.role_grants
        policy = hub.acl_policy
        if (
            grants is None
            or not grants.may_claim(sender, role)
            or policy is None
            or (
                evaluate_access(
                    subject=sender,
                    project=scope,
                    permission=permission,
                    target=Target("attachment", scope),
                    policy=policy,
                ).decision
                != WOULD_ALLOW
            )
        ):
            raise AttachmentError("attachment access denied")
        # No digest, upload token, or file is inspected before the above checks.
        session_owner = f"{sender}@{id(websocket)}"
        if kind == MessageType.ATTACHMENT_BEGIN:
            token = store.begin(
                scope=scope,
                sender=session_owner,
                digest=data["digest"],
                length=data["length"],
                media_type=data["media_type"],
                provenance=data["provenance"],
                expires_at=data["expires_at"],
            )
            result.update(upload_id=token)
        elif kind == MessageType.ATTACHMENT_CHUNK:
            encoded = data.get("body")
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_CHUNK_BYTES + 2) // 3):
                raise AttachmentError("invalid attachment chunk")
            try:
                body = base64.b64decode(encoded, validate=True)
            except binascii.Error as exc:
                raise AttachmentError("invalid attachment chunk") from exc
            result["received"] = store.chunk(data["upload_id"], session_owner, data["offset"], body)
        elif kind == MessageType.ATTACHMENT_COMMIT:
            result["metadata"] = store.commit(data["upload_id"], session_owner)
        elif kind == MessageType.ATTACHMENT_ABORT:
            store.abort(data["upload_id"], session_owner)
        elif kind == MessageType.ATTACHMENT_INFO:
            result["metadata"] = store.info(scope, data["digest"])
        elif kind == MessageType.ATTACHMENT_READ:
            digest = data["digest"]
            chunk, eof = store.read(scope, digest, data["offset"])
            result.update(
                body=base64.b64encode(chunk).decode("ascii"),
                eof=eof,
                offset=data.get("offset"),
                digest=digest,
            )
            if data.get("preview") is True and data.get("offset") == 0:
                metadata = store.info(scope, digest)
                if metadata["media_type"] == "text/plain":
                    result["preview_html"] = html.escape(
                        chunk[:512].decode("utf-8", errors="replace")
                    )
        elif kind == MessageType.ATTACHMENT_REF:
            store.reference(scope, data["digest"], data["ref"], remove=data.get("remove") is True)
        elif kind == MessageType.ATTACHMENT_GC:
            result["digests"] = store.gc(scope, dry_run=data.get("dry_run") is not False)
        result["ok"] = True
    except (
        AttachmentError,
        KeyError,
        OSError,
        sqlite3.DatabaseError,
        TypeError,
        ValueError,
    ) as exc:
        result["error"] = str(exc) if isinstance(exc, AttachmentError) else "attachment unavailable"
    await hub.send_json(
        websocket,
        hub.system("", msg_type=MessageType.ATTACHMENT_RESULT, target=sender, **result),
    )
