# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — POST /setup/plan: one plan bound to the command line's plan, and its receipt
"""Every answer of the plan route after the caller is known is a receipt; none before it is."""

from __future__ import annotations

import io
import json
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

import setup_plan_fixture as fixture
import synapse_channel.dashboard_setup_http as setup_http
from synapse_channel.dashboard import DashboardServer
from synapse_channel.dashboard_access import (
    DashboardAccessPolicy,
    DashboardCredential,
    DashboardPrincipal,
    capabilities_for_role,
)
from synapse_channel.dashboard_access_http import AccessHttpDecision
from synapse_channel.dashboard_operator import WriteRateLimiter
from synapse_channel.dashboard_setup_contract import MAX_SETUP_REQUEST_BYTES, SetupPostureDecision
from synapse_channel.dashboard_setup_http import SetupServing
from synapse_channel.dashboard_setup_plan_http import (
    SETUP_PLAN_PATH,
    SetupPlanBindings,
    setup_plan_response,
)
from synapse_channel.dashboard_setup_planner import SetupPlanStore
from synapse_channel.dashboard_setup_receipts import SetupReceipt, SetupReceiptStore
from synapse_channel.dashboard_setup_write_http import SetupWriteRequest, read_setup_write_request
from synapse_channel.setup_profiles import SetupProfile
from test_dashboard_setup_http import TOKENS, _access_file, _fake_inspect, _receipts, _start

HOST = "127.0.0.1:8765"
ORIGIN = "http://127.0.0.1:8765"
ADMIN = "a" * 40
OPERATOR = "o" * 40


def _policy() -> DashboardAccessPolicy:
    admin = DashboardPrincipal(
        "setup_admin",
        "admin",
        capabilities_for_role("admin", operator_armed=True, setup_armed=True),
        "harbour/admin",
    )
    operator = DashboardPrincipal(
        "ops",
        "operator",
        capabilities_for_role("operator", operator_armed=True, setup_armed=True),
        "harbour/ops",
    )
    credentials = (
        DashboardCredential(admin, ADMIN.encode()),
        DashboardCredential(operator, OPERATOR.encode()),
    )
    return DashboardAccessPolicy(credentials, None, True)


def _serving(receipts: Path | None, plans: SetupPlanStore | None = None) -> SetupServing:
    return SetupServing(
        SetupPostureDecision(True, "ready"),
        receipts,
        fixture.profile(),
        plans or SetupPlanStore(),
    )


def _body() -> bytes:
    document = {"version": 1, "profile": "local-single-user", "request_id": str(uuid4())}
    return json.dumps(document).encode()


def _request(
    *,
    token: str | None = ADMIN,
    origins: tuple[str, ...] = (ORIGIN,),
    content_type: str = "application/json",
    body: bytes | None = None,
) -> SetupWriteRequest:
    return SetupWriteRequest(
        authorization=None if token is None else f"Bearer {token}",
        host=HOST,
        origins=origins,
        content_type=content_type,
        body=_body() if body is None else body,
    )


def _inspect(statuses: dict[str, str] | None = None, **options: Any) -> Any:
    def inspect(_profile: SetupProfile) -> dict[str, object]:
        return fixture.inspection(statuses, **options)

    return inspect


def _bindings(
    serving: SetupServing,
    *,
    inspect: Any = None,
    calls: int = 30,
) -> SetupPlanBindings:
    return SetupPlanBindings(
        _policy(),
        serving,
        inspect or _inspect(fixture.HUB_AND_WAITER_DOWN, hub_pid=4321),
        WriteRateLimiter(max_calls=calls, window_seconds=60.0),
        clock=lambda: 1_800_000_000.0,
    )


def _chain(receipts: Path) -> tuple[SetupReceipt, ...]:
    with SetupReceiptStore(receipts) as chain:
        return chain.read_all()


def _text(decision: AccessHttpDecision) -> str:
    return decision.body.decode()


# --- before the caller is known: answers, and no receipt ----------------------------


