# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — doctor reports whether the hubs following a token set applied it
"""Exercise ``synapse doctor --token-set`` against real published hub states."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_doctor
from synapse_channel.cli_doctor_token_set import diagnose_token_set
from synapse_channel.client.diagnostics import Diagnosis
from synapse_channel.core.token_set import TokenSetAuthenticator, state_directory
from synapse_channel.token_set_hub_status import publish_hub_state
from test_token_set_hub_status import _killed_hub, _write


def _only(diagnoses: list[Diagnosis]) -> Diagnosis:
    (diagnosis,) = diagnoses
    assert diagnosis.check == "token-set"
    return diagnosis


def test_nothing_is_checked_without_a_set() -> None:
    assert diagnose_token_set(None) == []


def test_a_set_no_running_hub_reports_is_unknown_never_healthy(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")
    idle = _only(diagnose_token_set(path))
    assert (idle.status, "state is unknown" in idle.detail) == ("warn", True)
    _killed_hub(path)
    gone = _only(diagnose_token_set(str(path)))
    assert gone.status == "warn"
    assert f"synapse token-set status {path} --prune" in gone.remedy


def test_running_hubs_pass_only_when_every_one_applied_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")
    authenticator = TokenSetAuthenticator(path)
    writer = publish_hub_state(authenticator)
    assert writer is not None
    try:
        healthy = _only(diagnose_token_set(path))
        assert (healthy.status, healthy.detail) == ("pass", f"1 running hub(s) applied {path}")
        _write(path, raw="{broken")
        pending = _only(diagnose_token_set(path))
        assert pending.status == "warn"
        assert f"{writer.path.name} pending" in pending.detail
        authenticator.refresh()
        stale = _only(diagnose_token_set(path))
        assert f"{writer.path.name} stale (invalid)" in stale.detail
        assert "keeps its last accepted set" in stale.remedy
        _write(path)
        authenticator.refresh()
        _killed_hub(path)
        leftover = _only(diagnose_token_set(path))
        assert leftover.status == "warn"
        assert "files of 1 gone hub(s) remain" in leftover.detail
        assert leftover.remedy == f"run synapse token-set status {path} --prune"
    finally:
        writer.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symbolic links")
def test_state_that_cannot_be_read_fails(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")
    directory = state_directory(path)
    directory.mkdir(mode=0o700)
    (directory / "hub-3-3.json").write_text("{}")
    (directory / "hub-3-3.lock").symlink_to(tmp_path / "elsewhere")
    failed = _only(diagnose_token_set(path))
    assert failed.status == "fail"
    assert failed.detail.startswith(f"cannot read the hub states of {path}")


async def test_the_doctor_run_includes_the_token_set_check(tmp_path: Path) -> None:
    path = _write(tmp_path / "tokens.json")

    async def no_roster(**_: Any) -> list[str]:
        return []

    code, lines, diagnoses = await cli_doctor._diagnose(
        feed_tail_reader=lambda _env: [],
        cursor_names_reader=lambda _env: [],
        uri="ws://127.0.0.1:8876",
        project="demorepo",
        agent_id=None,
        token=None,
        roster_probe=no_roster,
        token_set=path,
    )
    assert [d.status for d in diagnoses if d.check == "token-set"] == ["warn"]
    assert any("token-set" in line and "state is unknown" in line for line in lines)
    assert code == 0


def test_the_doctor_command_passes_the_set_through(tmp_path: Path) -> None:
    seen: list[Any] = []

    async def runner(**kwargs: Any) -> tuple[int, list[str], list[Diagnosis]]:
        seen.append(kwargs["token_set"])
        return 0, [], []

    parser = cli.build_parser()
    target = str(tmp_path / "tokens.json")
    for argv, expected in ((["doctor"], None), (["doctor", "--token-set", target], target)):
        assert cli_doctor._cmd_doctor(parser.parse_args(argv), diagnose_runner=runner) == 0
        assert seen[-1] == expected
