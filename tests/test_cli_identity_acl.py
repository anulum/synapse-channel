# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the identity-audit and acl-shadow CLIs

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_acl_shadow, cli_identity


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
