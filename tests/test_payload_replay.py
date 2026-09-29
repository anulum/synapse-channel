# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — K3-F5: a replayed encrypted envelope is opened once
"""Version 2 envelopes bind a replay identity; the receiver's ledger opens each once.

Reproduced before the change on a real hub: the same captured encrypted chat,
sent twice on the same route, decrypted twice at the listener. These tests drive
the real envelope code, a real SQLite ledger and, at the end, a real hub and the
``listen`` command.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import stat
import time
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import cli_messaging
from synapse_channel.cli_messaging_listen import _render_chat_payload
from synapse_channel.core.at_rest import NONCE_BYTES, require_aes_gcm
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.payload_crypto import (
    LEGACY_PAYLOAD_ENVELOPE_VERSION,
    PayloadContext,
    PayloadCryptoError,
    authenticate_payload,
    decrypt_payload,
    encrypt_payload,
)
from synapse_channel.core.payload_replay import (
    DEFAULT_PAYLOAD_FUTURE_SKEW_SECONDS,
    DEFAULT_PAYLOAD_REPLAY_CAPACITY,
    DEFAULT_PAYLOAD_REPLAY_WINDOW_SECONDS,
    PayloadReplayError,
    PayloadReplayGuard,
    default_payload_replay_ledger,
    open_payload,
)
from synapse_channel.core.protocol import MessageType

KEY = b"k" * 32
CONTEXT = PayloadContext(message_type=MessageType.CHAT, sender="PEER", target="LISTENER")
NOW = 1_800_000_000.0


def _envelope(text: str = "deploy", *, created_at_ms: int | None = None) -> dict[str, Any]:
    return dict(
        encrypt_payload(
            text,
            KEY,
            key_id="k1",
            recipients=["LISTENER"],
            context=CONTEXT,
            created_at_ms=int(NOW * 1000) if created_at_ms is None else created_at_ms,
        )
    )


def _legacy_envelope(text: str) -> dict[str, Any]:
    """Build a version 1 envelope exactly as a client before K3-F5 did."""
    aad = json.dumps(
        {
            "channel": "",
            "message_type": MessageType.CHAT,
            "recipients": ["LISTENER"],
            "sender": "PEER",
            "target": "LISTENER",
            "task_id": "",
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    nonce = b"\x01" * NONCE_BYTES
    ciphertext = require_aes_gcm()(KEY).encrypt(nonce, text.encode("utf-8"), aad)
    return {
        "version": 1,
        "key_id": "k1",
        "recipients": ["LISTENER"],
        "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
        "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "aad": base64.urlsafe_b64encode(aad).decode("ascii"),
    }


def _guard(tmp_path: Path, **kwargs: Any) -> PayloadReplayGuard:
    return PayloadReplayGuard(tmp_path / "ledger" / "replay.db", **kwargs)


def test_version_two_binds_the_replay_identity_into_the_aad() -> None:
    envelope = _envelope()
    opened = authenticate_payload(envelope, KEY, context=CONTEXT)
    assert (opened.plaintext, opened.version) == ("deploy", 2)
    assert opened.message_id == envelope["message_id"]
    assert opened.created_at_ms == int(NOW * 1000)
    for field, value in (
        ("message_id", "0" * 32),
        ("created_at_ms", envelope["created_at_ms"] + 1),
        ("key_id", "k2"),
    ):
        moved = {**envelope, field: value}
        with pytest.raises(PayloadCryptoError, match="does not match encrypted payload aad"):
            decrypt_payload(moved, KEY, context=CONTEXT)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("message_id", "XYZ", "message_id must be 32 lowercase hex"),
        ("message_id", None, "message_id must be 32 lowercase hex"),
        ("created_at_ms", -1, "created_at_ms must be a nonnegative integer"),
        ("created_at_ms", True, "created_at_ms must be a nonnegative integer"),
        ("created_at_ms", 1.5, "created_at_ms must be a nonnegative integer"),
        ("version", True, "unsupported encrypted payload version"),
    ],
)
def test_a_malformed_version_two_identity_is_refused(field: str, value: Any, message: str) -> None:
    with pytest.raises(PayloadCryptoError, match=message):
        decrypt_payload({**_envelope(), field: value}, KEY, context=CONTEXT)


def test_encryption_refuses_an_invalid_identity_and_draws_a_fresh_one() -> None:
    with pytest.raises(PayloadCryptoError, match="message_id"):
        encrypt_payload("x", KEY, key_id="k1", recipients=[], context=CONTEXT, message_id="A" * 32)
    with pytest.raises(PayloadCryptoError, match="created_at_ms"):
        encrypt_payload("x", KEY, key_id="k1", recipients=[], context=CONTEXT, created_at_ms=-5)
    first = encrypt_payload("x", KEY, key_id="k1", recipients=[], context=CONTEXT)
    second = encrypt_payload("x", KEY, key_id="k1", recipients=[], context=CONTEXT)
    assert first["message_id"] != second["message_id"]
    assert abs(first["created_at_ms"] / 1000 - time.time()) < 60
    fixed = encrypt_payload(
        "x", KEY, key_id="k1", recipients=[], context=CONTEXT, message_id="a" * 32
    )
    assert fixed["message_id"] == "a" * 32


def test_a_version_one_envelope_still_decrypts() -> None:
    legacy = _legacy_envelope("old client")
    assert decrypt_payload(legacy, KEY, context=CONTEXT) == "old client"
    opened = authenticate_payload(legacy, KEY, context=CONTEXT)
    assert (opened.version, opened.message_id, opened.created_at_ms) == (
        LEGACY_PAYLOAD_ENVELOPE_VERSION,
        None,
        None,
    )


def test_the_ledger_opens_an_envelope_once_even_after_a_restart(tmp_path: Path) -> None:
    envelope = _envelope()
    with _guard(tmp_path) as guard:
        first = open_payload(envelope, KEY, context=CONTEXT, replay_guard=guard, now=NOW)
        with pytest.raises(PayloadReplayError, match="already opened") as replayed:
            open_payload(envelope, KEY, context=CONTEXT, replay_guard=guard, now=NOW + 1)
    assert first.replay_protected is True
    assert first.plaintext == "deploy"
    assert replayed.value.reason == "replayed"
    assert replayed.value.code == "payload_replay"
    with _guard(tmp_path) as reopened:
        with pytest.raises(PayloadReplayError) as after_restart:
            open_payload(envelope, KEY, context=CONTEXT, replay_guard=reopened, now=NOW + 2)
        other_sender = PayloadContext(
            message_type=MessageType.CHAT, sender="OTHER", target="LISTENER"
        )
        moved = dict(
            encrypt_payload(
                "deploy",
                KEY,
                key_id="k1",
                recipients=["LISTENER"],
                context=other_sender,
                message_id=envelope["message_id"],
                created_at_ms=envelope["created_at_ms"],
            )
        )
        independent = open_payload(moved, KEY, context=other_sender, replay_guard=reopened, now=NOW)
    assert after_restart.value.reason == "replayed"
    assert independent.replay_protected is True


def test_stale_future_and_full_ledgers_refuse(tmp_path: Path) -> None:
    with _guard(tmp_path, window_seconds=60.0, future_skew_seconds=5.0, max_entries=1) as guard:
        with pytest.raises(PayloadReplayError, match="older than the 60s replay window") as stale:
            open_payload(
                _envelope(created_at_ms=int((NOW - 61) * 1000)),
                KEY,
                context=CONTEXT,
                replay_guard=guard,
                now=NOW,
            )
        with pytest.raises(PayloadReplayError, match="ahead of this receiver") as future:
            open_payload(
                _envelope(created_at_ms=int((NOW + 6) * 1000)),
                KEY,
                context=CONTEXT,
                replay_guard=guard,
                now=NOW,
            )
        open_payload(_envelope(), KEY, context=CONTEXT, replay_guard=guard, now=NOW)
        with pytest.raises(PayloadReplayError, match="ledger is full") as full:
            open_payload(_envelope(), KEY, context=CONTEXT, replay_guard=guard, now=NOW)
        # once the first id ages out of the window, the slot is free again
        later = open_payload(
            _envelope(created_at_ms=int((NOW + 61) * 1000)),
            KEY,
            context=CONTEXT,
            replay_guard=guard,
            now=NOW + 62,
        )
    assert (stale.value.reason, future.value.reason, full.value.reason) == (
        "stale",
        "future",
        "capacity",
    )
    assert later.replay_protected is True


def test_version_one_is_marked_or_refused_and_no_guard_means_no_protection(tmp_path: Path) -> None:
    legacy = _legacy_envelope("old client")
    with _guard(tmp_path) as guard:
        marked = open_payload(legacy, KEY, context=CONTEXT, replay_guard=guard, now=NOW)
        with pytest.raises(PayloadReplayError, match="no replay identity") as refused:
            open_payload(
                legacy,
                KEY,
                context=CONTEXT,
                replay_guard=guard,
                require_replay_protection=True,
                now=NOW,
            )
    unguarded = open_payload(_envelope(), KEY, context=CONTEXT, replay_guard=None)
    assert (marked.plaintext, marked.replay_protected) == ("old client", False)
    assert refused.value.reason == "unprotected"
    assert unguarded.replay_protected is False


def test_the_receiver_clock_is_read_when_not_given(tmp_path: Path) -> None:
    fresh = dict(encrypt_payload("now", KEY, key_id="k1", recipients=[], context=CONTEXT))
    with _guard(tmp_path) as guard:
        opened = open_payload(fresh, KEY, context=CONTEXT, replay_guard=guard)
    assert opened.replay_protected is True


def test_the_ledger_is_owner_only(tmp_path: Path) -> None:
    with _guard(tmp_path):
        pass
    ledger = tmp_path / "ledger" / "replay.db"
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    assert stat.S_IMODE(ledger.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"window_seconds": 0.0}, "window must be positive and finite"),
        ({"window_seconds": math.inf}, "window must be positive and finite"),
        ({"window_seconds": math.nan}, "window must be positive and finite"),
        ({"future_skew_seconds": -1.0}, "future skew must be finite and nonnegative"),
        ({"future_skew_seconds": math.inf}, "future skew must be finite and nonnegative"),
    ],
)
def test_an_unusable_ledger_configuration_is_refused(
    tmp_path: Path, kwargs: dict[str, float], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _guard(tmp_path, **kwargs)


def test_the_default_ledger_is_per_receiver_under_the_data_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = default_payload_replay_ledger("P/alice", base=tmp_path)
    assert alice.parent == tmp_path / "synapse" / "payload-replay"
    assert alice != default_payload_replay_ledger("P/bob", base=tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert default_payload_replay_ledger("P/alice").parent == (
        tmp_path / "data" / "synapse" / "payload-replay"
    )
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert default_payload_replay_ledger("P/alice").parent == (
        tmp_path / "home" / ".local" / "share" / "synapse" / "payload-replay"
    )
    assert (DEFAULT_PAYLOAD_REPLAY_WINDOW_SECONDS, DEFAULT_PAYLOAD_REPLAY_CAPACITY) == (
        86_400.0,
        100_000,
    )
    assert DEFAULT_PAYLOAD_FUTURE_SKEW_SECONDS == 300.0


def _frame(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "chat",
        "payload": "<encrypted payload>",
        "encrypted": envelope,
        "sender": "PEER",
        "target": "LISTENER",
    }


def test_the_listener_rendering_shows_once_marks_legacy_and_refuses(tmp_path: Path) -> None:
    fresh = dict(
        encrypt_payload("hello", KEY, key_id="k1", recipients=["LISTENER"], context=CONTEXT)
    )
    with _guard(tmp_path) as guard:
        shown = _render_chat_payload(_frame(fresh), KEY, replay_guard=guard)
        again = _render_chat_payload(_frame(fresh), KEY, replay_guard=guard)
        legacy = _render_chat_payload(_frame(_legacy_envelope("old")), KEY, replay_guard=guard)
        refused = _render_chat_payload(
            _frame(_legacy_envelope("old")),
            KEY,
            replay_guard=guard,
            require_replay_protection=True,
        )
    assert shown == "hello"
    assert again.startswith("<encrypted payload: encrypted payload ") and "(replay)" in again
    assert legacy == "old [not replay-protected: version 1 envelope]"
    assert "no replay identity" in refused


async def test_listen_refuses_an_unusable_replay_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_file = tmp_path / "payload.key"
    key_file.write_bytes(KEY)
    key_file.chmod(0o600)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    code = await cli_messaging._listen(
        uri="ws://127.0.0.1:1",
        name="LISTENER",
        decrypt_key_file=str(key_file),
        replay_ledger=str(blocker / "replay.db"),
    )
    assert code == 1
    assert "replay ledger failed:" in capsys.readouterr().out


async def test_a_replayed_envelope_through_a_real_hub_is_shown_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key_file = tmp_path / "payload.key"
    key_file.write_bytes(KEY)
    key_file.chmod(0o600)
    ledger = tmp_path / "listener-replay.db"
    captured = dict(
        encrypt_payload(
            "rotate the key", KEY, key_id="k1", recipients=["LISTENER"], context=CONTEXT
        )
    )
    async with running_hub(SynapseHub()) as (_hub, uri):
        observer = await connect_agent("OBSERVER", uri)
        listen_task = asyncio.create_task(
            cli_messaging._listen(
                uri=uri,
                name="LISTENER",
                for_name="LISTENER",
                max_messages=2,
                decrypt_key_file=str(key_file),
                replay_ledger=str(ledger),
            )
        )
        await observer.recorder.wait_for(
            lambda m: m.get("type") == "presence_update" and m.get("agent") == "LISTENER"
        )
        peer = await connect_agent("PEER", uri)
        try:
            for _ in range(2):
                await peer.agent.send_message(
                    MessageType.CHAT,
                    target="LISTENER",
                    payload="<encrypted payload>",
                    encrypted=captured,
                )
            code = await asyncio.wait_for(listen_task, timeout=10)
        finally:
            await close_agents(peer, observer)
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("PEER: rotate the key") == 1
    assert "was already opened (replay)" in out
