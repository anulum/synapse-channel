# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — POST /setup/apply on a temporary home, through the command line's executor
"""What a person confirmed is what is applied, once; every other answer says why not, as a receipt.

The executor runs for real on a temporary home and unit directory, against the
fake ``systemctl`` its own tests use, so no host service is touched.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Callable
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import setup_plan_fixture as fixture
import synapse_channel.dashboard_setup_apply_http as apply_module
import synapse_channel.dashboard_setup_http as setup_http
import synapse_channel.dashboard_setup_writes as writes_module
from synapse_channel.dashboard import DashboardServer
from synapse_channel.dashboard_access_http import AccessHttpDecision
from synapse_channel.dashboard_operator import WriteRateLimiter
from synapse_channel.dashboard_setup_apply_http import (
    SETUP_APPLY_PATH,
    SetupApplyBindings,
    setup_apply_response,
)
from synapse_channel.dashboard_setup_contract import SetupPlan, SetupPostureDecision
from synapse_channel.dashboard_setup_http import SetupServing
from synapse_channel.dashboard_setup_plan_http import SetupPlanBindings, setup_plan_response
from synapse_channel.dashboard_setup_planner import SetupPlanStore
from synapse_channel.dashboard_setup_receipts import SetupReceipt, SetupReceiptStore
from synapse_channel.dashboard_setup_write_http import STATUS_VERDICTS, SetupWriteRequest
from synapse_channel.dashboard_setup_writes import SetupWriteContext, setup_write_response
from synapse_channel.setup_authorization import SetupAuthorizationError
from synapse_channel.setup_ledger import (
    SETUP_LEDGER_DATABASE,
    SetupAuthorizationLedger,
    default_setup_ledger_dir,
)
from synapse_channel.setup_profiles import SetupProfile
from test_dashboard_setup_http import TOKENS, _access_file, _fake_inspect, _receipts, _start
from test_dashboard_setup_plan_http import ADMIN, OPERATOR, _policy
from test_setup_executor import _manager, _manager_commands, _manager_state, _process

HOST = "127.0.0.1:8765"
ORIGIN = "http://127.0.0.1:8765"
NOW = 1_800_000_000.0


class Host:
    """A temporary home, its fake service manager, and what the inspections see of it."""

    def __init__(self, tmp_path: Path, statuses: dict[str, str], *, hub_pid: int | None = None):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.systemctl = _manager(tmp_path)
        self.statuses = dict(statuses)
        self.hub_pid = hub_pid
        self.executor_statuses: dict[str, str] | None = None
        self.executor_hub_pid: int | None = None
        self.applied = False
        self.after_statuses: dict[str, str] = {}
        self.unreadable_after = False
        self.python = fixture.PYTHON
        self.receipts = _receipts(tmp_path)
        self.plans = SetupPlanStore()
        self.serving = SetupServing(
            SetupPostureDecision(True, "ready"), self.receipts, fixture.profile(), self.plans
        )

    def inspection(self, statuses: dict[str, str], hub_pid: int | None) -> dict[str, object]:
        return fixture.inspection(
            statuses, hub_pid=hub_pid, systemctl=str(self.systemctl), python=self.python
        )

    def inspect(self, _profile: SetupProfile) -> dict[str, object]:
        """The dashboard's inspection: the host as planned, and all well once the executor ran."""
        if self.applied and self.unreadable_after:
            raise RuntimeError("the host could not be read after the apply")
        if self.applied:
            return self.inspection(self.after_statuses, None)
        return self.inspection(self.statuses, self.hub_pid)

    async def executor_inspect(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        """The executor's own fresh inspection, just before its first effect."""
        self.applied = True
        statuses = self.statuses if self.executor_statuses is None else self.executor_statuses
        hub_pid = self.hub_pid if self.executor_hub_pid is None else self.executor_hub_pid
        return self.inspection(statuses, hub_pid)

    def chain(self) -> tuple[SetupReceipt, ...]:
        with SetupReceiptStore(self.receipts) as chain:
            return chain.read_all()


def _request(body: dict[str, object], *, token: str = ADMIN) -> SetupWriteRequest:
    return SetupWriteRequest(
        f"Bearer {token}", HOST, (ORIGIN,), "application/json", json.dumps(body).encode()
    )


def _plan(host: Host, *, clock: Callable[[], float] = lambda: NOW) -> dict[str, Any]:
    body = {"version": 1, "profile": "local-single-user", "request_id": str(uuid4())}
    limiter = WriteRateLimiter(max_calls=100, window_seconds=60.0)
    bindings = SetupPlanBindings(_policy(), host.serving, host.inspect, limiter, clock=clock)
    decision = setup_plan_response(_request(body), bindings)
    assert decision.status == 200, decision.body
    plan: dict[str, Any] = json.loads(decision.body)
    return plan


def _confirm(plan: dict[str, Any], **changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "version": 1,
        "request_id": plan["request_id"],
        "plan_id": plan["plan_id"],
        "plan_digest": plan["plan_digest"],
        "confirmation_nonce": plan["confirmation_nonce"],
        "confirm": True,
    }
    body.update(changes)
    return body


def _limiter(calls: int) -> WriteRateLimiter:
    """A limiter allowing ``calls`` writes; at zero, one that is already spent."""
    limiter = WriteRateLimiter(max_calls=max(1, calls), window_seconds=60.0)
    if calls == 0:
        limiter.allow()
    return limiter


def _apply(
    host: Host,
    body: dict[str, object],
    *,
    token: str = ADMIN,
    clock: Callable[[], float] = lambda: NOW + 1,
    calls: int = 100,
    environment: dict[str, str] | None = None,
) -> tuple[int, str]:
    bindings = SetupApplyBindings(
        _policy(),
        host.serving,
        host.inspect,
        _limiter(calls),
        environment or {"HOME": str(host.home)},
        inspection_runner=host.executor_inspect,
        clock=clock,
    )
    decision = setup_apply_response(_request(body, token=token), bindings)
    return int(decision.status or 0), decision.body.decode()


def _outcomes(host: Host) -> list[tuple[str, str]]:
    return [(item.draft.outcome, item.draft.reason) for item in host.chain()]


def _commands(host: Host) -> list[list[str]]:
    """Every command the fake service manager ran; none when it never ran."""
    if not (host.systemctl.parent / "manager-log.jsonl").exists():
        return []
    return _manager_commands(host.systemctl)


def _enabled(host: Host) -> list[list[str]]:
    return [command for command in _commands(host) if "enable" in command]


# --- the apply ---------------------------------------------------------------------


def test_a_confirmed_plan_is_applied_once_through_the_executor(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)

    status, body = _apply(host, _confirm(plan))

    assert status == 200, body
    receipt = json.loads(body)
    assert (receipt["outcome"], receipt["reason"]) == ("applied", "none")
    assert receipt["plan_id"] == plan["plan_id"]
    assert receipt["capability"] == "setup_apply"
    assert [(e["kind"], e["before"], e["after"]) for e in receipt["effects"]] == [
        ("establish_local_loopback_hub", "unsatisfied", "satisfied"),
        ("establish_identity_waiter", "unsatisfied", "satisfied"),
    ]
    assert _outcomes(host) == [("planned", "none"), ("authorised", "none"), ("applied", "none")]
    units = host.home / ".config" / "systemd" / "user"
    assert (units / "synapse-hub.service").exists()
    assert (units / "synapse-arm@.service").exists()
    assert len(_enabled(host)) == 2
    ledger_directory = default_setup_ledger_dir(env={"HOME": str(host.home)})
    assert ledger_directory.is_relative_to(host.home)
    assert (ledger_directory / SETUP_LEDGER_DATABASE).is_file()  # written by the executor's reserve
    with SetupAuthorizationLedger(ledger_directory) as ledger:
        assert ledger.get("0" * 64) is None
    for value in (str(host.home), "/usr/bin", "ws://", "DEMO"):
        assert value not in body

    again = _apply(host, _confirm(plan))

    assert again == (409, "setup apply refused: plan_replayed")
    assert _outcomes(host)[-1] == ("refused", "plan_replayed")
    assert host.chain()[-1].draft.configuration_generation is None
    assert len(_enabled(host)) == 2


def test_an_apply_whose_effect_is_not_satisfied_after_is_partial(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)
    host.after_statuses = {"waiter": "fail"}

    status, body = _apply(host, _confirm(plan))

    assert status == 200, body
    receipt = json.loads(body)
    assert (receipt["outcome"], receipt["reason"]) == ("partial", "postcondition_unverified")
    assert [effect["after"] for effect in receipt["effects"]] == ["satisfied", "unsatisfied"]


def test_an_unreadable_host_after_the_apply_leaves_every_after_unverifiable(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)
    host.unreadable_after = True

    status, body = _apply(host, _confirm(plan))

    receipt = json.loads(body)
    assert status == 200
    assert (receipt["outcome"], receipt["reason"]) == ("partial", "postcondition_unverified")
    assert {effect["after"] for effect in receipt["effects"]} == {"unverifiable"}


def test_a_failed_effect_that_was_recovered_is_refused_effect_failed(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)
    (host.systemctl.parent / "manager-fail").write_text("enable", encoding="utf-8")

    status, body = _apply(host, _confirm(plan))

    receipt = json.loads(body)
    assert status == 200, body
    assert (receipt["outcome"], receipt["reason"]) == ("refused", "effect_failed")
    assert not (host.home / ".config" / "systemd" / "user" / "synapse-hub.service").exists()


def test_a_recovery_that_failed_is_partial_effects(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)
    (host.systemctl.parent / "manager-fail").write_text("arm-enable-wrong-state", encoding="utf-8")

    status, body = _apply(host, _confirm(plan))

    receipt = json.loads(body)
    assert status == 200, body
    assert (receipt["outcome"], receipt["reason"]) == ("partial", "partial_effects")


# --- refused before the plan's one use ----------------------------------------------


def test_a_caller_without_setup_apply_or_beyond_the_rate_is_refused_and_recorded(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)

    denied = _apply(host, _confirm(plan), token=OPERATOR)
    limited = _apply(host, _confirm(plan), calls=0)

    assert denied == (403, "setup apply refused: capability_denied")
    assert limited == (429, "setup apply refused: rate_limit")
    for receipt in host.chain()[-2:]:
        assert receipt.draft.plan_id == plan["plan_id"]
        assert receipt.draft.plan_digest == plan["plan_digest"]
        assert receipt.draft.configuration_generation is None
        assert receipt.draft.capability == "setup_apply"
    assert _outcomes(host)[-2:] == [("denied", "capability_denied"), ("rate_limited", "rate_limit")]
    assert _apply(host, _confirm(plan))[0] == 200


def test_a_host_that_cannot_be_inspected_is_unavailable(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)
    host.applied = True
    host.unreadable_after = True

    assert _apply(host, _confirm(plan)) == (503, "setup apply refused: apply_unavailable")
    assert _outcomes(host)[-1] == ("refused", "apply_unavailable")


def test_changed_executables_are_drift_and_leave_the_plan_unused(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)

    host.python = "/opt/other/python"  # the host's interpreter moved after the plan was issued
    assert _apply(host, _confirm(plan)) == (409, "setup apply refused: plan_drift")
    assert _outcomes(host)[-1] == ("drifted", "plan_drift")
    assert host.plans.generation_of(plan["plan_id"]) == plan["configuration_generation"]
    host.python = fixture.PYTHON
    assert _apply(host, _confirm(plan))[0] == 200


@pytest.mark.parametrize(
    ("changes", "answer"),
    [
        ({"plan_id": "P" * 32}, "plan_not_found"),
        ({"confirmation_nonce": "N" * 32}, "plan_mismatch"),
    ],
)
def test_an_unknown_plan_or_a_wrong_nonce_is_refused(
    tmp_path: Path, changes: dict[str, object], answer: str
) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)

    assert _apply(host, _confirm(plan, **changes)) == (409, f"setup apply refused: {answer}")
    assert _outcomes(host)[-1] == ("refused", answer)
    assert _enabled(host) == []