def test_a_start_that_does_not_serve_setup_answers_404_naming_the_reason() -> None:
    serving = SetupServing(SetupPostureDecision(False, "receipt_store_unavailable"))

    decision = setup_plan_response(_request(), _bindings(serving))

    assert decision.status == 404
    assert _text(decision) == "setup not served: receipt_store_unavailable"


@pytest.mark.parametrize(
    "origins",
    [
        (),
        (ORIGIN, ORIGIN),
        ("http://evil.example",),
        ("https://127.0.0.1:8765",),
        ("http://127.0.0.1:8766",),
        ("null",),
    ],
)
def test_a_write_without_exactly_this_dashboards_origin_is_refused_unrecorded(
    tmp_path: Path, origins: tuple[str, ...]
) -> None:
    receipts = _receipts(tmp_path)

    decision = setup_plan_response(_request(origins=origins), _bindings(_serving(receipts)))

    assert decision.status == 403
    assert _text(decision) == "setup writes require this dashboard's own Origin"
    assert _chain(receipts) == ()


def test_the_origin_is_compared_without_case(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)

    request = _request(origins=("HTTP://127.0.0.1:8765",))
    decision = setup_plan_response(request, _bindings(_serving(receipts)))

    assert decision.status == 200


@pytest.mark.parametrize(
    ("plan_request", "status", "text"),
    [
        (_request(token=None), 401, "bearer required"),
        (_request(token="x" * 40), 401, "bearer required"),
        (_request(content_type="text/plain"), 415, "setup writes require application/json"),
        (_request(body=b"{}"), 400, "setup plan request refused: invalid_fields"),
        (_request(body=b""), 400, "setup plan request refused: body_size"),
    ],
)
def test_an_unknown_caller_or_a_malformed_request_is_refused_unrecorded(
    tmp_path: Path, plan_request: SetupWriteRequest, status: int, text: str
) -> None:
    receipts = _receipts(tmp_path)

    decision = setup_plan_response(plan_request, _bindings(_serving(receipts)))

    assert (decision.status, _text(decision)) == (status, text)
    assert decision.authenticate is (status == 401)
    assert _chain(receipts) == ()


# --- a known caller: every answer is a receipt -------------------------------------


def _only(receipts: Path) -> SetupReceipt:
    [receipt] = _chain(receipts)
    return receipt


def _plan_free(receipt: SetupReceipt) -> tuple[object, ...]:
    draft = receipt.draft
    return (draft.plan_id, draft.plan_digest, draft.outcome, draft.reason, draft.capability)


