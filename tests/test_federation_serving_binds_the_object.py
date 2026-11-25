# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an owning hub authorises the peer, the namespace and the object it touches
"""An owning hub authorises a federated peer for the request's namespace, and binds what it touches.

A served owner hub holds claims in two namespaces it owns, ``ALPHA`` and ``BRAVO``, and
trusts one peer hub. Each test drives that peer's real frames over a socket. A relayed
release names a namespace, so it frees only a claim in that namespace. A forwarded claim
names a namespace and a task, so its body claims that task, for a claimant in that
namespace. The peer is checked for the request's namespace, not for the namespace of its
grant. A namespace another hub is observed to assert is not served. A dead-letter pointer is
namespace-authorised too, though it changes no coordination state.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from hub_e2e_helpers import read_until_type, running_hub, send_json
from synapse_channel.core.dead_letter_forwarding import FORWARDING_FIELD, forwarding_notice
from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.multihub_claim_wire import (
    ClaimForwardRequest,
    decode_claim_forward_result,
    encode_claim_forward_request,
)
from synapse_channel.core.multihub_serving import MultiHubServingGrant, MultiHubServingPolicy
from synapse_channel.core.namespace_ownership import NamespaceOwnership
from synapse_channel.core.operator_relay_wire import (
    RelayActionRequest,
    decode_relay_result,
    encode_relay_request,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.tls import MTLSPeerTrustBundle, MTLSTrustedPeer
from test_hub_operator_relay import _connect, _write_peer_cert

_A, _B, _C = "ALPHA", "BRAVO", "CHARLIE"
_OWNER = "syn-owner"
_DOMAIN = "domain-peer"
_KEY = "peer:main:2026-11"


def _policy(
    pin: str,
    der: bytes,
    *,
    trusted: str,
    scope: Iterable[tuple[str, str]],
    also_trusted: tuple[str, ...] = (),
) -> MultiHubServingPolicy:
    """Trust the peer hub ``peer`` for ``trusted`` and ``also_trusted``, with the given scope."""
    namespaces = frozenset({trusted, *also_trusted})
    return MultiHubServingPolicy(
        federation=FederationBundle(
            [
                FederationPeer(
                    domain_id=_DOMAIN,
                    namespaces=namespaces,
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    scope_grants=tuple(ScopeGrant(verb=v, namespace=n) for v, n in scope),
                )
            ]
        ),
        mtls=MTLSPeerTrustBundle(
            peers={
                _DOMAIN: MTLSTrustedPeer(
                    peer_id=_DOMAIN,
                    certificate_pins=frozenset({pin}),
                    signing_key_ids=frozenset({_KEY}),
                    projects=namespaces,
                )
            }
        ),
        grants={
            "peer": MultiHubServingGrant(domain_id=_DOMAIN, namespace=trusted, signing_key_id=_KEY)
        },
        clock=lambda: 0.0,
        cert_source=lambda _websocket: der,
    )


def _owner(
    policy: MultiHubServingPolicy,
    *,
    journal: EventStore | None = None,
    asserting: Callable[[str], Iterable[str]] | None = None,
    lease_offline_ttl: float = 900.0,
) -> SynapseHub:
    """Return the owner of ``ALPHA`` and ``BRAVO``, holding one claim in each."""
    hub = SynapseHub(
        hub_id=_OWNER,
        multihub_serving_policy=policy,
        namespace_ownership=NamespaceOwnership(
            owners={_A: _OWNER, _B: _OWNER}, local_hub_id=_OWNER
        ),
        journal=journal,
        observed_asserting_hubs=asserting,
        lease_offline_ttl=lease_offline_ttl,
    )
    hub.state.claim(f"{_A}/holder", "tA", paths=("alpha/held.py",))
    hub.state.claim(f"{_B}/holder", "tB", paths=("bravo/held.py",))
    return hub


def _release(namespace: str, task_id: str) -> RelayActionRequest:
    """Return a relayed force-release of ``task_id``, stated to act in ``namespace``."""
    return RelayActionRequest(
        action="release",
        namespace=namespace,
        task_id=task_id,
        operator="peer/ops",
        origin_hub_id=_DOMAIN,
    )


def _claim(namespace: str, task_id: str, *, claimant: str, body_task: str) -> ClaimForwardRequest:
    """Return a forwarded claim whose body claims ``body_task``."""
    return ClaimForwardRequest(
        namespace=namespace,
        claimant=claimant,
        task_id=task_id,
        claim={"task_id": body_task, "note": "forwarded work", "paths": [f"work/{body_task}.py"]},
    )


async def _relay(uri: str, request: RelayActionRequest) -> bool:
    """Relay one release as the peer hub and return whether the owner applied it."""
    async with await _connect(uri, "peer") as ws:
        await send_json(
            ws,
            sender="peer",
            type=MessageType.OPERATOR_RELAY_REQUEST,
            **encode_relay_request(request),
        )
        reply = await read_until_type(ws, MessageType.OPERATOR_RELAY_RESULT)
    return decode_relay_result(reply).applied


async def _forward(uri: str, request: ClaimForwardRequest) -> bool:
    """Forward one claim as the peer hub and return whether the owner granted it."""
    async with await _connect(uri, "peer") as ws:
        await send_json(
            ws,
            sender="peer",
            type=MessageType.MULTIHUB_CLAIM_REQUEST,
            **encode_claim_forward_request(request),
        )
        reply = await read_until_type(ws, MessageType.MULTIHUB_CLAIM_RESULT)
    return decode_claim_forward_result(reply).granted


async def test_a_relayed_release_frees_only_a_claim_in_its_namespace(tmp_path: Path) -> None:
    """A peer scoped to release in ALPHA cannot free BRAVO's claim by naming ALPHA."""
    pin, der = _write_peer_cert(tmp_path)
    hub = _owner(_policy(pin, der, trusted=_A, scope=[("release", _A)]))
    async with running_hub(hub) as (_, uri):
        assert await _relay(uri, _release(_A, "tB")) is False
        assert hub.state.claims["tB"].owner == f"{_B}/holder"
        assert await _relay(uri, _release(_A, "tA")) is True
    assert "tA" not in hub.state.claims