def test_an_expired_plan_is_refused_expired(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)

    late = _apply(host, _confirm(plan), clock=lambda: NOW + 181)

    assert late == (409, "setup apply refused: plan_expired")
    assert _outcomes(host)[-1] == ("expired", "plan_expired")


def test_a_malformed_apply_is_refused_unrecorded(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)

    refused = _apply(host, _confirm(plan, confirm=False))

    assert refused == (400, "setup apply request refused: confirmation_required")
    assert _outcomes(host) == [("planned", "none")]


# --- refused once the plan's one use is taken ----------------------------------------


def test_a_changed_plan_on_the_same_executables_is_drift(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)
    host.statuses = {"hub": "fail"}

    assert _apply(host, _confirm(plan)) == (409, "setup apply refused: plan_drift")
    drifted = host.chain()[-1].draft
    assert (drifted.outcome, drifted.configuration_generation) == (
        "drifted",
        plan["configuration_generation"],
    )
    assert _apply(host, _confirm(plan)) == (409, "setup apply refused: plan_replayed")


def test_a_plan_with_a_blocked_effect_is_refused_plan_blocked(tmp_path: Path) -> None:
    host = Host(tmp_path, {"package": "fail", "hub": "fail"})
    plan = _plan(host)

    assert _apply(host, _confirm(plan)) == (409, "setup apply refused: plan_blocked")
    assert _outcomes(host)[-1] == ("refused", "plan_blocked")
    assert _commands(host) == []


