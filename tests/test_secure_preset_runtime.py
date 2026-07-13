# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — secure umbrella hub runtime tests
"""Runtime tests proving the effective posture ``synapse hub --secure`` builds."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import socket
import ssl
import subprocess
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect as ws_connect

from cli_processes_helpers import _hub_ns
from synapse_channel import cli, cli_processes
from synapse_channel.client import agent_lifecycle
from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


def _write_acl_policy(tmp_path: Path) -> Path:
    """Write a minimal valid one-rule ACL policy for secure startup."""
    policy = tmp_path / "acl.json"
    policy.write_text(
        '{"rules": [{"permission": "claim", "target_kind": "path", "target_pattern": "src/*"}]}',
        encoding="utf-8",
    )
    return policy


def _write_identity_trust(tmp_path: Path) -> Path:
    """Write an Ed25519 identity trust bundle the loader accepts."""
    raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    )
    path = tmp_path / "identity.json"
    path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "key_id": "k",
                        "public_key": base64.b64encode(raw).decode("ascii"),
                        "senders": ["proj/claude"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_role_grants(tmp_path: Path) -> Path:
    """Write a deny-by-default role-grant store the loader accepts."""
    path = tmp_path / "roles.json"
    path.write_text(
        json.dumps({"grants": {"proj/coordinator": ["proj/claude"]}}),
        encoding="utf-8",
    )
    return path


def _close_runner(coro: Coroutine[Any, Any, None]) -> None:
    """Close a hub coroutine without running a long-lived server."""
    coro.close()


def _secure_args(tmp_path: Path, **overrides: Any) -> Any:
    """Return a hub namespace with complete secure-mode material on disk."""
    base: dict[str, Any] = {
        "secure": True,
        "token": "s3cret",
        "db": "hub.db",
        "identity_trust": str(_write_identity_trust(tmp_path)),
        "role_grants": str(_write_role_grants(tmp_path)),
        "message_auth_key": ["main:shared-secret:ALPHA"],
        "acl_policy": str(_write_acl_policy(tmp_path)),
        "tls_certfile": "cert.pem",
        "tls_keyfile": "key.pem",
        "metrics_query_token_ok": True,
        "insecure_off_loopback": True,
    }
    base.update(overrides)
    return _hub_ns(**base)


def _fake_tls(**_kwargs: Any) -> ssl.SSLContext | None:
    """Stub the TLS context; the secure gate only checks the cert/key paths."""
    return None


def test_parser_hub_secure_switch_defaults_to_off() -> None:
    """The hub parser exposes an explicit secure runtime switch."""
    defaults = cli.build_parser().parse_args(["hub"])
    enabled = cli.build_parser().parse_args(["hub", "--secure"])

    assert defaults.secure is False
    assert enabled.secure is True


def test_cmd_hub_secure_refuses_missing_material_in_one_pass(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Secure startup fails closed and lists every absent input at once."""
    assert cli_processes._cmd_hub(_hub_ns(secure=True), runner=_close_runner) == 2

    err = capsys.readouterr().err
    assert "secure mode requires all production material" in err
    assert "--token" in err
    assert "--tls-certfile" in err


