# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — whether a dashboard serves setup, and its read-only preflight, over HTTP
"""Setup is served only when every start condition holds; its preflight shows nothing measured."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import synapse_channel.dashboard_setup_http as setup_http
from synapse_channel import cli, cli_dashboard
from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.dashboard_access import compatibility_access_policy
from synapse_channel.dashboard_setup_contract import SetupPostureDecision, SetupPostureReason
from synapse_channel.dashboard_setup_http import (
    SETUP_PREFLIGHT_PATH,
    SETUP_RECEIPTS_PATH,
    SetupServing,
    hub_inspection,
    open_setup_receipts,
    setup_preflight_response,
    setup_serving,
)
from synapse_channel.dashboard_setup_receipts import SetupReceiptStore
from synapse_channel.setup_inspector import PlatformSnapshot
from synapse_channel.setup_inspector import inspect_setup as real_inspect_setup
from synapse_channel.setup_profiles import SetupProfile, get_setup_profile

TOKENS = {
    "viewer": "viewer-token-that-is-at-least-32-bytes",
    "operator": "operator-token-that-is-at-least-32-bytes",
    "admin": "admin-token-that-is-at-least-32-bytes",
}
MEASURED = ("/home/alice/.venvs/synapse/bin/synapse", "/usr/bin/systemctl", "harbour/alice")
"""What the faked inspection measures; none of it may reach a browser."""


def _access_file(tmp_path: Path) -> Path:
    principals: list[dict[str, str]] = []
    for role, token in TOKENS.items():
        token_path = tmp_path / f"{role}.token"
        token_path.write_text(token + "\n", encoding="utf-8")
        token_path.chmod(0o600)
        principal = {"id": role, "role": role, "token_file": token_path.name}
        if role != "viewer":
            principal["operator_name"] = f"harbour/{role}"
        principals.append(principal)
    path = tmp_path / "dashboard-access.json"
    path.write_text(json.dumps({"version": 1, "principals": principals}), encoding="utf-8")
    path.chmod(0o600)
    return path


def _receipts(tmp_path: Path, mode: int = 0o700) -> Path:
    directory = tmp_path / "setup-receipts"
    directory.mkdir()
    directory.chmod(mode)
    return directory


def _start(**options: Any) -> DashboardServer:
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:8876",
        name="DASH",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        **options,
    )


def _get(server: DashboardServer, path: str, token: str | None = None) -> tuple[int, str]:
    headers = {"Connection": "close"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(server.url(path), headers=headers), timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as error:
        return error.code, error.read().decode("utf-8")


async def _fake_inspect(profile: SetupProfile, **kwargs: Any) -> dict[str, object]:
    """The command line's own inspection, on a faked host, so nothing real is probed."""

    async def diagnose(**_ignored: Any) -> tuple[int, list[str], list[Diagnosis]]:
        return 0, [], [Diagnosis("hub", "pass", f"hub at {kwargs['uri']} answered", "")]

    return await real_inspect_setup(
        profile,
        uri=kwargs["uri"],
        project="harbour",
        agent_id="alice",
        env={"HOME": "/home/alice"},
        executable_probe=lambda name: MEASURED[0] if name == "synapse" else MEASURED[1],
        platform_probe=lambda: PlatformSnapshot("Linux", "7.0.0", "x86_64"),
        service_manager_probe=lambda _executable: True,
        hub_service_pid_probe=lambda _executable: 4242,
        diagnose_runner=diagnose,
    )


@pytest.fixture
def armed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DashboardServer]:
    """A start that serves setup: operator, setup apply, an access file and a safe receipt chain."""
    monkeypatch.setattr(setup_http, "inspect_setup", _fake_inspect)
    server = _start(
        dashboard_access_file=_access_file(tmp_path),
        operator=True,
        setup_apply=True,
        setup_receipts=_receipts(tmp_path),
    )
    try:
        yield server
    finally:
        server.close()