@pytest.mark.parametrize(
    ("claimant", "task_id", "body_task"),
    [
        (f"{_A}/alice", "tA2", "tB2"),
        (f"{_B}/bob", "tA3", "tA3"),
        (f"{_A}/alice", "tB", "tB"),
    ],
    ids=[
        "body-names-another-task",
        "claimant-of-another-namespace",
        "task-held-in-another-namespace",
    ],
)
async def test_a_forwarded_claim_is_bound_to_its_namespace_and_task(
    tmp_path: Path, claimant: str, task_id: str, body_task: str
) -> None:
    """A forwarded claim stated for ALPHA claims only its own task, for an ALPHA claimant."""
    pin, der = _write_peer_cert(tmp_path)
    hub = _owner(_policy(pin, der, trusted=_A, scope=[("claim", _A)]))
    async with running_hub(hub) as (_, uri):
        granted = await _forward(uri, _claim(_A, task_id, claimant=claimant, body_task=body_task))
        assert granted is False
        assert await _forward(uri, _claim(_A, "tA4", claimant=f"{_A}/alice", body_task="tA4"))
    assert body_task not in hub.state.claims or hub.state.claims[body_task].owner != claimant
    assert hub.state.claims["tB"].owner == f"{_B}/holder"


async def test_a_peer_trusted_for_another_namespace_is_refused(tmp_path: Path) -> None:
    """A peer trusted only for CHARLIE is refused in ALPHA, though its scope lists ALPHA."""
    pin, der = _write_peer_cert(tmp_path)
    policy = _policy(pin, der, trusted=_C, scope=[("release", _A), ("claim", _A)])
    hub = _owner(policy)
    async with running_hub(hub) as (_, uri):
        assert await _relay(uri, _release(_A, "tA")) is False
        assert (
            await _forward(uri, _claim(_A, "tA5", claimant=f"{_A}/alice", body_task="tA5")) is False
        )
    assert hub.state.claims["tA"].owner == f"{_A}/holder"
    assert "tA5" not in hub.state.claims


