# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the identity-audit and acl-shadow CLIs

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_acl_shadow, cli_identity
from synapse_channel.core.acl import PIN_RECLAIM, load_acl_policy


def _run(argv: list[str]) -> int:
    args = cli.build_parser().parse_args(argv)
    return int(args.func(args))


def _json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- identity audit --------------------------------------------------------


def test_identity_audit_parser_registered() -> None:
    args = cli.build_parser().parse_args(["identity", "audit", "--identities", "x.json"])
    assert args.func is cli_identity._cmd_identity_audit


def test_identity_audit_clean_inventory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ids = _json(tmp_path / "ids.json", [{"agent_id": "a", "project": "P", "credential_id": "k"}])
    code = _run(["identity", "audit", "--identities", str(ids)])
    out = capsys.readouterr().out
    assert code == 0
    assert "P/a" in out
    assert "credential=yes" in out


def test_identity_audit_duplicate_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = _json(
        tmp_path / "ids.json",
        [
            {"agent_id": "a", "project": "P", "credential_id": "k1"},
            {"agent_id": "a", "project": "P", "credential_id": "k2"},
        ],
    )
    code = _run(["identity", "audit", "--identities", str(ids)])
    assert code == 1
    assert "[fail]" in capsys.readouterr().out


def test_identity_audit_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ids = _json(tmp_path / "ids.json", [{"agent_id": "a", "project": "P"}])
    code = _run(["identity", "audit", "--identities", str(ids), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0  # a missing credential is a warn, not a fail
    assert report["identities"][0]["audit_subject"] == "P/a"
    assert any("no credential" in f["message"] for f in report["findings"])


def test_identity_audit_malformed_returns_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "ids.json"
    bad.write_text("{not json", encoding="utf-8")
    code = _run(["identity", "audit", "--identities", str(bad)])
    assert code == 2
    assert "identity error" in capsys.readouterr().out


# --- identity keygen -------------------------------------------------------


def test_identity_keygen_parser_registered() -> None:
    args = cli.build_parser().parse_args(
        ["identity", "keygen", "--sender", "P/a", "--key-id", "k", "--private-out", "x.pem"]
    )
    assert args.func is cli_identity._cmd_identity_keygen


def test_identity_keygen_writes_key_and_prints_trust_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = tmp_path / "id.pem"
    code = _run(
        ["identity", "keygen", "--sender", "P/a", "--key-id", "k", "--private-out", str(key)]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert key.is_file()
    entry = json.loads(out[out.index("{") :])
    assert entry["keys"][0]["key_id"] == "k"
    assert entry["keys"][0]["senders"] == ["P/a"]


def test_identity_keygen_enrols_into_a_trust_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from synapse_channel.core.identity_binding import load_identity_trust_bundle

    key = tmp_path / "id.pem"
    trust = tmp_path / "trust.json"
    code = _run(
        [
            "identity",
            "keygen",
            "--sender",
            "P/a",
            "--key-id",
            "k",
            "--private-out",
            str(key),
            "--trust",
            str(trust),
        ]
    )

    assert code == 0
    assert "enrolled k" in capsys.readouterr().out
    assert "k" in load_identity_trust_bundle(trust).keys


def test_identity_keygen_entry_carries_expiry_when_requested(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = tmp_path / "id.pem"
    code = _run(
        [
            "identity",
            "keygen",
            "--sender",
            "P/a",
            "--key-id",
            "k",
            "--private-out",
            str(key),
            "--expires-at",
            "1900000000",
        ]
    )
    out = capsys.readouterr().out

    assert code == 0
    entry = json.loads(out[out.index("{") :])
    assert entry["keys"][0]["expires_at"] == 1900000000.0


def test_identity_keygen_refuses_an_existing_key_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = tmp_path / "id.pem"
    key.write_text("existing", encoding="utf-8")

    code = _run(
        ["identity", "keygen", "--sender", "P/a", "--key-id", "k", "--private-out", str(key)]
    )

    assert code == 2
    assert "identity keygen error" in capsys.readouterr().out


def test_identity_keygen_duplicate_enrollment_returns_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust = tmp_path / "trust.json"
    _run(
        [
            "identity",
            "keygen",
            "--sender",
            "P/a",
            "--key-id",
            "k",
            "--private-out",
            str(tmp_path / "a.pem"),
            "--trust",
            str(trust),
        ]
    )
    capsys.readouterr()

    code = _run(
        [
            "identity",
            "keygen",
            "--sender",
            "P/b",
            "--key-id",
            "k",
            "--private-out",
            str(tmp_path / "b.pem"),
            "--trust",
            str(trust),
        ]
    )

    assert code == 2
    assert "already enrolled" in capsys.readouterr().out


# --- governed identity-pin reclaim ----------------------------------------


def test_identity_reclaim_parser_requires_explicit_governance_inputs() -> None:
    args = cli.build_parser().parse_args(
        [
            "identity",
            "reclaim",
            "PROJ/stale",
            "--operator",
            "OPS/operator",
            "--expected-key-id",
            "machine-old",
            "--reason",
            "holder wedged",
            "--break-glass",
        ]
    )
    assert args.func is cli_identity._cmd_identity_reclaim
    assert args.pin_name == "PROJ/stale"
    assert args.operator == "OPS/operator"
    assert args.expected_key_id == "machine-old"
    assert args.reason == "holder wedged"
    assert args.break_glass is True


def test_identity_pin_reclaim_acl_permission_loads_from_operator_policy(tmp_path: Path) -> None:
    policy_path = _json(
        tmp_path / "acl.json",
        {
            "rules": [
                {
                    "permission": PIN_RECLAIM,
                    "target_kind": "agent",
                    "target_pattern": "PROJ/stale",
                    "namespace": "OPS",
                    "reason": "designated recovery operator",
                }
            ]
        },
    )
    policy = load_acl_policy(policy_path)
    assert policy.rules[0].permission == PIN_RECLAIM


class _FakeReclaimAgent:
    """Minimal one-shot agent for CLI transport verdict branches."""

    def __init__(
        self,
        callback: Any,
        *,
        outcome: dict[str, Any] | None,
        ready: bool = True,
        closed: bool = False,
    ) -> None:
        self.callback = callback
        self.outcome = outcome
        self.ready = ready
        self.running = not closed
        self.last_close_code = 4009 if closed else None
        self.last_close_reason = "name conflict" if closed else ""

    async def connect(self) -> None:
        while self.running:
            await asyncio.sleep(1.0)

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return self.ready

    async def send_message(self, _msg_type: str, **_fields: Any) -> None:
        if self.outcome is not None:
            await self.callback(self.outcome)


def _reclaim_factory(
    outcome: dict[str, Any] | None, *, ready: bool = True, closed: bool = False
) -> Any:
    def factory(_name: str, callback: Any, **_kwargs: Any) -> _FakeReclaimAgent:
        return _FakeReclaimAgent(callback, outcome=outcome, ready=ready, closed=closed)

    return factory


async def test_identity_reclaim_cli_renders_a_generic_acl_error_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await cli_identity._identity_reclaim(
        uri="ws://hub",
        operator="OPS/operator",
        pin_name="PROJ/stale",
        expected_key_id="machine-old",
        reason="recover",
        break_glass=False,
        token=None,
        ready_timeout=0.1,
        result_timeout=0.1,
        json_output=True,
        agent_factory=_reclaim_factory(
            {"type": "error", "payload": "access denied: identity-pin-reclaim"}
        ),
    )
    assert code == 1
    assert json.loads(capsys.readouterr().out)["detail"].startswith("access denied")


async def test_identity_reclaim_cli_times_out_without_a_hub_verdict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await cli_identity._identity_reclaim(
        uri="ws://hub",
        operator="OPS/operator",
        pin_name="PROJ/stale",
        expected_key_id="machine-old",
        reason="recover",
        break_glass=False,
        token=None,
        ready_timeout=0.1,
        result_timeout=0.0,
        json_output=False,
        agent_factory=_reclaim_factory(None),
    )
    assert code == 2
    assert "no authoritative verdict" in capsys.readouterr().out


@pytest.mark.parametrize(("ready", "closed"), [(False, False), (True, True)])
async def test_identity_reclaim_cli_reports_a_connection_failure(
    ready: bool, closed: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await cli_identity._identity_reclaim(
        uri="ws://hub",
        operator="OPS/operator",
        pin_name="PROJ/stale",
        expected_key_id="machine-old",
        reason="recover",
        break_glass=False,
        token=None,
        ready_timeout=0.1,
        result_timeout=0.1,
        json_output=False,
        agent_factory=_reclaim_factory(None, ready=ready, closed=closed),
    )
    assert code == 2
    assert "hub" in capsys.readouterr().out.lower()


async def test_identity_reclaim_cli_renders_an_applied_result_without_an_audit_seq(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await cli_identity._identity_reclaim(
        uri="ws://hub",
        operator="OPS/operator",
        pin_name="PROJ/stale",
        expected_key_id="machine-old",
        reason="recover",
        break_glass=False,
        token=None,
        ready_timeout=0.1,
        result_timeout=0.1,
        json_output=False,
        agent_factory=_reclaim_factory(
            {
                "type": "identity_pin_reclaim_result",
                "applied": True,
                "pin_name": "PROJ/stale",
                "payload": "done",
            }
        ),
    )
    assert code == 0
    assert capsys.readouterr().out.strip() == "reclaimed identity pin for PROJ/stale"


def test_identity_reclaim_dispatches_the_async_command(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_reclaim(**_kwargs: Any) -> int:
        return 7

    monkeypatch.setattr(cli_identity, "_identity_reclaim", fake_reclaim)
    args = argparse.Namespace(
        uri="ws://hub",
        operator="OPS/operator",
        pin_name="PROJ/stale",
        expected_key_id="machine-old",
        reason="recover",
        break_glass=False,
        token=None,
        ready_timeout=1.0,
        timeout=1.0,
        json=False,
    )
    assert cli_identity._cmd_identity_reclaim(args) == 7


# --- acl shadow ------------------------------------------------------------


def _policy(tmp_path: Path) -> Path:
    return _json(
        tmp_path / "acl.json",
        {
            "rules": [
                {
                    "permission": "claim",
                    "target_kind": "path",
                    "target_pattern": "src/*",
                    "namespace": "P",
                    "reason": "core may claim src",
                }
            ]
        },
    )


def test_acl_shadow_parser_registered() -> None:
    args = cli.build_parser().parse_args(
        ["acl", "shadow", "--policy", "p.json", "--requests", "r.json"]
    )
    assert args.func is cli_acl_shadow._cmd_acl_shadow


def test_acl_shadow_reports_allow_and_deny(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path)
    requests = _json(
        tmp_path / "req.json",
        [
            {
                "subject": "P/a",
                "permission": "claim",
                "target_kind": "path",
                "target_value": "src/x.py",
            },
            {
                "subject": "P/a",
                "permission": "metrics",
                "target_kind": "metrics",
                "target_value": "live",
            },
        ],
    )
    code = _run(
        ["acl", "shadow", "--policy", str(policy), "--requests", str(requests), "--project", "P"]
    )
    out = capsys.readouterr().out
    assert code == 0  # shadow never blocks
    assert "1 would-allow, 1 would-deny" in out


def test_acl_shadow_json_uses_default_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path)
    requests = _json(
        tmp_path / "req.json",
        [
            {
                "subject": "P/a",
                "permission": "claim",
                "target_kind": "path",
                "target_value": "src/x.py",
            }
        ],
    )
    code = _run(
        [
            "acl",
            "shadow",
            "--policy",
            str(policy),
            "--requests",
            str(requests),
            "--project",
            "P",
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["mode"] == "shadow"
    assert report["would_allow"] == 1
    assert report["decisions"][0]["matched_rule"] == 0


def test_acl_shadow_bad_policy_returns_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "acl.json"
    bad.write_text("{}", encoding="utf-8")
    requests = _json(tmp_path / "req.json", [])
    code = _run(["acl", "shadow", "--policy", str(bad), "--requests", str(requests)])
    assert code == 2
    assert "acl shadow error" in capsys.readouterr().out


def test_acl_shadow_bad_requests_returns_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path)
    code = _run(
        ["acl", "shadow", "--policy", str(policy), "--requests", str(tmp_path / "absent.json")]
    )
    assert code == 2
    assert "requests file does not exist" in capsys.readouterr().out


def test_acl_shadow_rejects_non_list_requests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    policy = _policy(tmp_path)
    requests = tmp_path / "req.json"
    requests.write_text('{"not": "a list"}', encoding="utf-8")
    code = _run(["acl", "shadow", "--policy", str(policy), "--requests", str(requests)])
    assert code == 2
    assert "JSON list of objects" in capsys.readouterr().out


def test_load_requests_rejects_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "req.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(Exception, match="invalid requests JSON"):
        cli_acl_shadow._load_requests(bad)


def test_cmd_handlers_via_namespace(tmp_path: Path) -> None:
    # Exercise the handlers through a bare Namespace as main() would build one.
    ids = _json(tmp_path / "ids.json", [{"agent_id": "a", "project": "P", "credential_id": "k"}])
    ns = argparse.Namespace(identities=str(ids), json=False)
    assert cli_identity._cmd_identity_audit(ns) == 0
