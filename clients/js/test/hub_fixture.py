# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — isolated real hub for JavaScript integration tests

"""Serve an ephemeral authenticated hub until the parent closes stdin."""

import asyncio
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from synapse_channel.core.acl import (
    ATTACHMENT_READ,
    ATTACHMENT_WRITE,
    AclPolicy,
    AclRule,
)
from synapse_channel.core.attachment_store import AttachmentStore
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
)
from synapse_channel.core.message_auth_durable import DurableMessageAuthReplayStore
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.role_grants import RoleGrants


def _attachment_hub(root: Path) -> tuple[SynapseHub, list[Any]]:
    """Provision one ephemeral fully governed C12 Hub for the JS SDK test."""
    import base64

    sender = "proj/js-alice"
    public = base64.b64decode(os.environ["SYNAPSE_TEST_ID_PUBLIC"])
    identity = EventSignatureTrustBundle(
        keys={"js-id": EventSignatureKey("js-id", public, frozenset({sender}))},
        replay_cache=MessageReplayCache(window_seconds=30, max_entries=64),
    )
    journal = EventStore(root / "hub.db")
    replay = DurableMessageAuthReplayStore(root / "replay.db", max_entries=128, window_seconds=30)
    store = AttachmentStore(root / "attachments")
    permissions = (ATTACHMENT_READ, ATTACHMENT_WRITE)
    hub = SynapseHub(
        journal=journal,
        attachment_store=store,
        authenticator=TokenAuthenticator({"integration-only-token": {sender}}),
        identity_trust_bundle=identity,
        require_identity_binding=True,
        per_message_auth_keys={
            "js-hmac": MessageAuthKey(
                "js-hmac", os.environ["SYNAPSE_TEST_HMAC_SECRET"].encode(), frozenset({sender})
            )
        },
        require_per_message_auth=True,
        per_message_auth_replay_store=replay,
        acl_policy=AclPolicy(
            [
                AclRule(permission, "attachment", "proj", namespace="proj")
                for permission in permissions
            ]
        ),
        require_acl=True,
        role_grants=RoleGrants(
            {f"proj/{permission}": frozenset({sender}) for permission in permissions}
        ),
    )
    return hub, [store, replay, journal]


async def main() -> None:
    """Publish readiness and shut down when the test parent disconnects."""
    with tempfile.TemporaryDirectory() as directory:
        if os.environ.get("SYNAPSE_TEST_ATTACHMENT") == "1":
            hub, stores = _attachment_hub(Path(directory))
        else:
            # Strict fencing: every release in the SDK journey must name its lease epoch.
            hub = SynapseHub(
                authenticator=TokenAuthenticator(["integration-only-token"]),
                require_fencing_epoch=True,
            )
            stores = []
        server = asyncio.create_task(hub.serve(host="127.0.0.1", port=0))
        try:
            host, port = await hub.wait_until_serving(timeout=5.0)
            print(json.dumps({"uri": f"ws://{host}:{port}"}), flush=True)
            await asyncio.to_thread(sys.stdin.read)
        finally:
            server.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server
            for store in stores:
                store.close()


if __name__ == "__main__":
    asyncio.run(main())
