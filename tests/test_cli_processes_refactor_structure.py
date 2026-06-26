# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI compatibility split tests

from __future__ import annotations

from synapse_channel import (
    cli,
    cli_processes,
    cli_processes_hub,
    cli_processes_parsers,
    cli_processes_runtime,
    cli_processes_supervisor,
    cli_processes_team,
    cli_processes_worker,
)


def test_cli_processes_reexports_runtime_and_hub_surface() -> None:
    assert cli_processes._run is cli_processes_runtime._run
    assert cli_processes._cmd_hub is cli_processes_hub._cmd_hub


def test_cli_processes_reexports_worker_surface() -> None:
    assert cli_processes._egress_warning is cli_processes_worker._egress_warning
    assert cli_processes._cmd_worker is cli_processes_worker._cmd_worker


def test_cli_processes_reexports_team_and_supervisor_surface() -> None:
    assert cli_processes._cmd_team is cli_processes_team._cmd_team
    assert cli_processes._cmd_supervisor is cli_processes_supervisor._cmd_supervisor


def test_cli_processes_reexports_parser_registration() -> None:
    assert cli_processes.add_parsers is cli_processes_parsers.add_parsers


def test_top_level_parser_uses_compatibility_command_functions() -> None:
    parser = cli.build_parser()

    hub_args = parser.parse_args(["hub"])
    worker_args = parser.parse_args(["worker"])
    team_args = parser.parse_args(["team"])
    supervisor_args = parser.parse_args(["supervisor"])

    assert hub_args.func is cli_processes._cmd_hub
    assert worker_args.func is cli_processes._cmd_worker
    assert team_args.func is cli_processes._cmd_team
    assert supervisor_args.func is cli_processes._cmd_supervisor
