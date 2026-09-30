# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real governed attachment ingress and private filesystem tests
"""Exercise scoped attachment bytes through signed hub frames and restart."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect

from hub_e2e_helpers import Recorder, read_json, read_until_type, running_hub
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.acl import (
    ATTACHMENT_ADMIN,
    ATTACHMENT_READ,
    ATTACHMENT_WRITE,
    AclPolicy,
    AclRule,
)
from synapse_channel.core.attachment_store import AttachmentStore
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_keys import sign_registration, write_signing_key
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageAuthKey,
    MessageReplayCache,
    sign_frame,
)
from synapse_channel.core.message_auth_durable import DurableMessageAuthReplayStore
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.role_grants import RoleGrants

SENDER = "proj/alice"
OTHER = "other/bob"
SAME_PROJECT_OTHER = "proj/bob"
KEY = MessageAuthKey("hmac", b"test-secret", frozenset({SENDER, OTHER, SAME_PROJECT_OTHER}))


def _make_hub(
    tmp_path: Path, attachment_store: AttachmentStore
) -> tuple[SynapseHub, EventStore, DurableMessageAuthReplayStore, Ed25519PrivateKey]:
    """Create a real hub with the complete C12 security posture."""
    identity = Ed25519PrivateKey.generate()
    trust = EventSignatureTrustBundle(
        keys={
            "id": EventSignatureKey.from_private_key(
                key_id="id",
                private_key=identity,
                senders=frozenset({SENDER, OTHER, SAME_PROJECT_OTHER}),
            )
        },
        replay_cache=MessageReplayCache(window_seconds=30, max_entries=64),
    )
    journal = EventStore(tmp_path / "hub.db")
    replay = DurableMessageAuthReplayStore(
        tmp_path / "replay.db", max_entries=512, window_seconds=30
    )
    permissions = (ATTACHMENT_READ, ATTACHMENT_WRITE, ATTACHMENT_ADMIN)
    hub = SynapseHub(
        journal=journal,
        attachment_store=attachment_store,
        authenticator=TokenAuthenticator({"test-token": {SENDER, OTHER, SAME_PROJECT_OTHER}}),
        identity_trust_bundle=trust,
        require_identity_binding=True,
        per_message_auth_keys={KEY.key_id: KEY},
        require_per_message_auth=True,
        per_message_auth_replay_store=replay,
        acl_policy=AclPolicy(
            [AclRule(p, "attachment", "*", namespace="proj") for p in permissions]
        ),
        require_acl=True,
        role_grants=RoleGrants({f"proj/{p}": frozenset({SENDER}) for p in permissions}),
    )
    return hub, journal, replay, identity


async def _register(ws: Any, identity: Ed25519PrivateKey, sender: str, *, version: int = 4) -> None:
    """Bind a named socket using its token and Ed25519 registration proof."""
    frame = {
        "sender": sender,
        "type": "heartbeat",
        "target": "System",
        "payload": "online",
        "token": "test-token",
        "protocol_version": version,
    }
    signed = sign_registration(
        frame, private_key=identity, key_id="id", nonce=f"reg-{sender}-{time.time_ns()}", sequence=1
    )
    await ws.send(json.dumps(signed))
    await read_until_type(ws, MessageType.WELCOME)


async def _request(ws: Any, sender: str, kind: str, **fields: Any) -> dict[str, Any]:
    """Send a signed API request and consume its private response."""
    frame = {
        "sender": sender,
        "target": "SynapseHub",
        "type": kind,
        "payload": "",
        "scope": fields.pop("scope", "proj"),
        **fields,
    }
    signed = sign_frame(frame, key=KEY, nonce=f"req-{time.time_ns()}", sequence=1)
    await ws.send(json.dumps(signed))
    while True:
        response = await read_json(ws)
        if response.get("type") == MessageType.ERROR:
            raise AssertionError(response)
        if response.get("type") == MessageType.ATTACHMENT_RESULT:
            return response


async def test_governed_upload_read_preview_reference_and_gc(tmp_path: Path) -> None:
    """A signed authorized socket can transfer bytes; a hash alone cannot."""
    root = tmp_path / "attachments"
    store = AttachmentStore(root)
    hub, journal, replay, identity = _make_hub(tmp_path, store)
    body = b"<script>alert(1)</script>"
    digest = hashlib.sha256(body).hexdigest()
    try:
        async with running_hub(hub) as (_live_hub, uri):
            async with connect(uri) as ws:
                await _register(ws, identity, SENDER)
                began = await _request(
                    ws,
                    SENDER,
                    MessageType.ATTACHMENT_BEGIN,
                    digest=digest,
                    length=len(body),
                    media_type="text/plain",
                    provenance="claim:C12",
                    expires_at=time.time() + 60,
                )
                assert began["ok"] is True
                token = began["upload_id"]
                assert (
                    await _request(
                        ws,
                        SENDER,
                        MessageType.ATTACHMENT_CHUNK,
                        upload_id=token,
                        offset=0,
                        body="%%%",
                    )
                )["error"] == "invalid attachment chunk"
                assert (
                    await _request(
                        ws,
                        SENDER,
                        MessageType.ATTACHMENT_CHUNK,
                        upload_id=token,
                        offset=0,
                        body=7,
                    )
                )["error"] == "invalid attachment chunk"
                assert (
                    await _request(
                        ws,
                        SENDER,
                        MessageType.ATTACHMENT_CHUNK,
                        upload_id=token,
                        offset=0,
                        body=base64.b64encode(body).decode(),
                    )
                )["received"] == len(body)
                committed = await _request(
                    ws, SENDER, MessageType.ATTACHMENT_COMMIT, upload_id=token
                )
                assert committed["metadata"]["digest"] == digest
                assert (
                    await _request(
                        ws,
                        SENDER,
                        MessageType.ATTACHMENT_INFO,
                        digest=digest,
                    )
                )["metadata"]["length"] == len(body)
                assert (
                    await _request(
                        ws,
                        SENDER,
                        MessageType.ATTACHMENT_INFO,
                        digest="../escape",
                    )
                )["ok"] is False
                result = await _request(
                    ws, SENDER, MessageType.ATTACHMENT_READ, digest=digest, offset=0, preview=True
                )
                assert base64.b64decode(result["body"]) == body
                assert result["preview_html"] == "&lt;script&gt;alert(1)&lt;/script&gt;"
                assert result["eof"] is True
                assert "preview_html" not in await _request(
                    ws, SENDER, MessageType.ATTACHMENT_READ, digest=digest, offset=0
                )
                assert (
                    await _request(
                        ws,
                        SENDER,
                        MessageType.ATTACHMENT_GC,
                    )
                )["digests"] == []
                assert (
                    await _request(
                        ws, SENDER, MessageType.ATTACHMENT_REF, digest=digest, ref="claim:C12"
                    )
                )["ok"] is True
                store.db.execute(
                    "UPDATE objects SET expires_at=? WHERE scope=? AND digest=?",
                    (time.time() - 1, "proj", digest),
                )
                assert (await _request(ws, SENDER, MessageType.ATTACHMENT_GC, dry_run=False))[
                    "digests"
                ] == []
                await _request(
                    ws,
                    SENDER,
                    MessageType.ATTACHMENT_REF,
                    digest=digest,
                    ref="claim:C12",
                    remove=True,
                )
                assert (await _request(ws, SENDER, MessageType.ATTACHMENT_GC, dry_run=False))[
                    "digests"
                ] == [digest]
                aborted = await _request(
                    ws,
                    SENDER,
                    MessageType.ATTACHMENT_BEGIN,
                    digest=hashlib.sha256(b"aborted").hexdigest(),
                    length=7,
                    media_type="text/plain",
                    provenance="claim:C12",
                    expires_at=time.time() + 60,
                )
                assert (
                    await _request(
                        ws,
                        SENDER,
                        MessageType.ATTACHMENT_ABORT,
                        upload_id=aborted["upload_id"],
                    )
                )["ok"] is True
                assert (
                    await _request(
                        ws,
                        SENDER,
                        MessageType.ATTACHMENT_COMMIT,
                        upload_id=aborted["upload_id"],
                    )
                )["ok"] is False
                empty_digest = hashlib.sha256(b"").hexdigest()
                empty = await _request(
                    ws,
                    SENDER,
                    MessageType.ATTACHMENT_BEGIN,
                    digest=empty_digest,
                    length=0,
                    media_type="application/octet-stream",
                    provenance="claim:C12",
                    expires_at=time.time() + 60,
                )
                assert (
                    await _request(
                        ws,
                        SENDER,
                        MessageType.ATTACHMENT_COMMIT,
                        upload_id=empty["upload_id"],
                    )
                )["ok"] is True
                binary_preview = await _request(
                    ws,
                    SENDER,
                    MessageType.ATTACHMENT_READ,
                    digest=empty_digest,
                    offset=0,
                    preview=True,
                )
                assert binary_preview["body"] == ""
                assert "preview_html" not in binary_preview
                pending = await _request(
                    ws,
                    SENDER,
                    MessageType.ATTACHMENT_BEGIN,
                    digest=hashlib.sha256(b"disconnect").hexdigest(),
                    length=10,
                    media_type="text/plain",
                    provenance="claim:C12",
                    expires_at=time.time() + 60,
                )
                assert (store.staging / pending["upload_id"]).exists()
            for _ in range(100):
                if store.db.execute("SELECT COUNT(*) FROM uploads").fetchone()[0] == 0:
                    break
                await asyncio.sleep(0.01)
            assert store.db.execute("SELECT COUNT(*) FROM uploads").fetchone()[0] == 0
            assert not (store.staging / pending["upload_id"]).exists()
            async with connect(uri) as ws:
                await _register(ws, identity, SAME_PROJECT_OTHER)
                denied = await _request(
                    ws,
                    SAME_PROJECT_OTHER,
                    MessageType.ATTACHMENT_INFO,
                    digest=digest,
                )
                assert denied["error"] == "attachment access denied"
            async with connect(uri) as ws:
                await _register(ws, identity, OTHER)
                await ws.send(
                    json.dumps(
                        sign_frame(
                            {
                                "sender": OTHER,
                                "type": MessageType.ATTACHMENT_INFO,
                                "scope": "proj",
                                "digest": digest,
                            },
                            key=KEY,
                            nonce="unauthorised",
                            sequence=1,
                        )
                    )
                )
                denied = await read_until_type(ws, MessageType.ERROR)
                assert "access denied" in denied["payload"]
    finally:
        store.close()
        replay.close()
        journal.close()


async def test_attachment_version_scope_and_disabled_gates(tmp_path: Path) -> None:
    """Old peers and cross-project requests never reach an object lookup."""
    store = AttachmentStore(tmp_path / "private")
    hub, journal, replay, identity = _make_hub(tmp_path, store)
    digest = hashlib.sha256(b"secret").hexdigest()
    try:
        async with running_hub(hub) as (_live_hub, uri):
            async with connect(uri) as ws:
                await _register(ws, identity, SENDER, version=3)
                response = await _request(
                    ws,
                    SENDER,
                    MessageType.ATTACHMENT_INFO,
                    digest=digest,
                )
                assert response["error"] == "attachment protocol version four required"
            async with connect(uri) as ws:
                await _register(ws, identity, SENDER)
                response = await _request(
                    ws,
                    SENDER,
                    MessageType.ATTACHMENT_INFO,
                    scope="other",
                    digest=digest,
                )
                assert response["error"] == "attachment access denied"
    finally:
        store.close()
        replay.close()
        journal.close()

    async with running_hub(SynapseHub()) as (_live_hub, uri):
        async with connect(uri) as ws:
            await read_until_type(ws, MessageType.WELCOME)
            await ws.send(
                json.dumps(
                    {
                        "sender": SENDER,
                        "type": "heartbeat",
                        "protocol_version": 4,
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "sender": SENDER,
                        "type": MessageType.ATTACHMENT_INFO,
                        "scope": "proj",
                        "digest": digest,
                    }
                )
            )
            response = await read_until_type(ws, MessageType.ATTACHMENT_RESULT)
            assert response["error"] == "attachments disabled"


async def test_python_agent_attachment_entry_point_reaches_secure_hub(tmp_path: Path) -> None:
    """The public Python client signs and sends through the real guarded socket."""
    store = AttachmentStore(tmp_path / "private")
    hub, journal, replay, identity = _make_hub(tmp_path, store)
    key_path = tmp_path / "identity.pem"
    write_signing_key(key_path, identity)
    try:
        async with running_hub(hub) as (_live_hub, uri):
            recorder = Recorder()
            agent = SynapseAgent(
                SENDER,
                recorder,
                uri=uri,
                token="test-token",
                verbose=False,
                identity_key_path=str(key_path),
                identity_key_id="id",
                per_message_auth_key_id="hmac",
                per_message_auth_secret=b"test-secret",
                machine_identity=False,
            )
            task = asyncio.create_task(agent.connect())
            try:
                assert await agent.wait_until_ready(3)
                assert agent.hub_protocol_version == 6
                await agent.send_attachment(
                    MessageType.ATTACHMENT_BEGIN,
                    scope="proj",
                    digest=hashlib.sha256(b"python").hexdigest(),
                    length=6,
                    media_type="text/plain",
                    provenance="claim:C12",
                    expires_at=time.time() + 60,
                )
                result = await recorder.wait_for(
                    lambda frame: frame.get("type") == MessageType.ATTACHMENT_RESULT
                )
                assert result["ok"] is True
                assert isinstance(result["upload_id"], str)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    finally:
        store.close()
        replay.close()
        journal.close()
