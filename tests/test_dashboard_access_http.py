# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure dashboard access HTTP decision tests
"""Prove descriptor redaction and server-side per-route capability checks."""

from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import synapse_channel.dashboard as dashboard_module
from synapse_channel.dashboard import DashboardServer, start_dashboard_server
from synapse_channel.dashboard_access import (
    DashboardAccessPolicy,
    DashboardCredential,
    DashboardPrincipal,
    capabilities_for_role,
)
from synapse_channel.dashboard_access_http import (
    MESSAGE_PATH,
    TASK_PATH,
    TASK_UPDATE_PATH,
    TRUST_BOUNDARY,
    access_descriptor_decision,
    read_decision,
    write_decision,
)
from synapse_channel.dashboard_feed_serving import FeedResponse

_TOKENS = {
    "viewer": "viewer-token-that-is-at-least-32-bytes",
    "operator": "operator-token-that-is-at-least-32-bytes",
    "admin": "admin-token-that-is-at-least-32-bytes",
}


def _access_file(tmp_path: Path, *roles: str) -> Path:
    principals: list[dict[str, str]] = []
    for role in roles:
        token_path = tmp_path / f"{role}.token"
        token_path.write_text(_TOKENS[role] + "\n", encoding="utf-8")
        token_path.chmod(0o600)
        principal = {"id": role, "role": role, "token_file": token_path.name}
        if role != "viewer":
            principal["operator_name"] = f"operator:studio/{role}"
        principals.append(principal)
    policy_path = tmp_path / "dashboard-access.json"
    policy_path.write_text(
        json.dumps({"version": 1, "principals": principals}),
        encoding="utf-8",
    )
    policy_path.chmod(0o600)
    return policy_path


def _server(access_file: Path, *, operator: bool) -> DashboardServer:
    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://hub.invalid",
        name="DASH",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        dashboard_access_file=access_file,
        operator=operator,
    )


