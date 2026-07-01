# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shell-completion generation regressions

from __future__ import annotations

import shutil
import subprocess  # nosec B404 — drives bash/zsh/fish syntax checks on generated scripts
from pathlib import Path

import pytest

from synapse_channel.cli import build_parser
from synapse_channel.cli_completions import (
    CommandSpec,
    _cmd_completions,
    bash_script,
    command_tree,
    fish_script,
    zsh_script,
)
from synapse_channel.surface_taxonomy import CLI_TAXONOMY


def _spec() -> CommandSpec:
    """A small fixed tree exercising quoting, nesting, and leaf commands."""
    return CommandSpec(
        name="synapse",
        options=("--help", "--version"),
        subcommands=(
            CommandSpec(
                name="task",
                summary="Declare and update the shared plan: it's the board.",
                options=("--help", "--uri"),
                subcommands=(
                    CommandSpec(
                        name="declare",
                        summary="Declare a task.",
                        options=("--help", "--title", "--depends-on"),
                    ),
                    CommandSpec(name="update", summary="Update a task.", options=("--status",)),
                ),
            ),
            CommandSpec(name="health", summary="Probe the hub.", options=("--help", "--uri")),
        ),
    )


# -- introspection of the live parser -----------------------------------------


def test_command_tree_covers_the_live_surface() -> None:
    """Every classified subcommand appears in the tree introspected from the parser."""
    root = command_tree()
    names = {sub.name for sub in root.subcommands}
    missing = set(CLI_TAXONOMY) - names
    assert not missing, f"taxonomy commands absent from the completion tree: {sorted(missing)}"


def test_command_tree_extracts_nested_subcommands_and_flags() -> None:
    """The tree carries nested groups and the newest hub flags, so nothing drifts."""
    root = command_tree()
    by_name = {sub.name: sub for sub in root.subcommands}
    hub = by_name["hub"]
    assert "--federation-observe-only" in hub.options
    assert "--require-message-auth" in hub.options
    task = by_name["task"]
    nested = {sub.name for sub in task.subcommands}
    assert "declare" in nested
    completions = by_name["completions"]
    assert completions.subcommands == ()


def test_command_tree_keeps_only_long_options_without_duplicates() -> None:
    """Short flags are dropped and repeated registrations collapse to one entry."""
    root = command_tree()
    for sub in root.subcommands:
        assert all(option.startswith("--") for option in sub.options)
        assert len(sub.options) == len(set(sub.options))


# -- bash ----------------------------------------------------------------------


def test_bash_script_completes_commands_nested_groups_and_flags() -> None:
    script = bash_script(_spec())
    assert "complete -F _synapse synapse" in script
    # top level offers the subcommands and the root flags
    assert "'task health --help --version'" in script
    # the nested group offers its subcommands plus its own flags at position two
    assert "'declare update --help --uri'" in script
    # a nested leaf offers its flags
    assert "declare) COMPREPLY=( $(compgen -W '--help --title --depends-on'" in script


def test_bash_script_quotes_embedded_single_quotes() -> None:
    """A summary or option containing a quote must not break the script."""
    script = bash_script(_spec())
    proc = subprocess.run(  # nosec B603 B607 — fixed argv, generated input
        ["bash", "-n", "/dev/stdin"], input=script, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


def test_bash_script_for_the_live_tree_passes_a_bash_syntax_check(tmp_path: Path) -> None:
    """The real generated script parses cleanly under ``bash -n``."""
    script = bash_script(command_tree())
    target = tmp_path / "synapse.bash"
    target.write_text(script, encoding="utf-8")
    proc = subprocess.run(  # nosec B603 B607 — fixed argv over a written temp file
        ["bash", "-n", str(target)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr


# -- zsh -----------------------------------------------------------------------


def test_zsh_script_describes_commands_and_guards_direct_evaluation() -> None:
    script = zsh_script(_spec())
    assert script.startswith("#compdef synapse\n")
    assert "'health:Probe the hub.'" in script
    # embedded quote in a summary is escaped, and the raw colon is replaced
    assert "'task:Declare and update the shared plan'\\'' —" in script or (
        "task:Declare and update the shared plan" in script and "it'\\''s the board" in script
    )
    # the tail registers via compdef when evaluated inline instead of from fpath
    assert 'if [ "${funcstack[1]}" = "_synapse" ]; then' in script
    assert "compdef _synapse synapse" in script


def test_zsh_script_completes_nested_groups() -> None:
    script = zsh_script(_spec())
    assert "'declare:Declare a task.'" in script
    assert "declare) compadd -- --help --title --depends-on ;;" in script
    assert "*) compadd -- --help --uri ;;" in script


def test_zsh_script_for_the_live_tree_passes_a_zsh_syntax_check_when_available(
    tmp_path: Path,
) -> None:
    """When a zsh binary exists, the real generated script parses under ``zsh -n``."""
    script = zsh_script(command_tree())
    assert "#compdef synapse" in script
    zsh = shutil.which("zsh")
    if zsh:
        target = tmp_path / "_synapse"
        target.write_text(script, encoding="utf-8")
        proc = subprocess.run(  # nosec B603 — resolved binary over a written temp file
            [zsh, "-n", str(target)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr


# -- fish ----------------------------------------------------------------------


def test_fish_script_declares_commands_descriptions_and_flags() -> None:
    script = fish_script(_spec())
    assert script.splitlines()[1] == "complete -c synapse -f"
    assert "complete -c synapse -n __fish_use_subcommand -a health -d 'Probe the hub.'" in script
    assert "complete -c synapse -n __fish_use_subcommand -l version" in script
    # nested groups are offered only before one of them is typed
    assert (
        "-n '__fish_seen_subcommand_from task; and not __fish_seen_subcommand_from "
        "declare update' -a declare" in script
    )
    # nested flags require both the group and the nested word
    assert (
        "-n '__fish_seen_subcommand_from task; and __fish_seen_subcommand_from declare' "
        "-l title" in script
    )


def test_fish_script_for_the_live_tree_passes_a_fish_syntax_check_when_available(
    tmp_path: Path,
) -> None:
    """When a fish binary exists, the real generated script parses under ``fish -n``."""
    script = fish_script(command_tree())
    assert "complete -c synapse -f" in script
    fish = shutil.which("fish")
    if fish:
        target = tmp_path / "synapse.fish"
        target.write_text(script, encoding="utf-8")
        proc = subprocess.run(  # nosec B603 — resolved binary over a written temp file
            [fish, "-n", str(target)], capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr


# -- command dispatch ----------------------------------------------------------


def test_cmd_completions_prints_the_requested_dialect(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    for shell, marker in (
        ("bash", "complete -F _synapse synapse"),
        ("zsh", "#compdef synapse"),
        ("fish", "complete -c synapse -f"),
    ):
        args = parser.parse_args(["completions", shell])
        assert args.func is _cmd_completions
        assert _cmd_completions(args) == 0
        assert marker in capsys.readouterr().out


def test_completions_rejects_an_unknown_shell() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["completions", "powershell"])
    assert excinfo.value.code == 2


def test_zsh_script_renders_an_empty_command_tree() -> None:
    """A tree with no subcommands still emits a valid describe block."""
    script = zsh_script(CommandSpec(name="synapse", options=("--help",)))
    assert "candidates=(" in script
    assert "_describe -t commands 'synapse command' candidates" in script