def test_a_plan_with_no_effect_is_already_satisfied_and_runs_no_executor(tmp_path: Path) -> None:
    host = Host(tmp_path, {})
    plan = _plan(host)

    status, body = _apply(host, _confirm(plan))

    assert status == 200
    assert json.loads(body)["outcome"] == "already_satisfied"
    assert host.applied is False
    assert _commands(host) == []


def test_a_chain_that_cannot_be_written_refuses_before_any_effect(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)
    database = host.receipts / "setup-receipts.db"
    database.chmod(0o400)
    try:
        refused = _apply(host, _confirm(plan))
        limited = _apply(host, _confirm(plan), calls=0)
    finally:
        database.chmod(0o600)

    assert refused == (503, "setup receipt chain unavailable")
    assert limited == (503, "setup receipt chain unavailable")
    assert _enabled(host) == []
    assert host.applied is False


def test_an_answer_whose_receipt_cannot_be_written_is_unavailable(tmp_path: Path) -> None:
    host = Host(tmp_path, {})
    plan = _plan(host)
    database = host.receipts / "setup-receipts.db"
    database.chmod(0o400)
    try:
        answer = _apply(host, _confirm(plan))
    finally:
        database.chmod(0o600)

    assert answer == (503, "setup receipt chain unavailable")


