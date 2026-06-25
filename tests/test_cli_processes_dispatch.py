# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the long-running process commands (hub/worker/team/supervisor)

from __future__ import annotations

import argparse
import os

from synapse_channel import cli, cli_processes


def test_run_executes_coroutine() -> None:
    marker: list[bool] = []

    async def noop() -> None:
        marker.append(True)

    cli_processes._run(noop())
    assert marker == [True]


def test_parser_routes_to_team() -> None:
    args = cli.build_parser().parse_args(["team", "--no-workers"])
    assert args.func is cli_processes._cmd_team
    assert args.no_workers is True


def test_main_routes_to_hub_fail_fast() -> None:
    assert cli.main(["hub", "--host", "0.0.0.0", "--port", "0"]) == 2


def test_resolve_token_from_env_without_handler_patch() -> None:
    previous = os.environ.get(cli.TOKEN_ENV)
    os.environ[cli.TOKEN_ENV] = "env-tok"
    try:
        args = argparse.Namespace(token=None, token_file=None)
        assert cli._resolve_token(args) == "env-tok"
    finally:
        if previous is None:
            os.environ.pop(cli.TOKEN_ENV, None)
        else:
            os.environ[cli.TOKEN_ENV] = previous