def test_an_armed_start_serves_the_preflight_to_an_admin_only(armed: DashboardServer) -> None:
    status, body = _get(armed, SETUP_PREFLIGHT_PATH, TOKENS["admin"])
    preflight = json.loads(body)

    assert status == 200
    assert preflight["profile"] == "local-single-user"
    assert {row["id"]: row["status"] for row in preflight["requirements"]}["hub"] == "pass"
    for value in (*MEASURED, "4242", "ws://127.0.0.1:8876"):
        assert value not in body
    assert _get(armed, SETUP_PREFLIGHT_PATH, TOKENS["operator"]) == (
        403,
        "setup_plan capability required",
    )
    assert _get(armed, SETUP_PREFLIGHT_PATH, TOKENS["viewer"])[0] == 403
    assert _get(armed, SETUP_PREFLIGHT_PATH)[0] == 401
    capabilities = json.loads(_get(armed, "/capabilities.json", TOKENS["viewer"])[1])
    assert capabilities["setup"] == {"advertised": True, "reason": "ready"}
    descriptor = json.loads(_get(armed, "/dashboard-access.json", TOKENS["admin"])[1])
    assert (
        descriptor["capabilities"]["setup_plan"],
        descriptor["capabilities"]["setup_apply"],
    ) == (
        True,
        True,
    )
    operator = json.loads(_get(armed, "/dashboard-access.json", TOKENS["operator"])[1])
    assert operator["capabilities"]["setup_plan"] is False


def test_a_failing_inspection_is_one_bounded_answer(
    armed: DashboardServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(_profile: SetupProfile, **_kwargs: Any) -> dict[str, object]:
        raise OSError("/home/alice/secret probe failed")

    monkeypatch.setattr(setup_http, "inspect_setup", broken)

    assert _get(armed, SETUP_PREFLIGHT_PATH, TOKENS["admin"]) == (
        503,
        "setup inspection unavailable",
    )


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"operator": True}, "unarmed"),
        (
            {"dashboard_token": TOKENS["admin"], "operator": True, "setup_apply": True},
            "compatibility_access",
        ),
        ({"operator": True, "setup_apply": True}, "receipt_store_unavailable"),
    ],
)
def test_each_reachable_posture_reason_is_a_404_naming_it(
    tmp_path: Path, options: dict[str, Any], reason: str
) -> None:
    if "dashboard_token" not in options:
        options = {**options, "dashboard_access_file": _access_file(tmp_path)}
    server = _start(**options)
    try:
        token = TOKENS["admin"]
        assert _get(server, SETUP_PREFLIGHT_PATH, token) == (404, f"setup not served: {reason}")
        capabilities = json.loads(_get(server, "/capabilities.json", token)[1])
    finally:
        server.close()

    assert capabilities["setup"] == {"advertised": False, "reason": reason}


def test_a_non_loopback_bind_is_never_served_setup(tmp_path: Path) -> None:
    serving = setup_serving(
        host="192.0.2.10",
        operator=True,
        setup_apply=True,
        setup_receipts=_receipts(tmp_path),
        policy=compatibility_access_policy(
            dashboard_token=None,
            token_protects_reads=False,
            operator_armed=False,
            operator_name="x",
        ),
    )

    assert serving.capability() == {"advertised": False, "reason": "non_loopback"}
    assert serving.profile is None


@pytest.mark.parametrize(
    "reason", ["unarmed", "non_loopback", "compatibility_access", "access_file_required"]
)
def test_the_route_names_every_reason_the_posture_gives(reason: SetupPostureReason) -> None:
    serving = SetupServing(SetupPostureDecision(False, reason))
    policy = compatibility_access_policy(
        dashboard_token=None, token_protects_reads=False, operator_armed=False, operator_name="x"
    )

    decision = setup_preflight_response(None, policy, serving, lambda _profile: {})

    assert (decision.status, decision.body) == (404, f"setup not served: {reason}".encode())


def _refused_receipts(tmp_path: Path, kind: str) -> Path:
    if kind == "missing":
        return tmp_path / "absent"
    if kind == "group-readable":
        return _receipts(tmp_path, 0o750)
    target = _receipts(tmp_path)
    link = tmp_path / "linked-receipts"
    link.symlink_to(target, target_is_directory=True)
    return link


