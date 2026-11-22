# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a release sidecar beside a live seat
"""The release sidecar acts for its owner and never becomes a seat."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.agent_liveness import waiter_owner
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.event_description import describe_event
from synapse_channel.core.handlers.leasing import handle_release
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_identity_gate import HubIdentityGate
from synapse_channel.core.identity_keys import public_key_b64, sign_registration
from synapse_channel.core.identity_pins import IdentityPinStore
from synapse_channel.core.journal import EventKind
from synapse_channel.core.message_auth import (
    EventSignatureKey,
    EventSignatureTrustBundle,
    MessageReplayCache,
    sign_event_frame,
)
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.release_sidecar import (
    acting_release,
    claim_release_mode,
    claims_visible_to,
    release_connection_name,
    release_owner,
    sidecar_frame_allowed,
    sidecar_trigger_refusal,
)
from synapse_channel.core.replay import reconstruct_claim
from synapse_channel.core.state_models import GitContext
from synapse_channel.core.token_audit import TokenAudit
from synapse_channel.core.token_set import TokenSetAuthenticator, connect_token_digest
from synapse_channel.core.universal_receipts import NON_RECEIPT_EVENT_KINDS
from synapse_channel.git import githook


def test_release_owner_refuses_a_nested_or_empty_suffix() -> None:
    assert release_owner("seat-release") == "seat"
    assert release_owner("seat") is None
    assert release_owner("-release") is None
    assert release_owner("a-release-release") is None
    assert release_owner("a-rx-release") is None
    assert acting_release("a-rx-release") is None
    assert claims_visible_to("a-rx-release", {"active_claims": []}) is None
    assert acting_release("seat") == ("seat", None)
    assert release_owner("a-pane-rx-release") is None
    assert release_connection_name("seat") == "seat-release"


def test_a_release_sidecar_does_not_vouch_for_its_seat() -> None:
    assert waiter_owner("seat-rx") == "seat"
    assert waiter_owner("seat-release") == "seat-release"
    assert waiter_owner("seat-release-rx") == "seat-release-rx"
    assert sidecar_frame_allowed(MessageType.RELEASE)
    assert sidecar_frame_allowed(MessageType.STATE_REQUEST)
    assert not sidecar_frame_allowed(MessageType.CHAT)


def test_an_owner_release_row_is_unchanged_and_via_is_only_extra() -> None:
    plain = describe_event(EventKind.RELEASE, {"task_id": "T"})
    assert plain is not None
    assert plain.actor == ""
    assert plain.summary == "released T"
    named = describe_event(
        EventKind.RELEASE, {"task_id": "T", "owner": "seat", "via": "seat-release"}
    )
    assert named is not None
    assert named.actor == "seat"
    assert "via seat-release" in named.summary
    assert EventKind.RELEASE in NON_RECEIPT_EVENT_KINDS


def test_replay_ignores_owner_and_via_on_a_release() -> None:
    claim = StoredEvent(
        seq=1,
        ts=1.0,
        kind=EventKind.CLAIM,
        payload={
            "task_id": "T",
            "owner": "seat",
            "status": "active",
            "note": "",
            "data_ref": "",
            "version": 1,
            "epoch": 1,
            "paths": [],
        },
    )
    released = StoredEvent(
        seq=2,
        ts=2.0,
        kind=EventKind.RELEASE,
        payload={"task_id": "T", "owner": "seat", "via": "seat-release"},
    )
    assert reconstruct_claim("T", [claim, released]) is None


@pytest.mark.asyncio
async def test_a_sidecar_does_not_pin_and_a_foreign_key_is_refused() -> None:
    sent: list[dict[str, Any]] = []

    class _Socket:
        reason = ""

        async def close(self, *, code: int, reason: str) -> None:
            self.reason = reason

    async def send_json(_socket: object, message: dict[str, Any]) -> None:
        sent.append(message)

    def system(text: str, **fields: object) -> dict[str, object]:
        return {"text": text, **fields}

    store = IdentityPinStore()
    gate = HubIdentityGate(
        require_identity_binding=False,
        identity_trust_bundle=None,
        send_json=send_json,
        system=system,
        pin_store=store,
    )
    public = base64.b64encode(b"k" * 32).decode("ascii")
    store.pin("seat-release", key_id="foreign", public_key=public)
    socket = _Socket()
    assert await gate.verify_identity("seat-release", {}, socket)
    assert store.pinned("seat") is None
    ignored = store.pinned("seat-release")
    assert ignored is not None
    assert ignored.key_id == "foreign"

    store.pin("seat", key_id="owner-key", public_key=public)
    sent.clear()
    refused = _Socket()
    assert not await gate.verify_identity("seat-release", {}, refused)
    assert refused.reason == "identity pin mismatch"
    owner_pin = store.pinned("seat")
    assert owner_pin is not None
    assert owner_pin.key_id == "owner-key"


async def test_a_valid_sidecar_proof_is_admitted_and_pins_nothing() -> None:
    """A self-contained proof admits the sidecar and creates no pin."""
    private_key = Ed25519PrivateKey.generate()
    public = public_key_b64(private_key)
    frame = {
        "sender": "seat-release",
        "type": MessageType.HEARTBEAT,
        "target": "System",
        "payload": "online",
        "identity_public_key": public,
    }
    signed = sign_registration(
        frame,
        private_key=private_key,
        key_id="k",
        nonce="n-sidecar",
        sequence=1,
        signed_at=1000.0,
    )
    store = IdentityPinStore()
    gate = HubIdentityGate(
        require_identity_binding=False,
        identity_trust_bundle=None,
        send_json=_ignore_send,
        system=lambda text, **fields: {"text": text, **fields},
        pin_store=store,
        clock=lambda: 1000.0,
    )
    assert await gate.verify_identity("seat-release", signed, _Socket())
    assert store.pinned("seat") is None
    assert store.pinned("seat-release") is None


async def _ignore_send(_socket: object, _message: dict[str, Any]) -> None:
    return None


@pytest.mark.asyncio
async def test_a_nested_sidecar_name_is_refused_before_a_pin() -> None:
    sent: list[dict[str, Any]] = []

    class _Socket:
        reason = ""

        async def close(self, *, code: int, reason: str) -> None:
            self.reason = reason

    async def send_json(_socket: object, message: dict[str, Any]) -> None:
        sent.append(message)

    store = IdentityPinStore()
    gate = HubIdentityGate(
        require_identity_binding=False,
        identity_trust_bundle=None,
        send_json=send_json,
        system=lambda text, **fields: {"text": text, **fields},
        pin_store=store,
    )
    socket = _Socket()
    assert not await gate.verify_identity("a-rx-release", {}, socket)
    assert socket.reason == "reserved suffix"
    assert store.pinned("a-rx") is None
    assert store.pinned("a-rx-release") is None


def test_the_client_does_not_bind_a_sidecar_when_the_welcome_lacks_it(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    names: list[str] = []

    def factory(name: str, callback: Any, **_kwargs: Any) -> Any:
        names.append(name)

        class _Agent:
            running = True

            async def request_state(self) -> None:
                await callback(
                    {"type": MessageType.STATE_SNAPSHOT, "snapshot": {"active_claims": []}}
                )

            async def release(self, _task: str) -> None:
                return None

        return _Agent()

    async def probe(_uri: str) -> bool:
        return False

    async def registrar(agent: Any, _timeout: float) -> asyncio.Task[None]:
        return asyncio.create_task(asyncio.sleep(30))

    # git diff-tree needs a commit. The probe decides the name before any bind.
    _git(repo, ["init", "-b", "main"])
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, ["add", "a.txt"])
    _git(repo, ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-m", "a"])

    async def run() -> int:
        return await githook.run_git_release(
            uri="ws://127.0.0.1:9",
            name="seat",
            trigger="commit",
            runner=lambda args: _git(repo, args),
            agent_factory=factory,
            welcome_probe=probe,
            registrar=registrar,
        )

    assert asyncio.run(run()) == 0
    assert names == ["seat"]


def _git(repo: Path, args: list[str]) -> str:
    import subprocess

    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


class _ReleaseHub(SynapseHub):
    """A hub that records frames instead of writing them to a socket."""

    def __init__(self, store: EventStore, authenticator: TokenAuthenticator | None) -> None:
        super().__init__(
            hub_id="c85-s6",
            journal=store,
            authenticator=authenticator,
            anti_rollback_checkpoint=False,
        )
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, _websocket: Any, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def broadcast(self, data: dict[str, Any]) -> frozenset[str]:
        self.sent.append(data)
        return frozenset()


class _Socket:
    async def close(self, *, code: int, reason: str) -> None:
        self.code = code
        self.reason = reason


def _hold(hub: _ReleaseHub, task_id: str, mode: str | None) -> None:
    git = None if mode is None else GitContext(branch="release-sidecar", auto_release_on=mode)
    ok, message = hub.state.claim("seat", task_id, git=git)
    assert ok, message


async def _admit(hub: _ReleaseHub, token: str) -> _Socket:
    socket = _Socket()
    admitted = await hub._ingress.authorise(
        "seat-release",
        {"type": MessageType.HEARTBEAT, "token": token},
        socket,
    )
    assert admitted is True
    return socket


async def _release(hub: _ReleaseHub, socket: _Socket, task_id: str, trigger: str) -> None:
    await handle_release(
        hub,
        "seat-release",
        {"task_id": task_id, "trigger": trigger},
        socket,
    )


def _bound(hub: _ReleaseHub, socket: _Socket) -> bool:
    """Return whether the admitting token named the seat on this socket."""
    return hub._ingress._sidecar_roster.bound(socket)


def _denied(hub: _ReleaseHub, task_id: str) -> str:
    denied = [frame for frame in hub.sent if frame.get("type") == MessageType.RELEASE_DENIED]
    match = [frame for frame in denied if frame.get("task_id") == task_id]
    assert match, denied
    return str(match[-1].get("payload") or match[-1])


def test_a_narrowed_sidecar_names_a_manual_claim_and_another_trigger() -> None:
    assert sidecar_trigger_refusal("manual-one", "manual", "commit") == (
        "claim manual-one is manual and this release sidecar may not release it"
    )
    assert sidecar_trigger_refusal("plain", "manual", "manual") is not None
    other = sidecar_trigger_refusal("merge-one", "merge", "commit")
    assert other == "claim merge-one auto-releases on merge, not on commit"
    assert sidecar_trigger_refusal("commit-one", "commit", "") == (
        "claim commit-one auto-releases on commit, not on none"
    )
    assert sidecar_trigger_refusal("commit-one", "commit", "commit") is None
    assert claim_release_mode(None) == "manual"
    assert claim_release_mode(GitContext(branch="b", auto_release_on="commit")) == "commit"
    assert claim_release_mode(GitContext(branch="b", auto_release_on="nope")) == "manual"


def test_binds_agent_is_false_for_a_shared_or_unknown_token() -> None:
    shared = TokenAuthenticator(["shared-secret"])
    named = TokenAuthenticator({"seat-secret": ["seat"]})
    assert shared.binds_agent("", "seat") is False
    assert shared.binds_agent("missing", "seat") is False
    assert shared.binds_agent("shared-secret", "seat") is False
    assert named.binds_agent("seat-secret", "seat") is True
    assert named.binds_agent("seat-secret", "other") is False


async def test_a_nested_sidecar_name_cannot_release(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "nested.db")
    hub = _ReleaseHub(store, None)
    socket = _Socket()
    admitted = await hub._ingress.authorise("a-rx-release", {"type": MessageType.HEARTBEAT}, socket)
    assert admitted is False
    assert socket.reason == "reserved suffix"
    await handle_release(hub, "a-rx-release", {"task_id": "T"}, socket)
    assert "not a seat" in _denied(hub, "T")
    store.close()


async def test_an_open_hub_sidecar_releases_only_the_trigger_it_presents(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "open.db")
    hub = _ReleaseHub(store, None)
    socket = await _admit(hub, "")
    _hold(hub, "manual-one", "manual")
    _hold(hub, "commit-one", "commit")
    _hold(hub, "merge-one", "merge")
    _hold(hub, "plain-one", None)
    await handle_release(hub, "seat", {"task_id": "manual-one"}, socket)
    assert "manual-one" not in hub.state.claims
    await _release(hub, socket, "commit-one", "commit")
    assert "commit-one" not in hub.state.claims
    await _release(hub, socket, "merge-one", "commit")
    assert "merge-one" in hub.state.claims
    assert "merge-one" in _denied(hub, "merge-one")
    assert "not on commit" in _denied(hub, "merge-one")
    await _release(hub, socket, "plain-one", "commit")
    assert "plain-one" in hub.state.claims
    assert "plain-one is manual" in _denied(hub, "plain-one")
    await _release(hub, socket, "absent-one", "commit")
    assert "not currently claimed" in _denied(hub, "absent-one")
    ok, message = hub.state.claim(
        "other",
        "foreign-one",
        worktree="other-tree",
        git=GitContext(branch="release-sidecar", auto_release_on="manual"),
    )
    assert ok, message
    await _release(hub, socket, "foreign-one", "commit")
    assert "foreign-one" in hub.state.claims
    assert "owned by other" in _denied(hub, "foreign-one")
    store.close()


def _release_rows(store: EventStore) -> list[dict[str, Any]]:
    return [
        event.payload
        for event in store.read_all()
        if event.kind == EventKind.RELEASE and isinstance(event.payload, dict)
    ]


async def test_a_sidecar_release_row_names_the_owner_and_the_connection(tmp_path: Path) -> None:
    """The handler writes owner and via for a sidecar, and neither for the seat."""
    store = EventStore(tmp_path / "rows.db")
    hub = _ReleaseHub(store, None)
    socket = await _admit(hub, "")
    _hold(hub, "commit-one", "commit")
    _hold(hub, "owner-one", None)
    await _release(hub, socket, "commit-one", "commit")
    await handle_release(hub, "seat", {"task_id": "owner-one"}, socket)
    rows = {str(row["task_id"]): row for row in _release_rows(store)}
    assert rows["commit-one"]["owner"] == "seat"
    assert rows["commit-one"]["via"] == "seat-release"
    assert "owner" not in rows["owner-one"]
    assert "via" not in rows["owner-one"]
    store.close()


async def test_a_shared_token_sidecar_releases_only_the_trigger_it_presents(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "shared.db")
    hub = _ReleaseHub(store, TokenAuthenticator(["shared-secret"]))
    socket = await _admit(hub, "shared-secret")
    assert _bound(hub, socket) is False
    _hold(hub, "manual-one", "manual")
    _hold(hub, "commit-one", "commit")
    await _release(hub, socket, "manual-one", "commit")
    assert "manual-one" in hub.state.claims
    assert "manual-one is manual" in _denied(hub, "manual-one")
    await _release(hub, socket, "commit-one", "commit")
    assert "commit-one" not in hub.state.claims
    store.close()


async def test_a_per_agent_token_sidecar_keeps_the_owner_release(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "named.db")
    hub = _ReleaseHub(store, TokenAuthenticator({"seat-secret": ["seat"]}))
    socket = await _admit(hub, "seat-secret")
    assert _bound(hub, socket) is True
    _hold(hub, "manual-one", "manual")
    _hold(hub, "merge-one", "merge")
    await _release(hub, socket, "manual-one", "commit")
    await _release(hub, socket, "merge-one", "")
    assert "manual-one" not in hub.state.claims
    assert "merge-one" not in hub.state.claims
    await hub._connection.unregister(socket)
    assert _bound(hub, socket) is False
    store.close()


def _token_set(path: Path, secret: str, agents: list[str]) -> TokenSetAuthenticator:
    document = {
        "version": 1,
        "tokens": [
            {
                "id": "seat-token",
                "sha256": connect_token_digest(secret),
                "agents": agents,
            }
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)
    return TokenSetAuthenticator(path)


async def test_a_per_agent_token_set_sidecar_keeps_the_owner_release(tmp_path: Path) -> None:
    named = _token_set(tmp_path / "named.json", "named-secret", ["seat"])
    store = EventStore(tmp_path / "set.db")
    hub = _ReleaseHub(store, named)
    socket = await _admit(hub, "named-secret")
    assert _bound(hub, socket) is True
    _hold(hub, "manual-one", "manual")
    await _release(hub, socket, "manual-one", "commit")
    assert "manual-one" not in hub.state.claims
    store.close()


async def test_a_shared_token_set_sidecar_stays_narrowed(tmp_path: Path) -> None:
    shared = _token_set(tmp_path / "shared.json", "shared-secret", [])
    store = EventStore(tmp_path / "set-shared.db")
    hub = _ReleaseHub(store, shared)
    socket = await _admit(hub, "shared-secret")
    assert _bound(hub, socket) is False
    _hold(hub, "manual-one", "manual")
    await _release(hub, socket, "manual-one", "merge")
    assert "manual-one" in hub.state.claims
    assert "manual-one is manual" in _denied(hub, "manual-one")
    store.close()


async def test_the_sidecar_trigger_is_read_under_the_release_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.core.handlers.leasing as leasing

    store = EventStore(tmp_path / "lock.db")
    hub = _ReleaseHub(store, TokenAuthenticator(["shared-secret"]))
    socket = await _admit(hub, "shared-secret")
    _hold(hub, "manual-one", "manual")
    seen: list[bool] = []
    real = sidecar_trigger_refusal

    def spy(task_id: str, mode: str, presented: str) -> str | None:
        seen.append(hub.state_mutations._lock.locked())
        return real(task_id, mode, presented)

    monkeypatch.setattr(leasing, "sidecar_trigger_refusal", spy)
    await _release(hub, socket, "manual-one", "commit")
    assert seen == [True]
    assert "manual-one" in hub.state.claims
    store.close()


def test_a_sidecar_audit_names_the_seat_and_the_connection(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "audit.db")
    audit = TokenAudit(store, clock=lambda: 1000.0)
    audit.admitted("ops", "seat", connection="seat-release")
    audit.admitted("ops", "seat", connection="seat")
    audit.refused("ops", "unknown", "seat", connection="seat-release")
    audit.refused("ops", "expired", "seat")
    rows = [event.payload for event in store.read_all()]
    assert rows[0] == {"token_id": "ops", "agent": "seat", "connection": "seat-release"}
    assert rows[1] == {"token_id": "ops", "agent": "seat"}
    assert rows[2]["agent"] == "seat"
    assert rows[2]["connection"] == "seat-release"
    assert "connection" not in rows[3]
    store.close()


def _binding_gate(bundle: EventSignatureTrustBundle) -> HubIdentityGate:
    async def send_json(_socket: object, _message: dict[str, Any]) -> None:
        return None

    return HubIdentityGate(
        require_identity_binding=True,
        identity_trust_bundle=bundle,
        send_json=send_json,
        system=lambda text, **fields: {"text": text, **fields},
        clock=lambda: 1000.0,
    )


def _signed(private_key: Ed25519PrivateKey, sender: str) -> dict[str, Any]:
    frame = {"sender": sender, "type": "heartbeat", "target": "System", "payload": "online"}
    return sign_event_frame(
        frame, key_id="k", private_key=private_key, nonce="n1", sequence=1, signed_at=1000.0
    )


async def test_a_bundle_hub_judges_a_sidecar_by_its_owner() -> None:
    private_key = Ed25519PrivateKey.generate()
    owner_key = EventSignatureKey.from_private_key(
        key_id="k", private_key=private_key, senders=frozenset({"seat"})
    )
    gate = _binding_gate(
        EventSignatureTrustBundle(
            keys={"k": owner_key},
            replay_cache=MessageReplayCache(window_seconds=10.0, max_entries=8),
        )
    )
    socket = _Socket()
    assert await gate.verify_identity("seat-release", _signed(private_key, "seat-release"), socket)

    sidecar_only = EventSignatureKey.from_private_key(
        key_id="k", private_key=private_key, senders=frozenset({"seat-release"})
    )
    refused_gate = _binding_gate(
        EventSignatureTrustBundle(
            keys={"k": sidecar_only},
            replay_cache=MessageReplayCache(window_seconds=10.0, max_entries=8),
        )
    )
    refused = _Socket()
    assert not await refused_gate.verify_identity(
        "seat-release", _signed(private_key, "seat-release"), refused
    )
    assert refused.reason == "identity binding failed"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, ["init", "-b", "main"])
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, ["add", "a.txt"])
    _git(repo, ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-m", "a"])
    (repo / "a.txt").write_text("b\n", encoding="utf-8")
    _git(repo, ["add", "a.txt"])
    _git(repo, ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-m", "b"])
    return repo


async def _release_through(
    repo: Path, *, advertised: bool | None, accept_sidecar: bool
) -> tuple[list[str], list[tuple[str, str, str]]]:
    from synapse_channel.git.semantic_release import release_context

    names: list[str] = []
    released: list[tuple[str, str, str]] = []
    root, branch = release_context(runner=lambda args: _git(repo, args))
    claim = {
        "task_id": "T",
        "owner": "seat",
        "worktree": root.as_posix(),
        "paths": ["a.txt"],
        "git": {"branch": branch, "auto_release_on": "commit"},
    }

    def factory(name: str, callback: Any, **_kwargs: Any) -> Any:
        names.append(name)

        class _Agent:
            running = True

            async def request_state(self) -> None:
                await callback(
                    {
                        "type": MessageType.STATE_SNAPSHOT,
                        "snapshot": {"active_claims": [claim]},
                    }
                )

            async def release(self, task: str, *, trigger: str = "") -> None:
                released.append((name, task, trigger))

        return _Agent()

    async def probe(_uri: str) -> bool | None:
        return advertised

    async def registrar(_agent: Any, _timeout: float) -> asyncio.Task[None] | None:
        if names[-1].endswith("-release") and not accept_sidecar:
            return None
        return asyncio.create_task(asyncio.sleep(30))

    await githook.run_git_release(
        uri="ws://127.0.0.1:9",
        name="seat",
        trigger="commit",
        runner=lambda args: _git(repo, args),
        agent_factory=factory,
        welcome_probe=probe,
        registrar=registrar,
    )
    return names, released


async def test_an_advertised_hub_falls_back_to_an_offline_seat(tmp_path: Path) -> None:
    names, released = await _release_through(_repo(tmp_path), advertised=True, accept_sidecar=False)
    assert names == ["seat-release", "seat"]
    assert released == [("seat", "T", "commit")]


async def test_a_missing_welcome_tries_the_sidecar_and_then_the_seat_once(tmp_path: Path) -> None:
    names, released = await _release_through(_repo(tmp_path), advertised=None, accept_sidecar=False)
    assert names == ["seat-release", "seat"]
    assert released == [("seat", "T", "commit")]


async def test_an_advertised_hub_binds_the_sidecar_first(tmp_path: Path) -> None:
    names, released = await _release_through(_repo(tmp_path), advertised=True, accept_sidecar=True)
    assert names == ["seat-release"]
    assert released == [("seat-release", "T", "commit")]


_ALLOW_LIST = "release sidecar may only release and read its own claims"


def _allow_list_refusal(message: dict[str, Any]) -> bool:
    return message.get("type") == MessageType.ERROR and message.get("payload") == _ALLOW_LIST


def _refusal_count(messages: list[dict[str, Any]], count: int) -> bool:
    return sum(1 for item in messages if _allow_list_refusal(item)) >= count


async def test_a_live_sidecar_cannot_chat_or_claim_and_can_still_release() -> None:
    """A hook-registered sidecar is refused outside its allow-list and can still release."""
    async with running_hub() as (hub, uri):
        reader = await connect_agent("reader", uri)
        seat = await connect_agent("seat", uri)
        sidecar = await connect_agent("seat-release", uri)
        try:
            await seat.agent.claim(
                "seat-claim", git={"branch": "main", "auto_release_on": "commit"}
            )
            await seat.recorder.wait_for(
                lambda message: (
                    message.get("type") == MessageType.CLAIM_GRANTED
                    and message.get("task_id") == "seat-claim"
                )
            )
            await sidecar.agent.chat("sideways", target="reader")
            await sidecar.recorder.wait_for(
                lambda _message: _refusal_count(sidecar.recorder.messages, 1)
            )
            assert not any(
                message.get("type") == MessageType.CHAT and message.get("payload") == "sideways"
                for message in reader.recorder.messages
            )
            await sidecar.agent.claim("sidecar-claim")
            await sidecar.recorder.wait_for(
                lambda _message: _refusal_count(sidecar.recorder.messages, 2)
            )
            assert "sidecar-claim" not in hub.state.claims
            await sidecar.agent.release("seat-claim", trigger="commit")
            await sidecar.recorder.wait_for(
                lambda message: (
                    message.get("type") == MessageType.RELEASE_GRANTED
                    and message.get("task_id") == "seat-claim"
                )
            )
            assert "seat-claim" not in hub.state.claims
        finally:
            await close_agents(sidecar, seat, reader)
