# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — contract tests for the lazy CLI registration units

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter

import pytest

from synapse_channel.cli import (
    _REGISTRATION_UNITS,
    _registrar,
    _requested_command,
    _unit_owning,
    build_parser,
)


def _subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    assert parser._subparsers is not None
    action = parser._subparsers._group_actions[0]
    assert isinstance(action, argparse._SubParsersAction)
    return action


def test_units_cover_exactly_the_full_surface() -> None:
    # The union of declared commands must equal what a full build registers;
    # a command declared twice would make lazy ownership ambiguous.
    declared = Counter(command for _spec, commands in _REGISTRATION_UNITS for command in commands)
    duplicated = [command for command, count in declared.items() if count > 1]
    assert not duplicated, f"commands owned by more than one unit: {duplicated}"
    full = _subparsers_action(build_parser())
    assert set(declared) == set(full.choices)


def test_each_unit_registers_exactly_its_declared_commands() -> None:
    for spec, commands in _REGISTRATION_UNITS:
        parser = argparse.ArgumentParser(prog="synapse")
        sub = parser.add_subparsers(dest="command")
        _registrar(spec)(sub)
        assert set(sub.choices) == set(commands), spec


def test_lazy_parser_matches_the_full_parser_per_command() -> None:
    # For every command, the lazily built subparser must render the same help
    # as the one inside the full parser — same options, same defaults text.
    full = _subparsers_action(build_parser())
    for _spec, commands in _REGISTRATION_UNITS:
        for command in commands:
            lazy = _subparsers_action(build_parser(command=command))
            assert set(lazy.choices) <= set(full.choices)
            assert command in lazy.choices
            assert lazy.choices[command].format_help() == full.choices[command].format_help()


def test_unit_owning_resolves_known_and_unknown_names() -> None:
    assert _unit_owning("who") == "synapse_channel.cli_queries:add_parsers"
    assert _unit_owning("participant") == "synapse_channel.cli:_register_participant_group"
    assert _unit_owning("nonesuch") is None


def test_unknown_command_falls_back_to_the_full_parser() -> None:
    fallback = _subparsers_action(build_parser(command="nonesuch"))
    full = _subparsers_action(build_parser())
    assert set(fallback.choices) == set(full.choices)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], None),
        (["--version"], None),
        (["--help"], None),
        (["--", "hub"], "hub"),
        (["send", "all", "hello"], "send"),
        (["-x", "who"], "who"),
    ],
)
def test_requested_command_reads_the_first_positional(
    argv: list[str], expected: str | None
) -> None:
    assert _requested_command(argv) == expected


def test_token_file_companion_survives_the_lazy_path() -> None:
    # The --token/--token-file pairing is applied after registration and must
    # hold for a lazily registered command exactly as it does in a full build.
    lazy = _subparsers_action(build_parser(command="send"))
    options = {
        option for action in lazy.choices["send"]._actions for option in action.option_strings
    }
    assert {"--token", "--token-file"} <= options


def test_dispatch_imports_only_the_owning_unit() -> None:
    # Dispatching one local command through main() must not import the other
    # command families — this is the observable point of lazy registration.
    # (`completions` would not do as the probe: it legitimately rebuilds the
    # full parser at runtime to enumerate every command.)
    probe = (
        "import sys\n"
        "from synapse_channel.cli import main\n"
        "try:\n"
        "    main(['merkle', '--help'])\n"
        "    raise AssertionError('expected SystemExit from --help')\n"
        "except SystemExit as exc:\n"
        "    assert exc.code == 0, exc.code\n"
        "loaded = [m for m in ('synapse_channel.cli_a2a', 'synapse_channel.cli_processes',"
        " 'synapse_channel.core.hub', 'websockets', 'asyncio') if m in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