# --- the executor's own refusals ------------------------------------------------------


def test_a_restart_of_the_dashboards_own_process_is_refused_before_any_effect(
    tmp_path: Path,
) -> None:
    host = Host(tmp_path, {"hub": "fail"}, hub_pid=os.getpid())
    plan = _plan(host)
    assert plan["effects"][0]["process_id"] == os.getpid()

    assert _apply(host, _confirm(plan)) == (409, "setup apply refused: unsafe_posture")
    assert _outcomes(host)[-2:] == [("authorised", "none"), ("refused", "unsafe_posture")]
    assert _enabled(host) == []


def test_a_hub_whose_process_changed_after_the_plan_is_drift(tmp_path: Path) -> None:
    with _process() as hub:
        host = Host(tmp_path, {"hub": "fail"}, hub_pid=hub.pid)
        plan = _plan(host)
        host.executor_hub_pid = hub.pid + 1

        assert _apply(host, _confirm(plan)) == (409, "setup apply refused: plan_drift")
    assert _outcomes(host)[-1] == ("drifted", "plan_drift")
    assert _enabled(host) == []


def test_a_running_hub_is_restarted_and_its_after_read_afterwards(tmp_path: Path) -> None:
    with _process() as hub:
        host = Host(tmp_path, {"hub": "fail"}, hub_pid=hub.pid)
        state = {
            "units": {"synapse-hub.service": {"active": True, "enabled": True, "pid": hub.pid}}
        }
        (host.systemctl.parent / "manager-state.json").write_text(json.dumps(state), "utf-8")
        plan = _plan(host)

        status, body = _apply(host, _confirm(plan))

    receipt = json.loads(body)
    assert status == 200, body
    assert (receipt["outcome"], receipt["effects"][0]["after"]) == ("applied", "satisfied")
    commands = _commands(host)
    assert ["--user", "restart", "--", "synapse-hub.service"] in commands
    restarted = _manager_state(host.systemctl)["units"]
    assert isinstance(restarted, dict)
    assert restarted["synapse-hub.service"]["pid"] != hub.pid


def test_a_ledger_that_cannot_be_opened_is_unavailable(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)
    environment = {"HOME": str(host.home), "XDG_STATE_HOME": "relative/state"}

    answer = _apply(host, _confirm(plan), environment=environment)

    assert answer == (503, "setup apply refused: apply_unavailable")
    assert _enabled(host) == []


