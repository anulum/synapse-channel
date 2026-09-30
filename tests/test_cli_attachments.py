# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — attachment CLI activation refuses an ungoverned hub
"""The public Hub parser exposes C12 only behind its complete posture."""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner
from hub_e2e_helpers import _await_listening, _free_port
from multihub_tls_helpers import certificate_authority, issue_identity
from synapse_channel import cli_processes
from synapse_channel.cli import build_parser
from synapse_channel.core.attachment_transport import request_attachment
from synapse_channel.core.federation import FederationPeer, ScopeGrant
from synapse_channel.core.federation_store import FederationRecord, PeerProvenance, save_store
from test_attachment_peer_e2e import BODY, DIGEST, RECIPIENT, SCOPE, SOURCE, Source
from test_attachment_peer_e2e import source as source


def test_attachment_root_parser_and_fail_closed_startup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The opt-in path is parsed but never created under the default open posture."""
    root = tmp_path / "attachments"
    args = build_parser().parse_args(["hub", "--attachment-root", str(root)])
    assert args.attachment_root == str(root)
    ns = _hub_ns(attachment_root=str(root))
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert not root.exists()
    assert "--attachment-root requires" in capsys.readouterr().err


def test_recipient_policy_parser_and_required_source_features(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recipient policy requires both source storage and verified peer serving."""
    policy = tmp_path / "recipients.json"
    args = build_parser().parse_args(["hub", "--attachment-recipient-policy", str(policy)])
    assert args.attachment_recipient_policy == str(policy)
    ns = _hub_ns(attachment_recipient_policy=str(policy))
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert capsys.readouterr().err.strip() == (
        "synapse hub: attachment recipient policy unavailable or invalid"
    )


@pytest.mark.real_hub
async def test_cli_recipient_policy_serves_the_governed_source_over_real_tls(
    source: Source, tmp_path: Path
) -> None:
    """The public CLI loads the complete source posture and serves one exact peer grant."""
    ca_key, ca_cert = certificate_authority("cli-attachment-ca")
    ca = tmp_path / "cli-ca.pem"
    ca.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    ca.chmod(0o600)
    server = issue_identity(tmp_path, "cli-server", ca_key=ca_key, ca_cert=ca_cert, server=True)
    federation = tmp_path / "cli-federation.json"
    save_store(
        federation,
        [
            FederationRecord(
                FederationPeer(
                    domain_id="recipient-domain",
                    namespaces=frozenset({SCOPE}),
                    signing_key_ids=frozenset({"peer-key"}),
                    scope_grants=(ScopeGrant("read", SCOPE),),
                ),
                PeerProvenance("ceremony", time.time(), "operator"),
            )
        ],
    )
    trust = source.hub.identity_trust_bundle
    assert trust is not None
    files: dict[str, object] = {
        "cli-serving.json": {
            "version": 1,
            "federation_store": federation.name,
            "client_ca_file": ca.name,
            "grants": [
                {
                    "sender": RECIPIENT,
                    "domain_id": "recipient-domain",
                    "namespace": SCOPE,
                    "signing_key_id": "peer-key",
                    "identity_key_id": "identity",
                }
            ],
        },
        "cli-identity.json": {
            "keys": [
                {
                    "key_id": "identity",
                    "public_key": base64.b64encode(trust.keys["identity"].public_key).decode(),
                    "senders": [RECIPIENT, "PROJECT/alice"],
                }
            ]
        },
        "cli-acl.json": {
            "rules": [
                {
                    "permission": "attachment-read",
                    "target_kind": "attachment",
                    "target_pattern": "*",
                    "namespace": SCOPE,
                }
            ]
        },
        "cli-roles.json": {"grants": {"PROJECT/attachment-read": ["PROJECT/alice"]}},
    }
    for name, document in files.items():
        path = tmp_path / name
        path.write_text(json.dumps(document), encoding="utf-8")
        path.chmod(0o600)
    token = tmp_path / "cli-token"
    token.write_text("test-token", encoding="utf-8")
    token.chmod(0o600)
    auth = tmp_path / "cli-message-auth"
    auth.write_text("hmac:test-peer-key:PROJECT/alice,hub-recipient", encoding="utf-8")
    auth.chmod(0o600)
    root = source.store.root
    source.store.close()
    port = _free_port()
    command = [
        str(Path(sys.executable).parent / "synapse"),
        "hub",
        "--host",
        "localhost",
        "--port",
        str(port),
        "--hub-id",
        SOURCE,
        "--db",
        str(tmp_path / "cli-events.db"),
        "--token-file",
        str(token),
        "--identity-trust",
        str(tmp_path / "cli-identity.json"),
        "--require-identity-binding",
        "--message-auth-key-file",
        str(auth),
        "--require-message-auth",
        "--message-auth-replay-db",
        str(tmp_path / "cli-replay.db"),
        "--acl-policy",
        str(tmp_path / "cli-acl.json"),
        "--require-acl",
        "--role-grants",
        str(tmp_path / "cli-roles.json"),
        "--multihub-serving-policy",
        str(tmp_path / "cli-serving.json"),
        "--attachment-root",
        str(root),
        "--attachment-recipient-policy",
        str(source.policy_path),
        "--tls-certfile",
        str(server.cert),
        "--tls-keyfile",
        str(server.key),
        "--log-level",
        "ERROR",
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await _await_listening(port, timeout=15)
        context = ssl.create_default_context(cafile=str(ca))
        metadata = await request_attachment(
            "info",
            uri=f"wss://localhost:{port}",
            local_id=RECIPIENT,
            source_hub_id=SOURCE,
            scope=SCOPE,
            digest=DIGEST,
            token="test-token",
            signer=source.signer,
            ssl_context=context,
        )
        assert metadata["length"] == len(BODY)
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 5)
        except asyncio.TimeoutError:
            process.kill()
            _, stderr = await process.communicate()
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")[-4096:]