async def test_a_contested_namespace_is_not_served(tmp_path: Path) -> None:
    """A namespace another hub is observed to assert is refused on the serving side."""
    pin, der = _write_peer_cert(tmp_path)
    policy = _policy(pin, der, trusted=_A, scope=[("release", _A), ("claim", _A)])
    hub = _owner(policy, asserting=lambda namespace: ("syn-contender",) if namespace == _A else ())
    async with running_hub(hub) as (_, uri):
        assert await _relay(uri, _release(_A, "tA")) is False
        assert (
            await _forward(uri, _claim(_A, "tA6", claimant=f"{_A}/alice", body_task="tA6")) is False
        )
    assert hub.state.claims["tA"].owner == f"{_A}/holder"
    assert "tA6" not in hub.state.claims


async def test_a_dead_letter_pointer_is_authorised_for_its_namespace(tmp_path: Path) -> None:
    """A peer trusted only for CHARLIE cannot report a dead letter for an ALPHA seat."""
    pin, der = _write_peer_cert(tmp_path)
    store = EventStore(tmp_path / "events.db")
    hub = _owner(_policy(pin, der, trusted=_C, scope=[]), journal=store)
    async with running_hub(hub) as (_, uri):
        async with await _connect(uri, "peer") as peer:
            notice = forwarding_notice(
                f"{_A}/reader", 2, origin_hub_id=_DOMAIN, owner_hub_id=_OWNER
            )
            await send_json(
                peer,
                sender="peer",
                type=MessageType.DEAD_LETTER_FORWARDING,
                **{FORWARDING_FIELD: notice},
            )
            notice_c = forwarding_notice(
                f"{_C}/reader", 2, origin_hub_id=_DOMAIN, owner_hub_id=_OWNER
            )
            await send_json(
                peer,
                sender="peer",
                type=MessageType.DEAD_LETTER_FORWARDING,
                **{FORWARDING_FIELD: notice_c},
            )
            await send_json(peer, sender="peer", type="heartbeat")
            await read_until_type(peer, MessageType.PRESENCE_UPDATE)
    audits = [e.payload for e in store.read_all() if e.kind == EventKind.DEAD_LETTER_FORWARDING]
    store.close()
    assert audits == []


async def test_a_claim_forward_needs_the_claim_scope(tmp_path: Path) -> None:
    """A peer trusted for ALPHA whose scope grants only ``read`` there forwards no claim."""
    pin, der = _write_peer_cert(tmp_path)
    hub = _owner(_policy(pin, der, trusted=_A, scope=[("read", _A)]))
    async with running_hub(hub) as (_, uri):
        granted = await _forward(uri, _claim(_A, "tA7", claimant=f"{_A}/alice", body_task="tA7"))
    assert granted is False
    assert "tA7" not in hub.state.claims


async def test_the_claims_one_peer_forwards_share_one_quota_bucket(tmp_path: Path) -> None:
    """A peer cannot multiply the owner's claim cap by rotating the claimant names it forwards.

    The nested claimant is the peer's own assertion, so the owner charges every claim one peer
    forwards to that peer's bucket. With a cap of one, a second alias from the same peer is
    refused, and the first claim stands.
    """
    pin, der = _write_peer_cert(tmp_path)
    hub = _owner(_policy(pin, der, trusted=_A, scope=[("claim", _A)]))
    hub.state.max_claims_per_agent = 1
    async with running_hub(hub) as (_, uri):
        first = await _forward(uri, _claim(_A, "tQ1", claimant=f"{_A}/alice", body_task="tQ1"))
        rotated = await _forward(uri, _claim(_A, "tQ2", claimant=f"{_A}/bob", body_task="tQ2"))
    assert first is True
    assert rotated is False
    assert hub.state.claims["tQ1"].owner == f"{_A}/alice"
    assert "tQ2" not in hub.state.claims