def test_a_spent_rate_limit_is_a_rate_limited_receipt(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    bindings = _bindings(_serving(receipts), calls=1)
    bindings.rate_limiter.allow()

    decision = setup_plan_response(_request(), bindings)

    assert (decision.status, _text(decision)) == (429, "setup plan refused: rate_limit")
    receipt = _only(receipts)
    assert _plan_free(receipt) == (None, None, "rate_limited", "rate_limit", "setup_plan")
    assert receipt.draft.configuration_generation is None
    assert receipt.draft.principal_id == "setup_admin"


def test_a_caller_without_setup_plan_is_a_denied_receipt(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)

    decision = setup_plan_response(_request(token=OPERATOR), _bindings(_serving(receipts)))

    assert (decision.status, _text(decision)) == (403, "setup plan refused: capability_denied")
    receipt = _only(receipts)
    assert _plan_free(receipt) == (None, None, "denied", "capability_denied", "setup_plan")
    assert receipt.draft.principal_id == "ops"


def _failing_inspection(_profile: SetupProfile) -> dict[str, object]:
    raise RuntimeError("the hub at ws://127.0.0.1:8876 did not answer")


@pytest.mark.parametrize(
    "inspect",
    [
        _failing_inspection,
        _inspect(target={"uri": "ws://10.0.0.5:9999", "project": "P", "identity": "P/x"}),
    ],
)
def test_an_inspection_or_a_plan_that_fails_is_a_refused_receipt(
    tmp_path: Path, inspect: Any
) -> None:
    receipts = _receipts(tmp_path)

    decision = setup_plan_response(_request(), _bindings(_serving(receipts), inspect=inspect))

    assert (decision.status, _text(decision)) == (503, "setup plan refused: plan_issue_failed")
    receipt = _only(receipts)
    assert _plan_free(receipt) == (None, None, "refused", "plan_issue_failed", "setup_plan")
    assert "ws://" not in json.dumps(receipt.browser_projection())


def test_a_reused_request_id_is_a_conflict_and_a_receipt(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    bindings = _bindings(_serving(receipts))
    body = _body()

    first = setup_plan_response(_request(body=body), bindings)
    second = setup_plan_response(_request(body=body), bindings)

    assert first.status == 200
    assert (second.status, _text(second)) == (409, "setup plan refused: plan_request_reused")
    planned, reused = _chain(receipts)
    assert planned.draft.outcome == "planned"
    assert _plan_free(reused) == (None, None, "refused", "plan_request_reused", "setup_plan")
    assert reused.draft.configuration_generation == planned.draft.configuration_generation


def test_a_full_plan_store_is_unavailable_and_a_receipt(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    bindings = _bindings(_serving(receipts, SetupPlanStore(capacity=1)))

    assert setup_plan_response(_request(), bindings).status == 200
    decision = setup_plan_response(_request(), bindings)

    assert (decision.status, _text(decision)) == (503, "setup plan refused: plan_store_full")
    assert _chain(receipts)[-1].draft.reason == "plan_store_full"


@pytest.mark.parametrize("token", ["short", "spaces are not tokens"])
def test_a_failing_token_factory_is_a_server_error_and_a_receipt(
    tmp_path: Path, token: str
) -> None:
    receipts = _receipts(tmp_path)
    plans = SetupPlanStore(token_factory=lambda _size: token)

    decision = setup_plan_response(_request(), _bindings(_serving(receipts, plans)))

    assert (decision.status, _text(decision)) == (500, "setup plan refused: plan_issue_failed")
    assert _chain(receipts)[-1].draft.reason == "plan_issue_failed"


def test_a_token_collision_is_a_server_error_and_a_receipt(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    tokens = iter(["P" * 24, "N" * 24, "P" * 24, "M" * 24])
    plans = SetupPlanStore(token_factory=lambda _size: next(tokens))
    bindings = _bindings(_serving(receipts, plans))

    assert setup_plan_response(_request(), bindings).status == 200
    decision = setup_plan_response(_request(), bindings)

    assert (decision.status, _text(decision)) == (500, "setup plan refused: plan_issue_failed")


def test_an_issued_plan_is_bound_recorded_and_holds_nothing_measured(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    plans = SetupPlanStore()
    cli = fixture.cli_plan(fixture.HUB_AND_WAITER_DOWN, hub_pid=4321)

    decision = setup_plan_response(_request(), _bindings(_serving(receipts, plans)))

    assert decision.status == 200
    issued = json.loads(decision.body)
    assert issued["cli_plan_digest"] == cli["plan_digest"]
    assert issued["profile"] == "local-single-user"
    assert issued["mutates_local_state"] is True
    assert [effect["process_id"] for effect in issued["effects"]] == [4321, None]
    for measured in ("/home/someone", "/usr/bin", "ws://", "DEMO", "claude-one"):
        assert measured not in decision.body.decode()
    receipt = _only(receipts)
    assert receipt.draft.outcome == "planned"
    assert receipt.draft.plan_id == issued["plan_id"]
    assert receipt.draft.plan_digest == issued["plan_digest"]
    assert receipt.draft.configuration_generation == issued["configuration_generation"]
    assert receipt.draft.template_version == "synapse-setup.v1"
    assert [effect.as_dict() for effect in receipt.draft.effects] == [
        {
            "kind": "establish_local_loopback_hub",
            "target": "synapse-hub.service",
            "before": "unsatisfied",
            "after": "not_checked",
        },
        {
            "kind": "establish_identity_waiter",
            "target": "synapse-arm@.service",
            "before": "unsatisfied",
            "after": "not_checked",
        },
    ]
    assert plans.record_count == 1


def test_a_blocked_or_unverifiable_effect_is_recorded_as_such(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    inspect = _inspect({"package": "fail", "waiter": "unavailable"})

    decision = setup_plan_response(_request(), _bindings(_serving(receipts), inspect=inspect))

    assert decision.status == 200
    effects = {effect.kind: effect for effect in _only(receipts).draft.effects}
    assert effects["install_synapse_package"].target == "install_synapse_package"
    assert effects["install_synapse_package"].before == "blocked"
    assert effects["establish_identity_waiter"].before == "blocked"


def test_an_unwritable_chain_answers_503_and_leaves_no_plan_behind(tmp_path: Path) -> None:
    receipts = _receipts(tmp_path)
    (receipts / "setup-receipts.db").mkdir()
    plans = SetupPlanStore()
    bindings = _bindings(_serving(receipts, plans))

    issued = setup_plan_response(_request(), bindings)
    refused = setup_plan_response(_request(token=OPERATOR), bindings)

    assert (issued.status, _text(issued)) == (503, "setup receipt chain unavailable")
    assert (refused.status, _text(refused)) == (503, "setup receipt chain unavailable")
    assert plans.record_count == 0


# --- reading the request -------------------------------------------------------------


def _headers(**values: str) -> Message:
    message = Message()
    for name, value in values.items():
        message[name.replace("_", "-")] = value
    return message


def test_the_request_is_read_with_every_origin_and_a_bounded_body() -> None:
    headers = _headers(Host=HOST, Content_Type="application/json", Content_Length="5")
    headers["Origin"] = ORIGIN
    headers["Origin"] = "http://evil.example"

    read = read_setup_write_request(headers, io.BytesIO(b"12345678"))

    assert read.origins == (ORIGIN, "http://evil.example")
    assert read.body == b"12345"
    assert read.host == HOST
    assert read.authorization is None


@pytest.mark.parametrize("length", [str(MAX_SETUP_REQUEST_BYTES + 1), "-3", "many", "0"])
def test_a_body_that_is_absent_or_over_the_bound_is_never_read(length: str) -> None:
    stream = io.BytesIO(b"x" * (MAX_SETUP_REQUEST_BYTES + 10))

    read = read_setup_write_request(_headers(Content_Length=length), stream)

    assert read.body == b""
    assert stream.tell() == 0
    assert read.origins == ()


# --- a real start ------------------------------------------------------------------


def _post(server: DashboardServer, token: str | None, *, origin: str | None) -> tuple[int, str]:
    headers = {"Connection": "close", "Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if origin is not None:
        headers["Origin"] = origin
    request = Request(server.url(SETUP_PLAN_PATH), data=_body(), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8")


def test_a_real_start_issues_plans_to_its_admin_and_records_every_known_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup_http, "inspect_setup", _fake_inspect)
    receipts = _receipts(tmp_path)
    server = _start(
        dashboard_access_file=_access_file(tmp_path),
        operator=True,
        setup_apply=True,
        setup_receipts=receipts,
    )
    try:
        origin = server.url("").rstrip("/")
        admin = _post(server, TOKENS["admin"], origin=origin)
        operator = _post(server, TOKENS["operator"], origin=origin)
        foreign = _post(server, TOKENS["admin"], origin="http://evil.example")
        anonymous = _post(server, None, origin=origin)
    finally:
        server.close()

    assert admin[0] == 200, admin[1]
    plan = json.loads(admin[1])
    assert plan["profile"] == "local-single-user"
    assert all(effect["disposition"] in {"planned", "blocked"} for effect in plan["effects"])
    assert "/home/alice" not in admin[1]
    assert operator == (403, "setup plan refused: capability_denied")
    assert foreign == (403, "setup writes require this dashboard's own Origin")
    assert anonymous == (401, "bearer required")
    assert [receipt.draft.outcome for receipt in _chain(receipts)] == ["planned", "denied"]


def test_a_start_that_does_not_arm_setup_answers_404(tmp_path: Path) -> None:
    server = _start(dashboard_access_file=_access_file(tmp_path), operator=True)
    try:
        origin = server.url("").rstrip("/")
        status, text = _post(server, TOKENS["admin"], origin=origin)
    finally:
        server.close()

    assert (status, text) == (404, "setup not served: unarmed")
