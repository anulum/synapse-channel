# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cmd_hub authentication, ACL, identity, and TLS wiring

from __future__ import annotations

import asyncio
import json
import ssl
import time
from collections.abc import Coroutine
from pathlib import Path
from ssl import SSLContext
from typing import Any

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import (
    _close_runner,
    _owner_only,
    _write_identity_trust,
)
from synapse_channel import cli_processes
from synapse_channel.core.capability_card_history import PersistentCapabilityCardHistory
from synapse_channel.core.hub import (
    SynapseHub,
)
from synapse_channel.core.identity_keys import generate_signing_key, public_key_b64
from synapse_channel.core.message_auth import (
    MessageAuthKey,
    VerificationResult,
    sign_frame,
    verify_frame,
)
from synapse_channel.core.message_auth_durable import SequenceFloorMode
from synapse_channel.core.protocol import build_envelope


def test_cmd_hub_message_auth_key_file_merges_with_argv_keys(tmp_path: Path) -> None:
    """File entries join argv keys, so both sources can rotate together."""
    key_file = _owner_only(
        tmp_path / "hmac-keys", "# rotation 2026-07-14\nfilekey:filesecret:BETA\n"
    )
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    ns = _hub_ns(
        message_auth_key=["argvkey:argvsecret:ALPHA"],
        message_auth_key_file=str(key_file),
    )
    assert cli_processes._cmd_hub(ns, runner=_close_runner, hub_factory=build_hub) == 0
    key_ids = [key.key_id for key in captured["per_message_auth_keys"]]
    assert key_ids == ["argvkey", "filekey"]