async def test_an_abandoned_claim_of_another_namespace_is_freed_only_for_its_namespace(
    tmp_path: Path,
) -> None:
    """A task BRAVO's offline holder still holds is refused to ALPHA and released for BRAVO.

    The owner releases a claim whose holder stayed offline past the lease window before it
    decides a forwarded claim. A forward stated for ALPHA must be refused before that
    release can hand it BRAVO's task; a forward from BRAVO itself may take it over.
    """
    pin, der = _write_peer_cert(tmp_path)
    policy = _policy(pin, der, trusted=_A, also_trusted=(_B,), scope=[("claim", _A), ("claim", _B)])
    hub = _owner(policy, lease_offline_ttl=0.05)
    hub.state.claim(f"{_B}/sleeper", "tX", paths=("bravo/x.py",))
    async with running_hub(hub) as (_, uri):
        await asyncio.sleep(0.2)
        from_alpha = await _forward(uri, _claim(_A, "tX", claimant=f"{_A}/alice", body_task="tX"))
        assert from_alpha is False
        assert hub.state.claims["tX"].owner == f"{_B}/sleeper"
        from_bravo = await _forward(uri, _claim(_B, "tX", claimant=f"{_B}/bob", body_task="tX"))
    assert from_bravo is True
    assert hub.state.claims["tX"].owner == f"{_B}/bob"


@pytest.mark.parametrize(
    ("owner", "namespace"),
    [("PROJ/seat", "PROJ"), ("PROJ/", None), ("/seat", None), ("A", None), ("", None)],
)
def test_a_claim_belongs_to_its_owners_project_segment(owner: str, namespace: str | None) -> None:
    """An owner without both a project and a seat belongs to no namespace."""
    shared = importlib.import_module("synapse_channel.core.federation_serving")
    assert shared.claim_namespace(owner) == namespace


_HANDLERS = Path(__file__).resolve().parents[1] / "src" / "synapse_channel" / "core" / "handlers"
_SHARED = "authorise_federated_mutation"
_VERB_NONE_ALLOWED = frozenset({"dead_letter_forwarding"})
"""Handlers that may pass ``verb=None``: a dead-letter pointer changes no coordination state."""
_EXEMPT_REASONS = {
    "message_forward": "delivery to a hosted seat; per-target authorise_namespace, no ownership",
    "attachment_peer": (
        "a peer read of an attachment in scope: per request authorise_namespace(scope), "
        "ScopeGrant('read', scope) and recipients.allows(sender, scope, digest); the store is "
        "keyed by (scope, digest), so the object is bound to the scope it names"
    ),
    "spend": (
        "the peer's own spend ledger, keyed by the verified sender; there is no namespace-scoped "
        "object, and pool authorisation is the ledger's own grant rule"
    ),
}
"""Peer-reachable mutating handlers outside the shared function, each with its written reason."""


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    """Return every call in ``tree`` to a function or method named ``name``."""
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if called == name:
                found.append(node)
    return found


def _peer_reachable_mutating_handlers() -> set[str]:
    """Return the handler modules a federated peer reaches that declare a mutating verb."""
    names: set[str] = set()
    for path in sorted(_HANDLERS.glob("*.py")):
        if "multihub_serving_policy" not in path.read_text(encoding="utf-8"):
            continue
        module = importlib.import_module(f"synapse_channel.core.handlers.{path.stem}")
        if any(spec.mutates for spec in getattr(module, "VERB_SPECS", ())):
            names.add(path.stem)
    return names


