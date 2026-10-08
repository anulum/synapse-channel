# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — private recipient-authorised cross-hub attachment reads
"""Compose verified peer trust and source owner grants before any object lookup."""

from __future__ import annotations

import base64
import sqlite3
from typing import TYPE_CHECKING, Any

from synapse_channel.core.attachment_store import AttachmentError
from synapse_channel.core.federation import ScopeGrant
from synapse_channel.core.protocol import MIN_ATTACHMENT_PEER_PROTOCOL_VERSION, MessageType
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.attachment_serving import AttachmentServingPolicy
    from synapse_channel.core.attachment_store import AttachmentStore
    from synapse_channel.core.handler_context import HandlerContext
    from synapse_channel.core.multihub_serving import MultiHubServingPolicy

    class AttachmentPeerContext(HandlerContext, Protocol):
        """Capabilities consumed by attachment peer handlers and their callees."""

        @property
        def attachment_serving_policy(self) -> AttachmentServingPolicy | None:
            """Return the attachment serving policy used by this handler family."""
            ...

        @property
        def attachment_store(self) -> AttachmentStore | None:
            """Return the attachment store used by this handler family."""
            ...

        @property
        def multihub_serving_policy(self) -> MultiHubServingPolicy | None:
            """Return the multihub serving policy used by this handler family."""
            ...


async def handle_attachment_peer(
    hub: AttachmentPeerContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Return metadata or one bounded chunk privately; every refusal has the same shape."""
    result: dict[str, Any] = {"ok": False, "error": "attachment unavailable"}
    scope, digest, action = data.get("scope"), data.get("digest"), data.get("action")
    policy, recipients, store = (
        hub.multihub_serving_policy,
        hub.attachment_serving_policy,
        hub.attachment_store,
    )
    if (
        isinstance(scope, str)
        and isinstance(digest, str)
        and action in ("info", "read")
        and hub.clients.protocol_version_of(sender) >= MIN_ATTACHMENT_PEER_PROTOCOL_VERSION
        and policy is not None
        and recipients is not None
        and store is not None
        and (
            decision := policy.authorise_namespace(
                sender=sender, websocket=websocket, namespace=scope
            )
        ).allowed
        and ScopeGrant("read", scope) in decision.scope
        and recipients.allows(sender, scope, digest)
    ):
        try:
            metadata = store.info(scope, digest)
            if metadata["expires_at"] <= recipients.clock():
                raise AttachmentError("attachment unavailable")
            if action == "info":
                result = {"ok": True, "metadata": metadata}
            else:
                offset = data.get("offset")
                if type(offset) is not int:
                    raise AttachmentError("attachment unavailable")
                chunk, eof = store.read(scope, digest, offset)
                result = {
                    "ok": True,
                    "scope": scope,
                    "digest": digest,
                    "offset": data.get("offset"),
                    "body": base64.b64encode(chunk).decode("ascii"),
                    "eof": eof,
                }
        except (AttachmentError, OSError, sqlite3.DatabaseError, TypeError, ValueError):
            pass
    if store is not None:
        try:
            store.record_peer_read(sender, scope, digest, action, allowed=result["ok"] is True)
        except (AttachmentError, OSError, sqlite3.DatabaseError, TypeError, ValueError):
            result = {"ok": False, "error": "attachment unavailable"}
    await hub.send_json(
        websocket,
        hub.system("", msg_type=MessageType.ATTACHMENT_PEER_RESULT, target=sender, **result),
    )


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.ATTACHMENT_PEER_REQUEST,),
        handler=handle_attachment_peer,
        reply_types=(MessageType.ATTACHMENT_PEER_RESULT,),
        mutates=True,
        replay_protected=False,
        mutation_guarded=False,
        accesses=None,
        event_kinds=(),
        minimum_wire_version=6,
        commands=(),
    ),
)