def test_cmd_hub_refuses_a_group_readable_secret_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lax secret file fails closed by flag and path, never by content."""
    token_file = tmp_path / "metrics-token"
    token_file.write_text("leakable-bearer\n", encoding="utf-8")
    token_file.chmod(0o644)

    ns = _hub_ns(metrics=True, metrics_token_file=str(token_file))
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    err = capsys.readouterr().err
    assert "--metrics-token-file" in err
    assert "chmod 600" in err
    assert "leakable-bearer" not in err


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"db": None, "hub_id": "hub.example"}, "requires --db"),
        ({"db": "/tmp/aef.db", "hub_id": None}, "requires --hub-id"),
    ],
)
def test_cmd_hub_aef_route_requires_durable_identity_context(
    overrides: dict[str, object], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    ns = _hub_ns(aef_signing_key="/tmp/unused-aef-key", **overrides)
    assert cli_processes._cmd_hub(ns, runner=_close_runner) == 2
    assert message in capsys.readouterr().err


def test_cmd_hub_threads_message_authentication_options() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                message_auth_key=["main:shared-secret:ALPHA,BETA"],
                require_message_auth=True,
                message_auth_window_seconds=12.5,
                message_auth_replay_capacity=99,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["require_per_message_auth"] is True
    assert captured["per_message_auth_window_seconds"] == 12.5
    assert captured["per_message_auth_replay_capacity"] == 99
    assert captured["per_message_auth_keys"][0].key_id == "main"
    assert captured["per_message_auth_keys"][0].secret == b"shared-secret"
    assert captured["per_message_auth_keys"][0].senders == frozenset({"ALPHA", "BETA"})
    assert captured["per_message_auth_replay_store"] is None
    assert captured["per_message_auth_sequence_floor_mode"] is SequenceFloorMode.OFF


def test_cmd_hub_auto_durable_replay_survives_runtime_restart(tmp_path: Path) -> None:
    """A journalled authenticated hub refuses the same fresh frame after restart."""
    db = tmp_path / "hub.db"
    hubs: list[SynapseHub] = []
    now = time.time()
    key = MessageAuthKey(
        key_id="main",
        secret=b"shared-secret",
        senders=frozenset({"ALPHA"}),
    )
    frame = sign_frame(
        build_envelope("ALPHA", "claim", target="System", task_id="T1", now=now),
        key=key,
        nonce="restart-proof",
        sequence=1,
        timestamp=now,
    )
    outcomes: list[VerificationResult] = []

    def build_hub(**kwargs: Any) -> SynapseHub:
        hub = SynapseHub(**kwargs)
        hubs.append(hub)
        return hub

    def verify_then_close(coro: Coroutine[Any, Any, None]) -> None:
        hub = hubs[-1]
        outcomes.append(
            verify_frame(
                frame,
                keys=hub.per_message_auth_keys,
                replay_cache=hub._message_replay,
                now=now + len(outcomes) * 0.1,
                required_sender="ALPHA",
            )
        )
        coro.close()

    args = _hub_ns(
        db=str(db),
        message_auth_key=["main:shared-secret:ALPHA"],
        require_message_auth=True,
    )
    assert cli_processes._cmd_hub(args, runner=verify_then_close, hub_factory=build_hub) == 0
    assert cli_processes._cmd_hub(args, runner=verify_then_close, hub_factory=build_hub) == 0

    assert outcomes == [VerificationResult.OK, VerificationResult.REPLAYED]
    assert Path(f"{db}.message-auth.db").is_file()


def test_cmd_hub_requires_durable_store_for_sequence_floor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                message_auth_key=["main:shared-secret:ALPHA"],
                require_message_auth=True,
                message_auth_sequence_floor_mode="strict",
            ),
            runner=_close_runner,
        )
        == 2
    )
    assert "requires a durable replay ledger" in capsys.readouterr().err


def test_cmd_hub_rejects_unused_explicit_replay_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli_processes._cmd_hub(
            _hub_ns(message_auth_replay_db=str(tmp_path / "unused.db")),
            runner=_close_runner,
        )
        == 2
    )
    assert "requires --require-message-auth" in capsys.readouterr().err


def test_cmd_hub_rejects_malformed_message_auth_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli_processes._cmd_hub(
            _hub_ns(message_auth_key=["missing-separator"]),
            runner=_close_runner,
        )
        == 2
    )

    assert (
        "--message-auth-key / --message-auth-key-file entries must use "
        "KEY_ID:SECRET:SENDER[,SENDER...]" in capsys.readouterr().err
    )


def test_cmd_hub_threads_acl_policy(tmp_path: Path) -> None:
    policy = tmp_path / "acl.json"
    policy.write_text(
        '{"rules": [{"permission": "claim", "target_kind": "path", "target_pattern": "src/*"}]}',
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(token="t", acl_policy=str(policy), require_acl=True),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["require_acl"] is True
    assert captured["acl_policy"] is not None
    assert len(captured["acl_policy"].rules) == 1


def test_cmd_hub_rejects_malformed_acl_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = tmp_path / "acl.json"
    policy.write_text("{}", encoding="utf-8")
    assert (
        cli_processes._cmd_hub(
            _hub_ns(acl_policy=str(policy), require_acl=True), runner=_close_runner
        )
        == 2
    )
    assert "rules" in capsys.readouterr().err


def test_cmd_hub_warns_on_require_acl_without_token(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_processes._cmd_hub(_hub_ns(token=None, require_acl=True), runner=_close_runner) == 0
    assert "WARNING --require-acl without --token" in capsys.readouterr().err


def test_cmd_hub_threads_tls_context_to_serve() -> None:
    served: dict[str, Any] = {}
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

    class CapturingHub(SynapseHub):
        async def serve(
            self,
            host: str = "localhost",
            port: int = 8876,
            *,
            ssl_context: SSLContext | None = None,
        ) -> None:
            served.update({"host": host, "port": port, "ssl_context": ssl_context})

    assert (
        cli_processes._cmd_hub(
            _hub_ns(tls_certfile="cert.pem", tls_keyfile="key.pem"),
            runner=lambda coro: asyncio.run(coro),
            hub_factory=lambda **kwargs: CapturingHub(**kwargs),
            tls_context_factory=lambda certfile, keyfile: context,
        )
        == 0
    )

    assert served == {"host": "localhost", "port": 8876, "ssl_context": context}


def test_cmd_hub_rejects_incomplete_tls_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_processes._cmd_hub(_hub_ns(tls_certfile="cert.pem"), runner=_close_runner) == 2

    assert "requires both --tls-certfile and --tls-keyfile" in capsys.readouterr().err


def test_parse_message_auth_keys_rejects_empty_fields() -> None:
    """A key with a blank id, secret, or sender list is refused with the format."""
    from synapse_channel.cli_processes_hub import _parse_message_auth_keys

    with pytest.raises(ValueError, match="KEY_ID:SECRET:SENDER"):
        _parse_message_auth_keys([":secret:ALPHA"])
    with pytest.raises(ValueError, match="KEY_ID:SECRET:SENDER"):
        _parse_message_auth_keys(["main:secret:  ,  "])


def test_cmd_hub_no_role_grants_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["role_grants"] is None
    assert captured["require_role_claim"] is False


def test_cmd_hub_threads_role_grants(tmp_path: Path) -> None:
    store = tmp_path / "role-grants.json"
    store.write_text(
        json.dumps({"grants": {"proj/coordinator": ["proj/claude"]}}), encoding="utf-8"
    )
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(role_grants=str(store), require_role_claim=True, token="t"),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["require_role_claim"] is True
    assert captured["role_grants"].may_claim("proj/claude", "proj/coordinator")


def test_cmd_hub_rejects_a_malformed_role_grants_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "role-grants.json"
    store.write_text("{not json", encoding="utf-8")

    assert cli_processes._cmd_hub(_hub_ns(role_grants=str(store)), runner=_close_runner) == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_cmd_hub_warns_on_require_role_claim_without_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_processes._cmd_hub(_hub_ns(require_role_claim=True), runner=_close_runner) == 0
    assert "--require-role-claim without --token" in capsys.readouterr().err


def test_cmd_hub_no_identity_binding_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["identity_trust_bundle"] is None
    assert captured["require_identity_binding"] is False


def test_cmd_hub_threads_identity_trust(tmp_path: Path) -> None:
    trust = tmp_path / "identity-trust.json"
    _write_identity_trust(trust)
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(identity_trust=str(trust), require_identity_binding=True, token="t"),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["require_identity_binding"] is True
    assert "k" in captured["identity_trust_bundle"].keys


def test_cmd_hub_rejects_malformed_identity_trust(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust = tmp_path / "identity-trust.json"
    trust.write_text("{not json", encoding="utf-8")

    assert cli_processes._cmd_hub(_hub_ns(identity_trust=str(trust)), runner=_close_runner) == 2
    assert "invalid identity trust JSON" in capsys.readouterr().err


def test_cmd_hub_require_identity_binding_without_trust_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_processes._cmd_hub(_hub_ns(require_identity_binding=True), runner=_close_runner) == 2
    assert "--require-identity-binding requires --identity-trust" in capsys.readouterr().err


def test_cmd_hub_threads_capability_card_trust(tmp_path: Path) -> None:
    trust = tmp_path / "capability-card-trust.json"
    trust.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "agents": ["P/worker"],
                        "key_id": "P:key",
                        "projects": ["P"],
                        "public_key": public_key_b64(generate_signing_key()),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                capability_card_trust=str(trust),
                capability_card_history_db=str(tmp_path / "card-history.db"),
                capability_card_clock_skew_seconds=4.0,
                capability_card_history_capacity=7,
                capability_card_history_retention_seconds=8.0,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    bundle = captured["capability_card_trust_bundle"]
    assert "P:key" in bundle.keys
    assert bundle.clock_skew_seconds == 4.0
    assert isinstance(bundle.history, PersistentCapabilityCardHistory)
    assert bundle.history.path == tmp_path / "card-history.db"
    assert bundle.history.max_entries == 7
    assert bundle.history.retention_seconds == 8.0


def test_cmd_hub_rejects_malformed_capability_card_trust(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust = tmp_path / "capability-card-trust.json"
    trust.write_text("{not json", encoding="utf-8")

    assert (
        cli_processes._cmd_hub(_hub_ns(capability_card_trust=str(trust)), runner=_close_runner) == 2
    )
    assert "invalid capability-card trust JSON" in capsys.readouterr().err


def test_cmd_hub_threads_private_directed_messages() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(private_directed_messages=True),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["private_directed_messages"] is True


def test_cmd_hub_private_directed_messages_off_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["private_directed_messages"] is False


def test_cmd_hub_threads_stale_recipient_warning_opt_out() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert (
        cli_processes._cmd_hub(
            _hub_ns(
                warn_stale_recipients=False,
                recipient_liveness_window=30.0,
                waiter_liveness_window=15.0,
            ),
            runner=_close_runner,
            hub_factory=build_hub,
        )
        == 0
    )
    assert captured["warn_stale_recipients"] is False
    assert captured["recipient_liveness_window"] == 30.0
    assert captured["waiter_liveness_window"] == 15.0


def test_cmd_hub_stale_recipient_warning_on_by_default() -> None:
    captured: dict[str, Any] = {}

    def build_hub(**kwargs: Any) -> SynapseHub:
        captured.update(kwargs)
        return SynapseHub(**kwargs)

    assert cli_processes._cmd_hub(_hub_ns(), runner=_close_runner, hub_factory=build_hub) == 0
    assert captured["warn_stale_recipients"] is True