def _request(
    server: DashboardServer,
    path: str,
    *,
    token: str | None = None,
    document: dict[str, object] | None = None,
) -> tuple[int, dict[str, str], str]:
    headers = {"Connection": "close"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data: bytes | None = None
    method = "GET"
    if document is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(document).encode("utf-8")
        method = "POST"
    request = Request(server.url(path), data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=3) as response:  # nosec B310
            return response.status, dict(response.headers.items()), response.read().decode()
    except HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read().decode()


def _policy(*, armed: bool = True, open_reads: bool = False) -> DashboardAccessPolicy:
    viewer = DashboardPrincipal(
        "review", "viewer", capabilities_for_role("viewer", operator_armed=armed)
    )
    operator = DashboardPrincipal(
        "ops",
        "operator",
        capabilities_for_role("operator", operator_armed=armed),
        "operator:studio/ops",
    )
    admin = DashboardPrincipal(
        "owner",
        "admin",
        capabilities_for_role("admin", operator_armed=armed),
        "operator:studio/owner",
    )
    credentials = (
        DashboardCredential(viewer, b"v" * 32),
        DashboardCredential(operator, b"o" * 32),
        DashboardCredential(admin, b"a" * 32),
    )
    return DashboardAccessPolicy(credentials, viewer if open_reads else None, armed)


@pytest.mark.parametrize(
    ("token", "role", "principal", "writes"),
    [
        ("v", "viewer", "review", False),
        ("o", "operator", "ops", True),
        ("a", "admin", "owner", True),
    ],
)
def test_descriptor_is_token_free_and_role_capability_exact(
    token: str,
    role: str,
    principal: str,
    writes: bool,
) -> None:
    decision = access_descriptor_decision(_policy(), f"Bearer {token * 32}")
    assert decision.status is HTTPStatus.OK
    assert decision.headers == (("Vary", "Authorization"),)
    payload = json.loads(decision.body)
    assert payload == {
        "version": 1,
        "principal": principal,
        "role": role,
        "capabilities": {
            "read": True,
            "message_send": writes,
            "task_declare": writes,
            "task_update": writes,
        },
        "operator_armed": True,
        "trust_boundary": TRUST_BOUNDARY,
    }
    assert token * 32 not in decision.body.decode()


def test_read_and_descriptor_require_a_known_principal_when_gated() -> None:
    for authorization in (None, "Bearer wrong"):
        read = read_decision(_policy(), authorization)
        assert read.status is HTTPStatus.UNAUTHORIZED
        assert read.authenticate is True
        assert read.allowed is False
        descriptor = access_descriptor_decision(_policy(), authorization)
        assert descriptor.status is HTTPStatus.UNAUTHORIZED
        assert descriptor.headers == (("Vary", "Authorization"),)


def test_open_read_policy_returns_its_viewer() -> None:
    decision = read_decision(_policy(open_reads=True), None)
    assert decision.allowed is True
    assert decision.principal is not None
    assert decision.principal.role == "viewer"


@pytest.mark.parametrize("route", [MESSAGE_PATH, TASK_PATH, TASK_UPDATE_PATH])
@pytest.mark.parametrize("token", ["o", "a"])
def test_operator_and_admin_are_allowed_only_on_shipped_routes(route: str, token: str) -> None:
    decision = write_decision(_policy(), f"Bearer {token * 32}", route)
    assert decision.allowed is True
    assert decision.principal is not None
    assert decision.principal.operator_name in {
        "operator:studio/ops",
        "operator:studio/owner",
    }


@pytest.mark.parametrize("route", [MESSAGE_PATH, TASK_PATH, TASK_UPDATE_PATH])
def test_viewer_is_forbidden_on_every_write_route(route: str) -> None:
    decision = write_decision(_policy(), f"Bearer {'v' * 32}", route)
    assert decision.status is HTTPStatus.FORBIDDEN
    assert decision.body == b"dashboard capability denied\n"
    assert decision.authenticate is False


def test_write_denial_order_preserves_non_disclosure_and_auth_challenge() -> None:
    unarmed = write_decision(_policy(armed=False), None, MESSAGE_PATH)
    assert unarmed.status is HTTPStatus.NOT_FOUND
    assert write_decision(_policy(), None, "/unknown").status is HTTPStatus.UNAUTHORIZED
    assert write_decision(_policy(), f"Bearer {'o' * 32}", "/unknown").status is (
        HTTPStatus.NOT_FOUND
    )


def test_write_refuses_a_capability_without_relay_attribution() -> None:
    principal = DashboardPrincipal(
        "broken", "operator", capabilities_for_role("operator", operator_armed=True)
    )
    policy = DashboardAccessPolicy((DashboardCredential(principal, b"b" * 32),), None, True)
    assert write_decision(policy, f"Bearer {'b' * 32}", MESSAGE_PATH).status is (
        HTTPStatus.FORBIDDEN
    )


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_real_server_descriptor_is_private_exact_and_token_free(
    tmp_path: Path,
    role: str,
) -> None:
    access_file = _access_file(tmp_path, "viewer", "operator", "admin")
    server = _server(access_file, operator=True)
    try:
        status, headers, body = _request(
            server,
            "/dashboard-access.json",
            token=_TOKENS[role],
        )
    finally:
        server.close()

    payload = json.loads(body)
    writes = role != "viewer"
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert headers["Vary"] == "Authorization"
    assert payload["principal"] == role
    assert payload["role"] == role
    assert payload["capabilities"] == {
        "read": True,
        "message_send": writes,
        "task_declare": writes,
        "task_update": writes,
    }
    assert all(token not in body for token in _TOKENS.values())
    assert str(access_file) not in body


def test_real_server_challenges_unknown_descriptor_principals(tmp_path: Path) -> None:
    server = _server(_access_file(tmp_path, "viewer"), operator=False)
    try:
        missing = _request(server, "/dashboard-access.json")
        wrong = _request(server, "/dashboard-access.json", token="wrong")
    finally:
        server.close()

    for status, headers, body in (missing, wrong):
        assert status == 401
        assert headers["WWW-Authenticate"] == 'Bearer realm="synapse-dashboard"'
        assert headers["Vary"] == "Authorization"
        assert body == "dashboard authorization required\n"


def test_real_server_rechecks_write_capability_and_relay_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names: list[str] = []

    def fake_execute_relay(_plan: object, **kwargs: Any) -> FeedResponse:
        names.append(str(kwargs["operator_name"]))
        return FeedResponse(HTTPStatus.OK, b'{"ok":true}', "application/json")

    monkeypatch.setattr(dashboard_module, "execute_relay", fake_execute_relay)
    server = _server(_access_file(tmp_path, "viewer", "operator", "admin"), operator=True)
    try:
        missing = _request(server, MESSAGE_PATH, document={"to": "x", "text": "hi"})
        wrong = _request(
            server,
            MESSAGE_PATH,
            token="wrong",
            document={"to": "x", "text": "hi"},
        )
        viewer = _request(
            server,
            MESSAGE_PATH,
            token=_TOKENS["viewer"],
            document={"to": "x", "text": "hi"},
        )
        operator = _request(
            server,
            MESSAGE_PATH,
            token=_TOKENS["operator"],
            document={"to": "x", "text": "hi"},
        )
        admin = _request(
            server,
            TASK_UPDATE_PATH,
            token=_TOKENS["admin"],
            document={"id": "T-1", "status": "done"},
        )
    finally:
        server.close()

    assert [missing[0], wrong[0], viewer[0], operator[0], admin[0]] == [401, 401, 403, 200, 200]
    assert names == ["operator:studio/operator", "operator:studio/admin"]


def test_real_server_keeps_unarmed_writes_undisclosed(tmp_path: Path) -> None:
    server = _server(_access_file(tmp_path, "viewer"), operator=False)
    try:
        status, _, body = _request(
            server,
            MESSAGE_PATH,
            token=_TOKENS["viewer"],
            document={"to": "x", "text": "hi"},
        )
    finally:
        server.close()

    assert status == 404
    assert body == "not found\n"


@pytest.mark.parametrize(
    "conflict",
    [{"dashboard_token": "legacy"}, {"operator_name": "operator:legacy"}],
)
def test_access_file_refuses_legacy_identity_controls(
    tmp_path: Path,
    conflict: dict[str, str],
) -> None:
    kwargs: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 0,
        "uri": "ws://hub.invalid",
        "name": "DASH",
        "token": None,
        "ready_timeout": 0.01,
        "response_timeout": 0.01,
        "refresh_seconds": 5,
        "allow_non_loopback": False,
        "dashboard_access_file": _access_file(tmp_path, "viewer"),
        **conflict,
    }
    with pytest.raises(ValueError, match="dashboard-access-file"):
        start_dashboard_server(**kwargs)  # type: ignore[arg-type]
