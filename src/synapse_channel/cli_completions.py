# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — static shell-completion script generation for the synapse CLI
"""Generated static tab-completion scripts for Bash, Zsh, and Fish.

``synapse completions <shell>`` prints a self-contained completion script to
stdout. The script is *generated from the live argument parser* at print time —
subcommands, nested subcommands (one level, e.g. ``task declare``), and long
options are introspected from :func:`synapse_channel.cli.build_parser` — so the
printed script can never drift from the installed CLI, yet what the user sources
is a plain static script with **no runtime dependency** and no Python started per
keystroke. Re-run the command after an upgrade to refresh the installed script.

Install by writing the output where the shell looks for completions::

    synapse completions bash > ~/.local/share/bash-completion/completions/synapse
    synapse completions zsh  > ~/.zfunc/_synapse       # with fpath+=(~/.zfunc)
    synapse completions fish > ~/.config/fish/completions/synapse.fish

or evaluate it inline from a shell rc file (Bash: ``eval "$(synapse completions
bash)"``).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandSpec:
    """One CLI command's completable surface: options and nested subcommands.

    Parameters
    ----------
    name : str
        The subcommand name as typed (for example ``"task"``).
    summary : str
        The one-line help shown next to the name where the shell supports it.
    options : tuple[str, ...]
        The long option strings (``--flag``) the command accepts, in
        registration order, without duplicates.
    subcommands : tuple[CommandSpec, ...]
        Nested subcommands (for example ``task declare``); empty for a leaf.
    """

    name: str
    summary: str = ""
    options: tuple[str, ...] = ()
    subcommands: tuple[CommandSpec, ...] = field(default_factory=tuple)


def _spec_from_parser(name: str, summary: str, parser: argparse.ArgumentParser) -> CommandSpec:
    """Build a :class:`CommandSpec` from one parser, recursing into subparsers."""
    options: list[str] = []
    nested: list[CommandSpec] = []
    # argparse keeps its action registry private; cli.py walks the same fields.
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {choice.dest: str(choice.help or "") for choice in action._choices_actions}
            for sub_name, sub_parser in action.choices.items():
                nested.append(_spec_from_parser(sub_name, helps.get(sub_name, ""), sub_parser))
        else:
            options.extend(opt for opt in action.option_strings if opt.startswith("--"))
    return CommandSpec(
        name=name,
        summary=summary,
        options=tuple(dict.fromkeys(options)),
        subcommands=tuple(nested),
    )


def command_tree() -> CommandSpec:
    """Return the full ``synapse`` command tree from the live parser.

    The import is deferred because :mod:`synapse_channel.cli` registers this
    module's subparser — importing it at module load would be circular.

    Returns
    -------
    CommandSpec
        The root spec; its ``subcommands`` are the top-level CLI subcommands.
    """
    from synapse_channel.cli import build_parser

    return _spec_from_parser("synapse", "", build_parser())


def _sq(text: str) -> str:
    """Return ``text`` wrapped in shell single quotes, quoting embedded quotes."""
    return "'" + text.replace("'", "'\\''") + "'"


def _summary_for_zsh(text: str) -> str:
    """Return a help line safe inside a zsh ``_describe`` entry (no raw colon)."""
    return text.replace(":", " —")


# -- bash ----------------------------------------------------------------------


def _bash_leaf(spec: CommandSpec, indent: str) -> list[str]:
    """Emit the option completion for a command without nested subcommands."""
    words = " ".join(spec.options)
    return [f'{indent}COMPREPLY=( $(compgen -W {_sq(words)} -- "$cur") )']


def _bash_group(spec: CommandSpec, indent: str) -> list[str]:
    """Emit the two-position completion for a command with nested subcommands."""
    names = " ".join(sub.name for sub in spec.subcommands)
    own = " ".join(spec.options)
    lines = [
        f'{indent}if [ "$COMP_CWORD" -eq 2 ]; then',
        f'{indent}    COMPREPLY=( $(compgen -W {_sq(names + " " + own)} -- "$cur") )',
        f"{indent}    return 0",
        f"{indent}fi",
        f'{indent}case "$second" in',
    ]
    for sub in spec.subcommands:
        joined = " ".join(sub.options)
        lines.append(
            f'{indent}    {sub.name}) COMPREPLY=( $(compgen -W {_sq(joined)} -- "$cur") ) ;;'
        )
    lines.append(f'{indent}    *) COMPREPLY=( $(compgen -W {_sq(own)} -- "$cur") ) ;;')
    lines.append(f"{indent}esac")
    return lines


def bash_script(root: CommandSpec) -> str:
    """Render the Bash completion script for the given command tree.

    Parameters
    ----------
    root : CommandSpec
        The root spec from :func:`command_tree`.

    Returns
    -------
    str
        A self-contained script registering ``complete -F _synapse synapse``.
    """
    top = " ".join([*(sub.name for sub in root.subcommands), *root.options])
    lines = [
        "# synapse shell completion (bash) — generated by `synapse completions bash`",
        "_synapse() {",
        "    local cur first second",
        "    COMPREPLY=()",
        '    cur="${COMP_WORDS[COMP_CWORD]}"',
        '    first="${COMP_WORDS[1]}"',
        '    second="${COMP_WORDS[2]}"',
        '    if [ "$COMP_CWORD" -eq 1 ]; then',
        f'        COMPREPLY=( $(compgen -W {_sq(top)} -- "$cur") )',
        "        return 0",
        "    fi",
        '    case "$first" in',
    ]
    for sub in root.subcommands:
        lines.append(f"        {sub.name})")
        body = _bash_group(sub, " " * 12) if sub.subcommands else _bash_leaf(sub, " " * 12)
        lines.extend(body)
        lines.append("            ;;")
    lines.extend(["    esac", "    return 0", "}", "complete -F _synapse synapse", ""])
    return "\n".join(lines)


# -- zsh -----------------------------------------------------------------------


def _zsh_describe(entries: list[CommandSpec], label: str, indent: str) -> list[str]:
    """Emit a ``_describe`` block offering ``entries`` as ``label`` completions."""
    lines = [f"{indent}local -a candidates", f"{indent}candidates=("]
    for spec in entries:
        described = spec.name
        if spec.summary:
            described += ":" + _summary_for_zsh(spec.summary)
        lines.append(f"{indent}    {_sq(described)}")
    lines.append(f"{indent})")
    lines.append(f"{indent}_describe -t commands {_sq(label)} candidates")
    return lines


def zsh_script(root: CommandSpec) -> str:
    """Render the Zsh completion script for the given command tree.

    The output works both placed on ``fpath`` as ``_synapse`` (the ``#compdef``
    header) and evaluated inline (the ``compdef`` tail guard).

    Parameters
    ----------
    root : CommandSpec
        The root spec from :func:`command_tree`.

    Returns
    -------
    str
        A self-contained completion script for ``synapse``.
    """
    lines = [
        "#compdef synapse",
        "# synapse shell completion (zsh) — generated by `synapse completions zsh`",
        "_synapse() {",
        "    local first second",
        "    first=${words[2]}",
        "    second=${words[3]}",
        "    if (( CURRENT == 2 )); then",
        *_zsh_describe(list(root.subcommands), "synapse command", " " * 8),
        "        return",
        "    fi",
        "    case $first in",
    ]
    for sub in root.subcommands:
        lines.append(f"        {sub.name})")
        if sub.subcommands:
            lines.append("            if (( CURRENT == 3 )); then")
            lines.extend(
                _zsh_describe(list(sub.subcommands), f"synapse {sub.name} subcommand", " " * 16)
            )
            lines.append("                return")
            lines.append("            fi")
            lines.append("            case $second in")
            for nested in sub.subcommands:
                joined = " ".join(nested.options)
                lines.append(f"                {nested.name}) compadd -- {joined} ;;")
            own = " ".join(sub.options)
            lines.extend([f"                *) compadd -- {own} ;;", "            esac"])
        else:
            lines.append(f"            compadd -- {' '.join(sub.options)}")
        lines.append("            ;;")
    lines.extend(
        [
            "    esac",
            "}",
            'if [ "${funcstack[1]}" = "_synapse" ]; then',
            '    _synapse "$@"',
            "else",
            "    compdef _synapse synapse",
            "fi",
            "",
        ]
    )
    return "\n".join(lines)


# -- fish ----------------------------------------------------------------------


def fish_script(root: CommandSpec) -> str:
    """Render the Fish completion script for the given command tree.

    Parameters
    ----------
    root : CommandSpec
        The root spec from :func:`command_tree`.

    Returns
    -------
    str
        A series of ``complete -c synapse`` declarations.
    """
    lines = [
        "# synapse shell completion (fish) — generated by `synapse completions fish`",
        "complete -c synapse -f",
    ]
    for option in root.options:
        lines.append(f"complete -c synapse -n __fish_use_subcommand -l {option[2:]}")
    for sub in root.subcommands:
        described = f" -d {_sq(sub.summary)}" if sub.summary else ""
        lines.append(f"complete -c synapse -n __fish_use_subcommand -a {sub.name}{described}")
        seen = f"__fish_seen_subcommand_from {sub.name}"
        if sub.subcommands:
            nested_names = " ".join(nested.name for nested in sub.subcommands)
            offer = f"{seen}; and not __fish_seen_subcommand_from {nested_names}"
            for nested in sub.subcommands:
                described = f" -d {_sq(nested.summary)}" if nested.summary else ""
                lines.append(f"complete -c synapse -n {_sq(offer)} -a {nested.name}{described}")
                inside = f"{seen}; and __fish_seen_subcommand_from {nested.name}"
                for option in nested.options:
                    lines.append(f"complete -c synapse -n {_sq(inside)} -l {option[2:]}")
        for option in sub.options:
            lines.append(f"complete -c synapse -n {_sq(seen)} -l {option[2:]}")
    lines.append("")
    return "\n".join(lines)


# -- command -------------------------------------------------------------------

_RENDERERS = {"bash": bash_script, "zsh": zsh_script, "fish": fish_script}


def _cmd_completions(args: argparse.Namespace) -> int:
    """Dispatch ``completions``: print the requested shell's script to stdout."""
    print(_RENDERERS[str(args.shell)](command_tree()), end="")
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``completions`` subparser."""
    completions = subparsers.add_parser(
        "completions",
        help="Print a static tab-completion script for bash, zsh, or fish.",
        description=(
            "Print a self-contained tab-completion script for the given shell. "
            "The script is generated from the installed CLI, needs no extra "
            "dependency, and is refreshed by re-running this command after an "
            "upgrade. Install it where the shell looks for completions, e.g. "
            "`synapse completions fish > ~/.config/fish/completions/synapse.fish`, "
            'or evaluate it inline, e.g. `eval "$(synapse completions bash)"`.'
        ),
    )
    completions.add_argument(
        "shell",
        choices=sorted(_RENDERERS),
        help="Shell dialect to generate.",
    )
    completions.set_defaults(func=_cmd_completions)
