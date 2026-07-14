# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the shadow-mode ACL evaluation CLI

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli_acl_shadow
from synapse_channel.core.acl import AclDecision, AclError, Target

_POLICY: dict[str, Any] = {
    "rules": [
        {
            "permission": "message",
            "target_kind": "channel",
            "target_pattern": "general",
            "namespace": "proj",
            "reason": "operators may post to general",
        }
    ]
}

# One request that matches the rule (would_allow) and one that does not (would_deny).
# The allow request carries its own project; the deny request omits it, so the
# command must fall back to ``--project``.
_REQUESTS: list[dict[str, Any]] = [
    {
        "subject": "alice",
        "project": "proj",
        "permission": "message",
        "target_kind": "channel",
        "target_value": "general",
    },
    {
        "subject": "bob",
        "permission": "message",
        "target_kind": "channel",
        "target_value": "secret",
    },
]


def _write(path: Path, payload: object) -> Path:
    """Serialise ``payload`` as JSON to ``path`` and return it."""
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ns(
    policy: Path, requests: Path, *, project: str = "proj", json_out: bool = False
) -> argparse.Namespace:
    """Build the argparse namespace the command handler consumes."""
    return argparse.Namespace(
        policy=str(policy), requests=str(requests), project=project, json=json_out
    )


class TestLoadRequests:
    """Cover every branch of :func:`_load_requests`."""

    def test_valid_list_of_objects_is_returned(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "r.json", [{"subject": "a"}, {"subject": "b"}])
        assert cli_acl_shadow._load_requests(path) == [{"subject": "a"}, {"subject": "b"}]

    def test_missing_file_raises_acl_error(self, tmp_path: Path) -> None:
        with pytest.raises(AclError, match="requests file does not exist"):
            cli_acl_shadow._load_requests(tmp_path / "absent.json")

    def test_invalid_json_raises_acl_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(AclError, match="invalid requests JSON"):
            cli_acl_shadow._load_requests(path)

    def test_non_list_payload_raises_acl_error(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "obj.json", {"subject": "a"})
        with pytest.raises(AclError, match="must be a JSON list of objects"):
            cli_acl_shadow._load_requests(path)

    def test_list_with_non_object_item_raises_acl_error(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "mixed.json", [{"subject": "a"}, "oops"])
        with pytest.raises(AclError, match="must be a JSON list of objects"):
            cli_acl_shadow._load_requests(path)


class TestCommandTextMode:
    """Cover the human-readable output path of :func:`_cmd_acl_shadow`."""

    def test_reports_allow_and_deny_with_glyphs(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        policy = _write(tmp_path / "policy.json", _POLICY)
        requests = _write(tmp_path / "requests.json", _REQUESTS)
        code = cli_acl_shadow._cmd_acl_shadow(_ns(policy, requests))
        out = capsys.readouterr().out
        assert code == 0
        assert "acl shadow [proj]: 1 would-allow, 1 would-deny" in out
        # The allow request keeps its own project; the deny request inherits --project.
        assert "+ alice message channel:general ->" in out
        assert "- bob message channel:secret ->" in out

    def test_unknown_decision_uses_fallback_glyph(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        policy = _write(tmp_path / "policy.json", _POLICY)
        requests = _write(tmp_path / "requests.json", [_REQUESTS[0]])

        def _fake_evaluate(**_: Any) -> AclDecision:
            return AclDecision(
                "would_maybe", "alice", "message", Target("channel", "general"), "n/a"
            )

        monkeypatch.setattr(cli_acl_shadow, "evaluate_access", _fake_evaluate)
        code = cli_acl_shadow._cmd_acl_shadow(_ns(policy, requests))
        out = capsys.readouterr().out
        assert code == 0
        # Out-of-vocabulary decision falls back to the "?" glyph.
        assert "? alice message channel:general ->" in out


class TestCommandJsonMode:
    """Cover the JSON output path of :func:`_cmd_acl_shadow`."""

    def test_emits_structured_report(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        policy = _write(tmp_path / "policy.json", _POLICY)
        requests = _write(tmp_path / "requests.json", _REQUESTS)
        code = cli_acl_shadow._cmd_acl_shadow(_ns(policy, requests, json_out=True))
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["mode"] == "shadow"
        assert payload["would_allow"] == 1
        assert payload["would_deny"] == 1
        assert len(payload["decisions"]) == 2
        assert payload["decisions"][0]["decision"] == "would_allow"
        assert payload["decisions"][1]["decision"] == "would_deny"


class TestCommandErrors:
    """Both input-loading failures short-circuit to exit code 2."""

    def test_missing_policy_returns_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        requests = _write(tmp_path / "requests.json", _REQUESTS)
        code = cli_acl_shadow._cmd_acl_shadow(_ns(tmp_path / "absent.json", requests))
        assert code == 2
        assert "acl shadow error:" in capsys.readouterr().out

    def test_missing_requests_returns_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        policy = _write(tmp_path / "policy.json", _POLICY)
        code = cli_acl_shadow._cmd_acl_shadow(_ns(policy, tmp_path / "absent.json"))
        assert code == 2
        assert "acl shadow error:" in capsys.readouterr().out


class TestAddParsers:
    """Cover parser registration in :func:`add_parsers`."""

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="synapse")
        subparsers = parser.add_subparsers()
        cli_acl_shadow.add_parsers(subparsers)
        return parser

    def test_shadow_defaults_and_func_binding(self) -> None:
        args = self._parser().parse_args(
            ["acl", "shadow", "--policy", "p.json", "--requests", "r.json"]
        )
        assert args.func is cli_acl_shadow._cmd_acl_shadow
        assert args.acl_command == "shadow"
        assert args.policy == "p.json"
        assert args.requests == "r.json"
        assert args.project == ""
        assert args.json is False

    def test_json_flag_and_project_override(self) -> None:
        args = self._parser().parse_args(
            ["acl", "shadow", "--policy", "p", "--requests", "r", "--project", "x", "--json"]
        )
        assert args.project == "x"
        assert args.json is True

    def test_acl_requires_a_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            self._parser().parse_args(["acl"])

    def test_shadow_requires_policy_and_requests(self) -> None:
        with pytest.raises(SystemExit):
            self._parser().parse_args(["acl", "shadow"])
