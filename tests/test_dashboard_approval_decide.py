# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the cockpit's approval decision: its checks, its relay and its record
"""Tests for ``POST /approval/decide``: every refusal comes before any relay.

The unit tests drive the body parser, the store checks and the route with
injected readers. The real test runs a hub with its journal, a dashboard with
an access file, and asks: a valid decision is journalled and a later attempt is
``approved_fresh``; each refusal leaves no approval note in the journal.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import operation_attempt_scenario as scene
import operation_report_store as journal_helpers
import synapse_channel.dashboard_approval_decide as decide_module
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.operation_report import (
    ChainLink,
    OperationReport,
    run_operation_report,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.dashboard_access_http import APPROVAL_DECIDE_PATH
from synapse_channel.dashboard_approval_decide import (
    APPROVAL_DECIDE_ACTION,
    MAX_APPROVAL_REASON_BYTES,
    REFUSED,
    ApprovalDecideBindings,
    ApprovalDecision,
    approval_refusal,
    decide_approval,
    parse_approval_decision,
)
from synapse_channel.dashboard_feed_serving import FeedResponse
from synapse_channel.dashboard_operator import (
    DENIED,
    RECORDED,
    REJECTED,
    UNREACHABLE,
    OperatorRelay,
)
from synapse_channel.dashboard_operator_writes import RelayPlan

DIGEST = "a" * 64
OTHER = "b" * 64


def _body(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "operation_id": "op-1",
        "intent_sha256": DIGEST,
        "decision": "approved",
        "reason": "looked at the intent",
    }
    body.update(changes)
    return body


# --- the body -----------------------------------------------------------------------


@pytest.mark.parametrize("field", ["operator_name", "author", "as"])
def test_a_body_naming_an_identity_is_refused_as_an_unknown_field(field: str) -> None:
    refusal = parse_approval_decision(_body(**{field: "P/someone"}))

    assert refusal == (f"unknown field '{field}': the relay identity comes from the access file")


@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"operation_id": None}, "'operation_id'"),
        ({"operation_id": "  "}, "'operation_id'"),
        ({"operation_id": "x" * 513}, "'operation_id'"),
        ({"operation_id": "op\n1"}, "'operation_id'"),
        ({"intent_sha256": "A" * 64}, "'intent_sha256'"),
        ({"intent_sha256": "a" * 63}, "'intent_sha256'"),
        ({"intent_sha256": 7}, "'intent_sha256'"),
        ({"decision": "requested"}, "'decision'"),
        ({"decision": None}, "'decision'"),
        ({"reason": None}, "'reason'"),
        ({"reason": 3}, "'reason'"),
    ],
)
def test_each_malformed_field_is_named(changes: dict[str, object], named: str) -> None:
    body = _body(**changes)
    for key, value in changes.items():
        if value is None:
            del body[key]

    refusal = parse_approval_decision(body)

    assert isinstance(refusal, str)
    assert refusal.startswith(named)


def test_the_reason_is_refused_over_its_byte_bound_never_trimmed() -> None:
    at_bound = parse_approval_decision(_body(reason="r" * MAX_APPROVAL_REASON_BYTES))
    over = parse_approval_decision(_body(reason="r" * (MAX_APPROVAL_REASON_BYTES + 1)))
    wide = parse_approval_decision(_body(reason="é" * (MAX_APPROVAL_REASON_BYTES // 2 + 1)))

    assert isinstance(at_bound, ApprovalDecision)
    assert at_bound.reason == "r" * MAX_APPROVAL_REASON_BYTES
    assert over == f"'reason' exceeds {MAX_APPROVAL_REASON_BYTES} UTF-8 bytes"
    assert wide == over


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_a_valid_body_parses_and_an_empty_reason_is_kept(decision: str) -> None:
    parsed = parse_approval_decision(_body(decision=decision, reason=""))

    assert parsed == ApprovalDecision("op-1", DIGEST, decision, "")


# --- the store checks ---------------------------------------------------------------


def _link(seq: int, kind: str, actor: str) -> ChainLink:
    return ChainLink(seq, float(seq), kind, actor, {})


def _report(
    chain: Sequence[ChainLink],
    *,
    digest: str = DIGEST,
    recomputed: str = DIGEST,
    check: str = "matches",
) -> OperationReport:
    return OperationReport(
        operation_id="op-1",
        task_id="T1",
        state="awaiting approval",
        intent_sha256=digest,
        digest_check=check,
        attempts=(),
        chain=tuple(chain),
        as_of_seq=9,
        as_of=9.0,
        recomputed_sha256=recomputed,
    )


DECLARED = _report([_link(3, "declared", "P/worker"), _link(4, "approval", "P/worker")])
VALID = ApprovalDecision("op-1", DIGEST, "approved", "")


def test_an_operation_without_a_declaration_is_not_found() -> None:
    refusal = approval_refusal(VALID, _report([]), "P/approver")

    assert refusal == (HTTPStatus.NOT_FOUND, "operation 'op-1' has no declaration")


@pytest.mark.parametrize(
    ("decision", "report"),
    [
        (ApprovalDecision("op-1", OTHER, "approved", ""), DECLARED),
        (VALID, _report(DECLARED.chain, recomputed=OTHER, check="differs")),
        (VALID, _report(DECLARED.chain, digest=OTHER, check="differs")),
        (VALID, _report(DECLARED.chain, recomputed="", check="no intent")),
        (VALID, _report(DECLARED.chain, check="differs")),
    ],
)
def test_a_digest_that_is_not_equal_three_ways_is_a_conflict(
    decision: ApprovalDecision, report: OperationReport
) -> None:
    refusal = approval_refusal(decision, report, "P/approver")

    assert refusal is not None
    assert refusal[0] is HTTPStatus.CONFLICT
    assert "not all equal" in refusal[1]


def test_the_intent_actor_cannot_decide_its_own_intent() -> None:
    refusal = approval_refusal(VALID, DECLARED, "P/worker")

    assert refusal is not None
    assert refusal[0] is HTTPStatus.CONFLICT
    assert "is the intent's actor" in refusal[1]


@pytest.mark.parametrize("identity", ["Q/approver", "operator:DASH", "PP/approver"])
def test_an_identity_of_another_project_cannot_decide(identity: str) -> None:
    refusal = approval_refusal(VALID, DECLARED, identity)

    assert refusal is not None
    assert refusal[0] is HTTPStatus.CONFLICT
    assert "not of the actor's project 'P'" in refusal[1]


def test_the_declarer_comes_from_the_declaration_not_from_the_first_link() -> None:
    early = _report([_link(2, "approval", "P/approver-alice"), _link(5, "declared", "P/worker")])

    assert early.declaration == _link(5, "declared", "P/worker")
    assert approval_refusal(VALID, early, "P/approver-alice") is None
    refusal = approval_refusal(VALID, early, "P/worker")
    assert refusal is not None
    assert "is the intent's actor" in refusal[1]


# --- the route with injected readers ------------------------------------------------


class _Readers:
    """Record every roster read and relay the route makes."""

    def __init__(self, *, online: Sequence[str] = (), roster_error: Exception | None = None):
        self.online = list(online)
        self.roster_error = roster_error
        self.roster_reads = 0
        self.relayed: list[tuple[RelayPlan, str]] = []

    def online_agents(self) -> list[str]:
        self.roster_reads += 1
        if self.roster_error is not None:
            raise self.roster_error
        return self.online

    def relay(self, plan: RelayPlan, identity: str) -> FeedResponse:
        self.relayed.append((plan, identity))
        return FeedResponse(HTTPStatus.OK, b"{}", "application/json")

    def bindings(self, store: Path | None) -> ApprovalDecideBindings:
        return ApprovalDecideBindings(store, None, self.online_agents, self.relay)


def _document(response: FeedResponse) -> dict[str, Any]:
    document = json.loads(response.body)
    assert isinstance(document, dict)
    return document


def _with_report(monkeypatch: pytest.MonkeyPatch, report: OperationReport) -> None:
    def read(_store: object, _operation: str, *, key_file: object = None) -> OperationReport:
        del key_file
        return report

    monkeypatch.setattr(decide_module, "run_operation_report", read)


def test_a_malformed_body_is_a_plain_400_before_any_read(tmp_path: Path) -> None:
    readers = _Readers()

    response = decide_approval(_body(author="P/x"), "P/approver", readers.bindings(tmp_path))

    assert response.status is HTTPStatus.BAD_REQUEST
    assert response.content_type == "text/plain"
    assert readers.roster_reads == 0
    assert readers.relayed == []


def test_a_start_without_a_store_refuses_with_404_naming_the_flag() -> None:
    readers = _Readers()

    response = decide_approval(_body(), "P/approver", readers.bindings(None))

    assert response.status is HTTPStatus.NOT_FOUND
    assert _document(response) == {
        "action": APPROVAL_DECIDE_ACTION,
        "operation_id": "op-1",
        "status": REFUSED,
        "detail": (
            "approval decisions need --feeds-db: the declaration is read from the event store"
        ),
        "ok": False,
    }
    assert readers.relayed == []


def test_an_unreadable_store_refuses_with_503(tmp_path: Path) -> None:
    readers = _Readers()

    response = decide_approval(_body(), "P/approver", readers.bindings(tmp_path / "absent.db"))

    assert response.status is HTTPStatus.SERVICE_UNAVAILABLE
    assert _document(response)["detail"] == "the event store could not be read; nothing was relayed"
    assert readers.roster_reads == 0
    assert readers.relayed == []


def test_a_store_refusal_is_answered_before_the_roster_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_report(monkeypatch, DECLARED)
    readers = _Readers()

    response = decide_approval(_body(), "P/worker", readers.bindings(tmp_path))

    assert response.status is HTTPStatus.CONFLICT
    assert _document(response)["status"] == REFUSED
    assert readers.roster_reads == 0
    assert readers.relayed == []


@pytest.mark.parametrize(
    "error", [RuntimeError("hub down"), TimeoutError("busy"), OSError("refused")]
)
def test_an_unreadable_roster_refuses_with_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _with_report(monkeypatch, DECLARED)
    readers = _Readers(roster_error=error)

    response = decide_approval(_body(), "P/approver", readers.bindings(tmp_path))

    assert response.status is HTTPStatus.SERVICE_UNAVAILABLE
    assert _document(response)["detail"] == (
        "the hub's roster could not be read; nothing was relayed"
    )
    assert readers.relayed == []


def test_a_relay_identity_online_as_an_agent_is_refused_with_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_report(monkeypatch, DECLARED)
    readers = _Readers(online=["P/reviewer", "P/approver"])

    response = decide_approval(_body(), "P/approver", readers.bindings(tmp_path))

    assert response.status is HTTPStatus.CONFLICT
    assert "is online as an agent" in _document(response)["detail"]
    assert readers.relayed == []


def test_a_decision_that_passes_every_check_is_relayed_under_the_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_report(monkeypatch, DECLARED)
    readers = _Readers(online=["P/reviewer"])

    response = decide_approval(
        _body(decision="rejected", reason="not yet"), "P/approver", readers.bindings(tmp_path)
    )

    assert response.status is HTTPStatus.OK
    assert readers.roster_reads == 1
    [(plan, identity)] = readers.relayed
    assert identity == "P/approver"
    assert plan.action == APPROVAL_DECIDE_ACTION
    assert plan.extra == {"operation_id": "op-1", "intent_sha256": DIGEST, "decision": "rejected"}
    calls: list[tuple[str, str, str]] = []

    class _Relay:
        async def relay_approval_decision(self, digest: str, state: str, reason: str) -> Any:
            calls.append((digest, state, reason))
            return None

    asyncio.run(plan.run(cast(OperatorRelay, _Relay())))
    assert calls == [(DIGEST, "rejected", "not yet")]


# --- the relay ----------------------------------------------------------------------


class _Agent:
    """A stand-in client: it records the note and lets the test answer as the hub."""

    def __init__(self, name: str, collect: Callable[[dict[str, Any]], Any], **_: object) -> None:
        self.name = name
        self.callback = collect
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason: str | None = None
        self.posted: list[tuple[str, str, str]] = []
        self.answer: Callable[[_Agent, str, str, str], list[dict[str, Any]]] = lambda *_: []

    async def connect(self) -> None:
        while True:
            await asyncio.sleep(0.02)

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return True

    async def request_who(self, hub: str | None = None) -> None:
        del hub
        await self.callback({"type": MessageType.WHO_SNAPSHOT})

    async def post_progress(self, task_id: str, text: str, *, kind: str = "note") -> None:
        self.posted.append((task_id, text, kind))
        for frame in self.answer(self, task_id, text, kind):
            await self.callback(frame)


def _relay_with(
    answer: Callable[[_Agent, str, str, str], list[dict[str, Any]]],
) -> tuple[OperatorRelay, list[_Agent]]:
    agents: list[_Agent] = []

    def make(name: str, collect: Callable[[dict[str, Any]], Any], **kwargs: object) -> Any:
        agent = _Agent(name, collect, **kwargs)
        agent.answer = answer
        agents.append(agent)
        return agent

    relay = OperatorRelay(
        uri="ws://hub.test",
        operator_name="P/approver",
        ready_timeout=0.5,
        response_timeout=0.3,
        agent_factory=make,
    )
    return relay, agents


def _posted(
    agent: _Agent, subject: str, text: str, kind: str, changes: dict[str, object] | None = None
) -> dict[str, Any]:
    body = {"task_id": subject, "author": agent.name, "kind": kind, "text": text, **(changes or {})}
    return {"type": MessageType.LEDGER_PROGRESS_POSTED, "note": body}


def test_the_relay_posts_the_cli_note_and_says_recorded() -> None:
    relay, agents = _relay_with(
        lambda agent, subject, text, kind: [_posted(agent, subject, text, kind)]
    )

    outcome = asyncio.run(relay.relay_approval_decision(DIGEST, "approved", "line\nbreak"))

    subject = f"sha256:{DIGEST}"
    text = format_approval_note(subject=subject, state="approved", reason="line\nbreak")
    assert agents[0].posted == [(subject, text, APPROVAL_NOTE_KIND)]
    assert outcome.status == RECORDED
    assert outcome.ok is True
    assert outcome.detail == (
        f"approved recorded for {subject}; the hub judges it when asked for an attempt"
    )
    assert agents[0].running is False


@pytest.mark.parametrize(
    "foreign",
    [
        {"author": "P/someone-else"},
        {"task_id": f"sha256:{OTHER}"},
        {"kind": "note"},
        {"text": "approval subject=sha256:x state=approved"},
    ],
)
def test_the_relay_settles_only_on_its_own_note(foreign: dict[str, object]) -> None:
    relay, _agents = _relay_with(
        lambda agent, subject, text, kind: [_posted(agent, subject, text, kind, foreign)]
    )

    outcome = asyncio.run(relay.relay_approval_decision(DIGEST, "approved", ""))

    assert outcome.status == UNREACHABLE
    assert outcome.detail == "hub confirmed no approval note in time"


def test_a_note_the_hub_refuses_is_rejected_with_its_words() -> None:
    error = {
        "type": MessageType.ERROR,
        "target": "P/approver",
        "payload": "approval gate: no accepted verdict",
    }
    relay, _agents = _relay_with(lambda *_: [error])

    outcome = asyncio.run(relay.relay_approval_decision(DIGEST, "approved", ""))

    assert outcome.status == REJECTED
    assert outcome.detail == "approval gate: no accepted verdict"


def test_an_acl_refusal_is_denied() -> None:
    error = {
        "type": MessageType.ERROR,
        "target": "P/approver",
        "acl_reason": "no progress rule",
        "acl_decision": "deny",
    }
    relay, _agents = _relay_with(lambda *_: [error])

    outcome = asyncio.run(relay.relay_approval_decision(DIGEST, "rejected", "no"))

    assert outcome.status == DENIED
    assert outcome.ok is False


# --- a real hub, its journal and a dashboard ----------------------------------------

_PRINCIPALS = {
    "alice": "P/approver-alice",
    "worker": scene.WORKER,
    "other": "Q/approver",
    "bob": "P/approver-bob",
}


def _token(principal: str) -> str:
    return f"{principal}-token-that-is-at-least-32-bytes-long"


def _access_file(tmp_path: Path) -> Path:
    principals = []
    for principal, identity in _PRINCIPALS.items():
        token_file = tmp_path / f"{principal}.token"
        token_file.write_text(_token(principal) + "\n", encoding="utf-8")
        token_file.chmod(0o600)
        principals.append(
            {
                "id": principal,
                "role": "operator",
                "token_file": token_file.name,
                "operator_name": identity,
            }
        )
    path = tmp_path / "dashboard-access.json"
    path.write_text(json.dumps({"version": 1, "principals": principals}), encoding="utf-8")
    path.chmod(0o600)
    return path


def _dashboard(uri: str, store: Path, **access: Any) -> DashboardServer:
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri=uri,
        name="DASH",
        token=None,
        ready_timeout=2.0,
        response_timeout=2.0,
        refresh_seconds=5,
        allow_non_loopback=False,
        operator=True,
        reliability_db=store,
        **access,
    )


def _post(server: DashboardServer, bearer: str, body: dict[str, object]) -> tuple[int, str]:
    request = Request(
        server.url(APPROVAL_DECIDE_PATH),
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Connection": "close",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer}",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _approval_notes(store: Path) -> list[dict[str, Any]]:
    events = EventStore(store)
    try:
        return [
            row.payload
            for row in events.read_all()
            if row.payload.get("kind") == APPROVAL_NOTE_KIND
        ]
    finally:
        events.close()


async def _refusals(
    servers: tuple[DashboardServer, DashboardServer], store: Path, digest: str
) -> None:
    """Ask every refused decision; none leaves an approval note."""
    access, compatibility = servers
    body = _body(intent_sha256=digest)
    cases = [
        (access, _token("alice"), {**body, "operator_name": "P/approver-alice"}, 400),
        (access, _token("alice"), _body(intent_sha256=OTHER), 409),
        (access, _token("alice"), _body(operation_id="op-missing", intent_sha256=digest), 404),
        (access, _token("worker"), body, 409),
        (access, _token("other"), body, 409),
        (access, _token("bob"), body, 409),
        (compatibility, "compat-token-that-is-at-least-32-bytes", body, 409),
    ]
    for server, bearer, request, expected in cases:
        status, text = await asyncio.to_thread(_post, server, bearer, request)
        assert status == expected, text
    assert _approval_notes(store) == []


async def _scenario(tmp_path: Path) -> None:
    store_path = tmp_path / "hub.db"
    store = EventStore(store_path)
    try:
        hub = SynapseHub(journal=store, acl_policy=journal_helpers.policy())
        async with running_hub(hub) as (_hub, uri):
            worker = await connect_agent(scene.WORKER, uri)
            reviewer = await connect_agent(scene.REVIEWER, uri)
            bob = await connect_agent("P/approver-bob", uri)
            try:
                epoch = await scene.claim(worker)
                digest = await scene.declare(worker, epoch, intent=scene.statement())
                await scene.review(reviewer, digest)
                servers = (
                    _dashboard(uri, store_path, dashboard_access_file=_access_file(tmp_path)),
                    _dashboard(
                        uri, store_path, dashboard_token="compat-token-that-is-at-least-32-bytes"
                    ),
                )
                try:
                    await _refusals(servers, store_path, digest)
                    status, text = await asyncio.to_thread(
                        _post, servers[0], _token("alice"), _body(intent_sha256=digest)
                    )
                finally:
                    for server in servers:
                        server.close()
                assert status == 200, text
                answer = json.loads(text)
                assert answer["status"] == RECORDED
                assert answer["ok"] is True
                assert "approved for" not in answer["detail"]
                [note] = _approval_notes(store_path)
                assert note["author"] == "P/approver-alice"
                attempt = await scene.attempt(worker, epoch, digest, "after-cockpit")
                assert attempt["answer"] == "approved_fresh", attempt
            finally:
                await close_agents(worker, reviewer, bob)
    finally:
        store.close()
    report = run_operation_report(store_path, scene.OPERATION)
    approvals = [link for link in report.chain if link.kind == "approval"]
    assert [link.actor for link in approvals] == ["P/approver-alice"]


def test_a_cockpit_decision_is_journalled_and_admits_the_next_attempt(tmp_path: Path) -> None:
    asyncio.run(_scenario(tmp_path))


def test_a_real_dashboard_refuses_when_the_hub_roster_cannot_be_read(tmp_path: Path) -> None:
    store_path = tmp_path / "hub.db"
    journal_helpers.journal_every_state(store_path)
    report = run_operation_report(store_path, "op-awaiting")
    server = _dashboard(
        "ws://127.0.0.1:1", store_path, dashboard_access_file=_access_file(tmp_path)
    )
    try:
        status, text = _post(
            server,
            _token("alice"),
            _body(operation_id="op-awaiting", intent_sha256=report.intent_sha256),
        )
    finally:
        server.close()

    assert status == 503
    assert json.loads(text)["detail"] == "the hub's roster could not be read; nothing was relayed"