def test_cmd_hub_secure_applies_the_composed_posture(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A complete secure hub composes both profiles and the flood bounds."""
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _secure_args(tmp_path),
            runner=_close_runner,
            hub_factory=build_hub,
            tls_context_factory=_fake_tls,
        )
        == 0
    )

    # Composed paranoid + team-secure gates.
    assert captured["authenticator"] is not None
    assert captured["require_per_message_auth"] is True
    assert captured["require_acl"] is True
    assert captured["require_identity_binding"] is True
    assert captured["require_role_claim"] is True
    assert captured["metrics_query_token_ok"] is False
    assert captured["insecure_off_loopback"] is False
    # Preset flood bounds.
    assert captured["rate_limiter"].rate_per_second == 100.0
    assert captured["rate_limiter"].burst == 20.0
    assert captured["host_rate_limiter"].rate_per_second == 500.0
    assert captured["host_rate_limiter"].burst == 100.0
    assert captured["max_connections_per_host"] == 10

    err = capsys.readouterr().err
    assert "secure mode enforced:" in err
    assert "secure mode effective limits:" in err
    # One consolidated report: no duplicate subordinate profile reports.
    assert "team-secure mode enforced:" not in err
    assert "paranoid mode enforced:" not in err


def test_cmd_hub_secure_preserves_a_stricter_operator_limit(tmp_path: Path) -> None:
    """An operator-configured limit below the ceiling survives the preset."""
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _secure_args(tmp_path, rate=25.0, max_connections_per_host=4),
            runner=_close_runner,
            hub_factory=build_hub,
            tls_context_factory=_fake_tls,
        )
        == 0
    )

    assert captured["rate_limiter"].rate_per_second == 25.0
    assert captured["max_connections_per_host"] == 4


def test_cmd_hub_secure_operator_stricter_limits_reach_the_runtime_exactly(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Parser-to-runtime-to-report parity for operator-stricter rate AND burst.

    The audit reproduced a stricter rate paired with a colossal burst reaching the
    runtime while labelled operator-stricter; both buckets must now carry exactly
    the values the report names.
    """
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _secure_args(tmp_path, rate=25.0, burst=10.0, host_rate=200.0, host_burst=50.0),
            runner=_close_runner,
            hub_factory=build_hub,
            tls_context_factory=_fake_tls,
        )
        == 0
    )

    assert captured["rate_limiter"].rate_per_second == 25.0
    assert captured["rate_limiter"].burst == 10.0
    assert captured["host_rate_limiter"].rate_per_second == 200.0
    assert captured["host_rate_limiter"].burst == 50.0
    err = capsys.readouterr().err
    assert "per-agent 25/s burst 10 (operator-stricter)" in err
    assert "per-host 200/s burst 50 (operator-stricter)" in err


