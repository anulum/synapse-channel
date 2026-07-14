# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for dashboard access CLI arguments

from __future__ import annotations

import argparse
from pathlib import Path

from synapse_channel.cli_dashboard_access import add_dashboard_access_arguments


def _parser() -> argparse.ArgumentParser:
    """Return a parser carrying the dashboard access flags."""
    parser = argparse.ArgumentParser(prog="synapse-dashboard")
    add_dashboard_access_arguments(parser)
    return parser


class TestDashboardAccessArguments:
    """Cover the four dashboard access flags registered on the parser."""

    def test_defaults(self) -> None:
        args = _parser().parse_args([])
        assert args.dashboard_token is None
        assert args.dashboard_access_file is None
        assert args.operator is False
        assert args.operator_name is None

    def test_access_file_is_coerced_to_path(self) -> None:
        args = _parser().parse_args(["--dashboard-access-file", "/etc/synapse/access.json"])
        assert isinstance(args.dashboard_access_file, Path)
        assert args.dashboard_access_file == Path("/etc/synapse/access.json")

    def test_operator_flag_arms_writes(self) -> None:
        args = _parser().parse_args(["--operator"])
        assert args.operator is True

    def test_token_and_operator_name_overrides(self) -> None:
        args = _parser().parse_args(["--dashboard-token", "bearer-abc", "--operator-name", "OPS-1"])
        assert args.dashboard_token == "bearer-abc"
        assert args.operator_name == "OPS-1"