def test_an_authorisation_that_cannot_be_built_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)

    def refuse(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise SetupAuthorizationError("invalid_expiry")

    monkeypatch.setattr(apply_module, "build_setup_authorization", refuse)

    assert _apply(host, _confirm(plan)) == (503, "setup apply refused: apply_unavailable")
    assert _outcomes(host)[-1] == ("refused", "apply_unavailable")


def test_the_authorisation_lives_the_plans_remaining_time_within_its_bounds() -> None:
    plan = SetupPlan("r", "local-single-user", 1, "g" * 64, "c" * 64, 1_000, ())

    assert apply_module._lifetime(plan, 990) == 30
    assert apply_module._lifetime(plan, 800) == 200
    assert apply_module._lifetime(plan, -10_000) == 900
    assert apply_module._cli_nonce("n" * 32) != "n" * 32
    assert len(apply_module._cli_nonce("n" * 32)) == 64


# --- a real start: only paths that never reach the executor -------------------------


def _apply_over_http(
    server: DashboardServer, token: str, body: dict[str, object]
) -> tuple[int, str]:
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    origin = server.url("").rstrip("/")
    headers = {
        "Connection": "close",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "Origin": origin,
    }
    request = Request(
        server.url(SETUP_APPLY_PATH), data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8")


def test_a_real_start_serves_the_apply_route_and_never_runs_a_blocked_plan(
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
        from test_dashboard_setup_plan_http import _post

        status, text = _post(server, TOKENS["admin"], origin=origin)
        assert status == 200, text
        plan = json.loads(text)
        assert any(effect["disposition"] == "blocked" for effect in plan["effects"])
        operator = _apply_over_http(server, TOKENS["operator"], _confirm(plan))
        admin = _apply_over_http(server, TOKENS["admin"], _confirm(plan))
    finally:
        server.close()

    assert operator == (403, "setup apply refused: capability_denied")
    assert admin == (409, "setup apply refused: plan_blocked")
    with SetupReceiptStore(receipts) as chain:
        outcomes = [receipt.draft.outcome for receipt in chain.read_all()]
    assert outcomes == ["planned", "denied", "refused"]


@pytest.mark.parametrize("hub_token", ["hub-secret", None])
def test_the_setup_writes_hand_the_apply_the_hubs_secret_in_its_environment(
    monkeypatch: pytest.MonkeyPatch, hub_token: str | None
) -> None:
    seen: list[SetupApplyBindings] = []

    def capture(request: SetupWriteRequest, bindings: SetupApplyBindings) -> AccessHttpDecision:
        seen.append(bindings)
        return AccessHttpDecision(HTTPStatus.NOT_FOUND, b"", headers=())

    monkeypatch.setattr(writes_module, "setup_apply_response", capture)
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)
    context = SetupWriteContext(
        _policy(),
        SetupServing(SetupPostureDecision(False, "unarmed")),
        fixture.inspection_of_profile,
        WriteRateLimiter(max_calls=1, window_seconds=60.0),
        hub_token,
    )

    setup_write_response(SETUP_APPLY_PATH, Message(), io.BytesIO(b""), context)

    [bindings] = seen
    assert bindings.environment.get("SYNAPSE_TOKEN") == hub_token


def test_an_apply_without_this_dashboards_origin_is_refused_unrecorded(tmp_path: Path) -> None:
    host = Host(tmp_path, fixture.HUB_AND_WAITER_DOWN)
    plan = _plan(host)
    bindings = SetupApplyBindings(
        _policy(), host.serving, host.inspect, _limiter(1), {"HOME": str(host.home)}
    )
    request = SetupWriteRequest(
        f"Bearer {ADMIN}", HOST, (), "application/json", json.dumps(_confirm(plan)).encode()
    )

    decision = setup_apply_response(request, bindings)

    assert decision.status == 403
    assert decision.body == b"setup writes require this dashboard's own Origin"
    assert _outcomes(host) == [("planned", "none")]


def test_each_check_status_reads_as_its_verdict() -> None:
    assert dict(STATUS_VERDICTS) == {
        "pass": "satisfied",
        "warn": "unsatisfied",
        "fail": "unsatisfied",
        "unavailable": "unverifiable",
    }
