# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — public integration catalog checks
"""Keep accepted capabilities explicit and unsupported hosts fail closed."""

from __future__ import annotations

import json

import pytest

from synapse_channel import cli
from synapse_channel.integration_catalog import HOSTS, host_capability


def test_public_catalog_lists_each_reviewed_host(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["integrations", "list"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["host"] for row in rows} == {host.key for host in HOSTS}
    assert host_capability("codex-cli").verified_version == "0.156.0"
    assert host_capability("pi").verified_version == "0.87.1"
    assert host_capability("claude-code").verified_version == "2.1.280"
    assert host_capability("opencode").verified_version == "1.18.32"
    assert all(
        row["version_accepted"] is False for row in rows if row["capability"] == "unsupported"
    )


@pytest.mark.parametrize("host", ["gemini-cli", "claude-desktop"])
def test_unsupported_package_operations_are_explicit(
    host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    for action in ("install", "diagnose", "uninstall"):
        assert cli.main(["integrations", action, host]) == 2
        row = json.loads(capsys.readouterr().out)
        assert row["state"] == "unsupported"
        assert action not in row["lifecycle"]


def test_missing_and_unknown_host_return_clear_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["integrations", "inspect"]) == 2
    assert "host is required" in capsys.readouterr().err
    assert cli.main(["integrations", "inspect", "unknown-host"]) == 2
    assert "unknown integration host" in capsys.readouterr().err


def test_opencode_status_parsing_ignores_path_words() -> None:
    from synapse_channel.cli_integration_catalog import _opencode_state, _row

    row = _row(host_capability("opencode"), "opencode 1.18.32")
    assert row["verified_version"] == "1.18.32"
    assert row["version_accepted"] is True
    assert (
        _opencode_state("config: installed (/tmp/absent)\nplugin: installed (/tmp/absent)")
        == "installed"
    )
    assert (
        _opencode_state("config: absent (/tmp/installed)\nplugin: absent (/tmp/installed)")
        == "absent"
    )
    assert _opencode_state("config: installed (x)\nplugin: absent (y)") == "partial"
