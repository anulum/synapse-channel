# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `synapse role` grant/revoke/list CLI

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel import cli, cli_role
from synapse_channel.core.role_grants import load_role_grants


def _store(tmp_path: Path) -> str:
    return str(tmp_path / "role-grants.json")


class TestParserRouting:
    def test_grant_routes_to_its_handler(self) -> None:
        args = cli.build_parser().parse_args(
            ["role", "grant", "SYNAPSE-CHANNEL/coordinator", "--to", "SYNAPSE-CHANNEL/me"]
        )

        assert args.func is cli_role._cmd_grant
        assert args.agent == "SYNAPSE-CHANNEL/me"

    def test_revoke_routes_to_its_handler(self) -> None:
        args = cli.build_parser().parse_args(["role", "revoke", "a/coordinator", "--from", "a/me"])

        assert args.func is cli_role._cmd_revoke
        assert args.agent == "a/me"

    def test_list_routes_to_its_handler(self) -> None:
        args = cli.build_parser().parse_args(["role", "list"])

        assert args.func is cli_role._cmd_list
        assert args.role is None

    def test_role_requires_a_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["role"])


class TestGrant:
    def test_grant_creates_the_store_and_reports(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = _store(tmp_path)

        code = cli.main(["role", "grant", "a/coordinator", "--to", "a/me", "--store", store])

        assert code == 0
        assert "granted: a/me may claim a/coordinator" in capsys.readouterr().out
        assert load_role_grants(store).subjects_for("a/coordinator") == ("a/me",)

    def test_second_grant_is_reported_as_already_granted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = _store(tmp_path)
        cli.main(["role", "grant", "a/coordinator", "--to", "a/me", "--store", store])

        code = cli.main(["role", "grant", "a/coordinator", "--to", "a/me", "--store", store])

        assert code == 0
        assert "already granted" in capsys.readouterr().out

    def test_invalid_role_returns_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = cli.main(["role", "grant", "noslash", "--to", "a/me", "--store", _store(tmp_path)])

        assert code == 2
        assert "role grant error" in capsys.readouterr().out


class TestRevoke:
    def test_revoke_removes_a_grant(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = _store(tmp_path)
        cli.main(["role", "grant", "a/coordinator", "--to", "a/me", "--store", store])

        code = cli.main(["role", "revoke", "a/coordinator", "--from", "a/me", "--store", store])

        assert code == 0
        assert "revoked: a/me may no longer claim a/coordinator" in capsys.readouterr().out
        assert load_role_grants(store).roles() == ()

    def test_revoke_absent_grant_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = cli.main(
            ["role", "revoke", "a/coordinator", "--from", "a/nobody", "--store", _store(tmp_path)]
        )

        assert code == 1
        assert "not granted" in capsys.readouterr().out

    def test_revoke_invalid_role_returns_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = cli.main(["role", "revoke", "bad", "--from", "a/me", "--store", _store(tmp_path)])

        assert code == 2
        assert "role revoke error" in capsys.readouterr().out


class TestList:
    def test_list_all_roles(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        store = _store(tmp_path)
        cli.main(["role", "grant", "a/coordinator", "--to", "a/me", "--store", store])
        cli.main(["role", "grant", "a/reviewer", "--to", "a/you", "--store", store])
        capsys.readouterr()

        code = cli.main(["role", "list", "--store", store])

        out = capsys.readouterr().out
        assert code == 0
        assert "a/coordinator" in out
        assert "a/reviewer" in out
        assert "  a/me" in out

    def test_list_one_role(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        store = _store(tmp_path)
        cli.main(["role", "grant", "a/coordinator", "--to", "a/me", "--store", store])
        cli.main(["role", "grant", "a/reviewer", "--to", "a/you", "--store", store])
        capsys.readouterr()

        code = cli.main(["role", "list", "a/coordinator", "--store", store])

        out = capsys.readouterr().out
        assert code == 0
        assert "a/coordinator" in out
        assert "a/reviewer" not in out

    def test_list_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        store = _store(tmp_path)
        cli.main(["role", "grant", "a/coordinator", "--to", "a/me", "--store", store])
        capsys.readouterr()

        code = cli.main(["role", "list", "--json", "--store", store])

        assert code == 0
        assert json.loads(capsys.readouterr().out) == {
            "grants": {"a/coordinator": ["a/me"]},
        }

    def test_list_empty_store(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = cli.main(["role", "list", "--store", _store(tmp_path)])

        assert code == 0
        assert "no role grants for any role" in capsys.readouterr().out

    def test_list_one_absent_role(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = cli.main(["role", "list", "a/ghost", "--store", _store(tmp_path)])

        assert code == 0
        assert "no role grants for a/ghost" in capsys.readouterr().out

    def test_list_malformed_store_returns_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        store = tmp_path / "role-grants.json"
        store.write_text("{bad", encoding="utf-8")

        code = cli.main(["role", "list", "--store", str(store)])

        assert code == 2
        assert "role list error" in capsys.readouterr().out