def test_cmd_hub_secure_refuses_an_unbounded_burst(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The exact audit reproducer fails closed before hub construction."""
    assert (
        cli_processes._cmd_hub(
            _secure_args(tmp_path, rate=25.0, burst=1_000_000.0),
            runner=_close_runner,
            tls_context_factory=_fake_tls,
        )
        == 2
    )

    assert "secure mode caps --burst at 20" in capsys.readouterr().err


def test_cmd_hub_secure_refuses_a_limit_above_the_ceiling(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A limit above a preset ceiling fails closed before hub construction."""
    assert (
        cli_processes._cmd_hub(
            _secure_args(tmp_path, max_connections_per_host=50),
            runner=_close_runner,
            tls_context_factory=_fake_tls,
        )
        == 2
    )

    assert "secure mode caps --max-connections-per-host at 10" in capsys.readouterr().err


# --- the real-surface end-to-end proof ---------------------------------------

_E2E_AGENT = "proj/secure-e2e"
_E2E_HMAC_SECRET = "shared-hmac-secret"
_E2E_CHAT = "secure preset end-to-end proof"


def _free_port() -> int:
    """Reserve an ephemeral port for the end-to-end hub."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _write_self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    """Write a one-day self-signed localhost certificate pair with openssl."""
    certfile = tmp_path / "hub-cert.pem"
    keyfile = tmp_path / "hub-key.pem"
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


def _write_e2e_acl_policy(tmp_path: Path) -> Path:
    """Write a deny-by-default policy that grants the E2E client its one chat.

    ``--secure`` enforces the ACL, so the end-to-end broadcast needs an explicit
    ``message`` grant — deny-by-default stays observable for everything else.
    """
    policy = tmp_path / "e2e-acl.json"
    policy.write_text(
        json.dumps(
            {
                "rules": [
                    {"permission": "message", "target_kind": "agent", "target_pattern": "*"},
                    {"permission": "claim", "target_kind": "path", "target_pattern": "src/*"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return policy


def _write_identity_trust_for(
    tmp_path: Path, private_key: Ed25519PrivateKey, *, key_id: str, sender: str
) -> Path:
    """Write a trust bundle whose one key binds ``sender`` to ``private_key``."""
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    path = tmp_path / "e2e-identity-trust.json"
    path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "key_id": key_id,
                        "public_key": base64.b64encode(raw).decode("ascii"),
                        "senders": [sender],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


async def _wait_listening(port: int, timeout: float = 15.0) -> None:
    """Wait until the hub's TLS socket accepts TCP connections."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.05)
            continue
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return
    raise TimeoutError(f"hub never listened on port {port}")


async def _wait_for_stored_chat(db_path: Path, needle: str, timeout: float = 10.0) -> bool:
    """Poll the durable store until a chat event carrying ``needle`` is journaled."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if db_path.exists():
            store = EventStore(db_path)
            try:
                events = store.read_all()
            finally:
                store.close()
            if any(event.kind == "chat" and needle in str(event.payload) for event in events):
                return True
        await asyncio.sleep(0.1)
    return False


def test_cmd_hub_secure_serves_a_real_wss_identity_client_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full secure posture serves a real authenticated client over real WSS.

    This is the boundary proof the unit tests above cannot give: the real
    ``_cmd_hub`` startup path with the real TLS context factory binds a real
    socket, and the real :class:`~synapse_channel.client.agent.SynapseAgent`
    connects over ``wss://`` presenting the connect token, an Ed25519
    identity-signed registration the hub verifies against its trust bundle, and
    an HMAC-signed mutating chat frame — which must land in the durable event
    store. The single test-only seam is the client's TLS context (the
    self-signed test certificate cannot chain to a system root); every other
    element is the production surface.
    """
    certfile, keyfile = _write_self_signed_cert(tmp_path)
    private_key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "e2e-agent-key.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    trust_path = _write_identity_trust_for(tmp_path, private_key, key_id="e2e", sender=_E2E_AGENT)
    port = _free_port()
    db_path = tmp_path / "secure-e2e-hub.db"
    args = _secure_args(
        tmp_path,
        host="127.0.0.1",
        port=port,
        db=str(db_path),
        tls_certfile=str(certfile),
        tls_keyfile=str(keyfile),
        identity_trust=str(trust_path),
        message_auth_key=[f"main:{_E2E_HMAC_SECRET}:{_E2E_AGENT}"],
        acl_policy=str(_write_e2e_acl_policy(tmp_path)),
    )

    # The one test-only seam: trust the self-signed certificate. The context still
    # performs a real TLS handshake against the hub's real certificate. The client
    # opens its socket through ``agent_lifecycle.connect`` (the websockets client),
    # so wrapping that name with the test SSL context reaches the real handshake.
    client_ssl = ssl.create_default_context()
    client_ssl.check_hostname = False
    client_ssl.verify_mode = ssl.CERT_NONE
    monkeypatch.setattr(
        agent_lifecycle,
        "connect",
        lambda uri, **kwargs: ws_connect(uri, ssl=client_ssl, **kwargs),
    )

    outcome: dict[str, bool] = {}

    def e2e_runner(serve_coro: Coroutine[Any, Any, None]) -> None:
        async def orchestrate() -> None:
            serve_task = asyncio.create_task(serve_coro)
            connect_task: asyncio.Task[None] | None = None
            try:
                await _wait_listening(port)
                agent = SynapseAgent(
                    _E2E_AGENT,
                    uri=f"wss://127.0.0.1:{port}",
                    verbose=False,
                    token="s3cret",
                    per_message_auth_key_id="main",
                    per_message_auth_secret=_E2E_HMAC_SECRET,
                    identity_key_path=str(key_path),
                    identity_key_id="e2e",
                )
                connect_task = asyncio.create_task(agent.connect())
                outcome["ready"] = await agent.wait_until_ready(timeout=15.0)
                assert outcome["ready"], "the secure hub never welcomed the signed client"
                await agent.send_message(MessageType.CHAT, target="all", payload=_E2E_CHAT)
                outcome["stored"] = await _wait_for_stored_chat(db_path, _E2E_CHAT)
            finally:
                if connect_task is not None:
                    connect_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await connect_task
                serve_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await serve_task

        asyncio.run(orchestrate())

    assert cli_processes._cmd_hub(args, runner=e2e_runner) == 0
    assert outcome.get("ready") is True
    assert outcome.get("stored") is True

    # Independent post-shutdown read: the HMAC-signed frame is durably journaled.
    store = EventStore(db_path)
    try:
        events = store.read_all()
    finally:
        store.close()
    assert any(event.kind == "chat" and _E2E_CHAT in str(event.payload) for event in events), (
        "the authenticated chat frame must survive in the durable store"
    )