@pytest.mark.parametrize("kind", ["missing", "group-readable", "symlink"])
def test_an_unsafe_receipt_directory_refuses_the_start_naming_the_flag_and_no_path(
    tmp_path: Path, kind: str
) -> None:
    directory = _refused_receipts(tmp_path, kind)

    with pytest.raises(
        ValueError, match="--setup-receipts must name an existing owner-only"
    ) as raised:
        _start(
            dashboard_access_file=_access_file(tmp_path),
            operator=True,
            setup_apply=True,
            setup_receipts=directory,
        )

    assert str(tmp_path) not in str(raised.value)


def test_a_safe_receipt_directory_is_proved_usable_and_left_closed(tmp_path: Path) -> None:
    directory = open_setup_receipts(_receipts(tmp_path))

    assert directory == tmp_path / "setup-receipts"
    with SetupReceiptStore(directory) as store:
        assert store.count() == 0


def test_the_start_refuses_setup_options_that_do_not_go_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(ValueError, match="--setup-apply needs --operator"):
        _start(setup_apply=True)
    with pytest.raises(ValueError, match="--setup-receipts needs --setup-apply"):
        _start(operator=True, setup_receipts=tmp_path)
    monkeypatch.setattr(setup_http, "get_setup_profile", lambda _profile: None)
    with pytest.raises(ValueError, match="setup profile local-single-user is not registered"):
        _start(
            dashboard_access_file=_access_file(tmp_path),
            operator=True,
            setup_apply=True,
            setup_receipts=_receipts(tmp_path),
        )
    assert cli.main(["dashboard", "--port", "0", "--setup-apply"]) == 2
    assert "--setup-apply needs --operator" in capsys.readouterr().out


def test_the_hub_inspection_asks_the_dashboards_hub_with_its_secret_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    async def record(profile: SetupProfile, **kwargs: Any) -> dict[str, object]:
        seen.append({"profile": profile.profile_id, **kwargs})
        return {"checks": []}

    monkeypatch.setattr(setup_http, "inspect_setup", record)
    monkeypatch.delenv("SYNAPSE_TOKEN", raising=False)
    profile = get_setup_profile("local-single-user")
    assert profile is not None

    assert hub_inspection("ws://127.0.0.1:9", "hub-secret")(profile) == {"checks": []}
    hub_inspection("ws://127.0.0.1:9", None)(profile)

    assert seen[0]["uri"] == "ws://127.0.0.1:9"
    assert (seen[0]["project"], seen[0]["agent_id"]) == (None, None)
    assert seen[0]["env"]["SYNAPSE_TOKEN"] == "hub-secret"
    assert "SYNAPSE_TOKEN" not in seen[1]["env"]
    assert os.environ.get("SYNAPSE_TOKEN") is None


def test_the_command_line_arms_setup_and_names_the_preflight_and_where_its_answer_is(
    armed: DashboardServer, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "cli").mkdir()
    receipts = _receipts(tmp_path / "cli")
    args = cli.build_parser().parse_args(
        ["dashboard", "--operator", "--setup-apply", "--setup-receipts", str(receipts)]
    )

    assert (args.setup_apply, args.setup_receipts) == (True, receipts)
    assert cli_dashboard._announce(args, armed, None) is True
    printed = capsys.readouterr().out
    assert f"setup preflight (admin): {armed.url(SETUP_PREFLIGHT_PATH)}" in printed
    assert "whether setup is served: /capabilities.json" in printed
    assert f"setup receipts (admin): {armed.url(SETUP_RECEIPTS_PATH)}" in printed
    assert f"setup plan and apply (admin): POST {armed.url('/setup/plan')}, /setup/apply" in printed
    plain = cli.build_parser().parse_args(["dashboard"])
    assert (plain.setup_apply, plain.setup_receipts) == (False, None)
    assert cli_dashboard._announce(plain, armed, None) is True
    assert "setup " not in capsys.readouterr().out