def _is_name(node: ast.AST, name: str) -> bool:
    """Return whether ``node`` is the bare name ``name``."""
    return isinstance(node, ast.Name) and node.id == name


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    """Return the value of ``call``'s keyword argument ``name``, if it has one."""
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _message_forward_binds_each_target(tree: ast.AST) -> bool:
    """Assert message_forward authorises the peer for a named namespace per target."""
    return any(
        _keyword(call, "namespace") is not None for call in _calls(tree, "authorise_namespace")
    )


def _attachment_peer_binds_the_scope(tree: ast.AST) -> bool:
    """Assert the scope is authorised, granted for ``read``, and keys every store access."""
    authorised = any(
        _is_name(_keyword(call, "namespace") or ast.Pass(), "scope")
        for call in _calls(tree, "authorise_namespace")
    )
    granted = any(
        isinstance(node, ast.Compare)
        and isinstance(node.ops[0], ast.In)
        and isinstance(node.left, ast.Call)
        and _is_name(node.left.func, "ScopeGrant")
        and isinstance(node.left.args[0], ast.Constant)
        and node.left.args[0].value == "read"
        and _is_name(node.left.args[1], "scope")
        for node in ast.walk(tree)
    )
    reads = [
        call
        for method in ("info", "read")
        for call in _calls(tree, method)
        if isinstance(call.func, ast.Attribute) and _is_name(call.func.value, "store")
    ]
    return (
        authorised
        and granted
        and len(reads) >= 2
        and all(_is_name(c.args[0], "scope") for c in reads)
    )


def _spend_keys_the_ledger_by_the_sender(tree: ast.AST) -> bool:
    """Assert the peer is authorised by name and every ledger call is keyed by the sender."""
    authorised = any(
        _is_name(_keyword(call, "sender") or ast.Pass(), "sender")
        for call in _calls(tree, "authorise")
    )
    methods = {"reserve", "settle", "peer_status", "query"}
    keyed: set[str] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        arguments = list(call.args)
        if arguments and isinstance(call.func, ast.Attribute) and call.func.attr == "to_thread":
            target, arguments = arguments[0], arguments[1:]
        else:
            target = call.func
        if (
            isinstance(target, ast.Attribute)
            and _is_name(target.value, "ledger")
            and target.attr in methods
        ):
            if not (arguments and _is_name(arguments[0], "sender")):
                return False
            keyed.add(target.attr)
    return authorised and keyed == methods


_EXEMPT_CHECKS: dict[str, Callable[[ast.AST], bool]] = {
    "message_forward": _message_forward_binds_each_target,
    "attachment_peer": _attachment_peer_binds_the_scope,
    "spend": _spend_keys_the_ledger_by_the_sender,
}


def test_every_peer_reachable_mutation_goes_through_the_shared_authorisation() -> None:
    """A new peer-reachable mutating verb must use the shared function or a named exemption."""
    handlers = _peer_reachable_mutating_handlers()
    assert handlers == {
        "dead_letter_forwarding",
        "multihub_claim",
        "operator_relay",
        *_EXEMPT_REASONS,
    }
    assert set(_EXEMPT_CHECKS) == set(_EXEMPT_REASONS)
    for name in sorted(handlers):
        tree = ast.parse((_HANDLERS / f"{name}.py").read_text(encoding="utf-8"))
        shared = _calls(tree, _SHARED)
        if name in _EXEMPT_REASONS:
            assert _EXEMPT_CHECKS[name](tree), (name, _EXEMPT_REASONS[name])
            assert shared == [], name
            continue
        assert shared, name
        assert _calls(tree, "authorise") == [], name
        for call in shared:
            verb = next(keyword.value for keyword in call.keywords if keyword.arg == "verb")
            if isinstance(verb, ast.Constant) and verb.value is None:
                assert name in _VERB_NONE_ALLOWED, name
    shared_function = importlib.import_module("synapse_channel.core.federation_serving")
    parameter = inspect.signature(getattr(shared_function, _SHARED)).parameters["verb"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
