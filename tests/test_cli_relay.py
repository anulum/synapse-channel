# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li — `synapse federation relay` CLI

from __future__ import annotations

import getpass
import json
from typing import Any

import pytest

from synapse_channel import cli, cli_relay
from synapse_channel.core.operator_relay_transport import RelayTransportError
from synapse_channel.core.operator_relay_wire import RelayActionRequest, RelayActionResult


def _args(*extra: str) -> Any:
    argv = [
        "federation",
        "relay",
        "release",
        "--peer",
        "ws://peer:1/",
        "--namespace",
        "SYNAPSE-CHANNEL",
        "--task",
        "t1",
        *extra,
    ]
    return cli.build_parser().parse_args(argv)


def _relayer(
    result: RelayActionResult | None = None,
    *,
    error: Exception | None = None,
    captured: dict[str, Any] | None = None,
) -> Any:
    """Return an injectable relayer that records its request or raises ``error``."""

    async def _relay(request: RelayActionRequest, **kwargs: Any) -> RelayActionResult:
        if captured is not None:
            captured["request"] = request
            captured["kwargs"] = kwargs
        if error is not None:
            raise error
        assert result is not None
        return result

    return _relay


def _applied(detail: str = "released by operator ops-admin (was held by x)") -> RelayActionResult:
    return RelayActionResult(
        applied=True,
        action="release",
        namespace="SYNAPSE-CHANNEL",
        task_id="t1",
        owner_hub_id="syn-peer",
        detail=detail,
    )


def test_relay_applied_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_relay._cmd_relay(_args("--operator", "ops-admin"), relayer=_relayer(_applied()))
    assert rc == 0
    out = capsys.readouterr().out
    assert "syn-peer applied relay 'release'" in out


def test_relay_makes_peer_result_controls_visible(capsys: pytest.CaptureFixture[str]) -> None:
    hostile = "remote\x1b]52;c;YQ==\x07\nforged\u202e"
    result = RelayActionResult(
        applied=True,
        action=hostile,
        namespace="SYNAPSE-CHANNEL",
        task_id="t1",
        owner_hub_id=hostile,
        detail=hostile,
    )

    assert cli_relay._cmd_relay(_args(), relayer=_relayer(result)) == 0

    rendered = capsys.readouterr().out
    assert "remote\\x1b]52;c;YQ==\\x07\\nforged\\u202e" in rendered
    assert "\x1b" not in rendered
    assert "\x07" not in rendered
    assert "\u202e" not in rendered


def test_relay_refused_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    refused = RelayActionResult(
        applied=False,
        action="release",
        namespace="SYNAPSE-CHANNEL",
        task_id="t1",
        owner_hub_id="syn-peer",
        detail="scope_not_granted",
    )
    rc = cli_relay._cmd_relay(_args(), relayer=_relayer(refused))
    assert rc == 1
    assert "refused relay 'release': scope_not_granted" in capsys.readouterr().out


def test_relay_transport_error_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_relay._cmd_relay(
        _args(), relayer=_relayer(error=RelayTransportError("peer unreachable"))
    )
    assert rc == 2
    assert "could not relay the action: peer unreachable" in capsys.readouterr().err


def _pending(detail: str = "recorded; awaiting approval by a second operator") -> RelayActionResult:
    return RelayActionResult(
        applied=False,
        action="release",
        namespace="SYNAPSE-CHANNEL",
        task_id="t1",
        owner_hub_id="syn-peer",
        detail=detail,
        pending=True,
    )


def test_relay_pending_exits_three(capsys: pytest.CaptureFixture[str]) -> None:
    # A two-person hold is neither applied (0) nor refused (1) nor a transport failure (2).
    rc = cli_relay._cmd_relay(_args("--operator", "alice"), relayer=_relayer(_pending()))
    assert rc == 3
    out = capsys.readouterr().out
    assert "recorded pending a second operator for relay 'release'" in out


def test_relay_pending_json_carries_the_pending_flag(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_relay._cmd_relay(_args("--json"), relayer=_relayer(_pending()))
    assert rc == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert payload["pending"] is True


def test_relay_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_relay._cmd_relay(_args("--json"), relayer=_relayer(_applied()))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is True
    assert payload["pending"] is False
    assert payload["owner_hub_id"] == "syn-peer"


def test_relay_forwards_the_request_fields_and_local_id() -> None:
    captured: dict[str, Any] = {}
    rc = cli_relay._cmd_relay(
        _args("--operator", "ceo", "--local-id", "syn-origin", "--peer-token", "tok"),
        relayer=_relayer(_applied(), captured=captured),
    )
    assert rc == 0
    request: RelayActionRequest = captured["request"]
    assert request.action == "release"
    assert request.namespace == "SYNAPSE-CHANNEL"
    assert request.task_id == "t1"
    assert request.operator == "ceo"
    assert request.origin_hub_id == "syn-origin"
    assert captured["kwargs"]["uri"] == "ws://peer:1/"
    assert captured["kwargs"]["local_id"] == "syn-origin"
    assert captured["kwargs"]["token"] == "tok"


def test_relay_forwards_the_reason_and_break_glass_tag() -> None:
    captured: dict[str, Any] = {}
    rc = cli_relay._cmd_relay(
        _args("--reason", "freeing a wedged release", "--break-glass"),
        relayer=_relayer(_applied(), captured=captured),
    )
    assert rc == 0
    request: RelayActionRequest = captured["request"]
    assert request.reason == "freeing a wedged release"
    assert request.break_glass is True


def test_relay_defaults_the_reason_empty_and_break_glass_off() -> None:
    captured: dict[str, Any] = {}
    cli_relay._cmd_relay(_args(), relayer=_relayer(_applied(), captured=captured))
    assert captured["request"].reason == ""
    assert captured["request"].break_glass is False


def test_relay_defaults_the_operator_to_the_os_user(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the getpass module the CLI resolves the default operator through.
    monkeypatch.setattr(getpass, "getuser", lambda: "logged-in-user")
    captured: dict[str, Any] = {}
    cli_relay._cmd_relay(_args(), relayer=_relayer(_applied(), captured=captured))
    assert captured["request"].operator == "logged-in-user"


def test_relay_action_choice_is_validated_by_the_parser() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            [
                "federation",
                "relay",
                "reboot-the-peer",
                "--peer",
                "ws://x",
                "--namespace",
                "N",
                "--task",
                "t",
            ]
        )
