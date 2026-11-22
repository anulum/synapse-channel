# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — single argparse introspection adapter for CLI assembly and completions
"""Read registered options and subparsers from an :class:`argparse.ArgumentParser`.

Python's :mod:`argparse` keeps its action registry (``parser._actions``) and
subparser choice help records (``action._choices_actions``) private, with no
public accessor for enumerating them. This module is the single adapter
permitted to read those standard-library internals; :mod:`synapse_channel.cli`
and :mod:`synapse_channel.cli_completions` inspect parsers exclusively through
the functions below.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

__all__ = [
    "SubparserEntry",
    "parser_long_options",
    "parser_option_strings",
    "parser_subparsers",
]


@dataclass(frozen=True)
class SubparserEntry:
    """One subcommand parser attached to a parent parser, with its help summary.

    Attributes
    ----------
    name : str
        Subcommand name as typed on the command line.
    summary : str
        One-line help string declared on ``add_parser(..., help=...)``, or ``""``
        when no help text was supplied.
    parser : argparse.ArgumentParser
        The child parser registered for ``name``.
    """

    name: str
    summary: str
    parser: argparse.ArgumentParser


def parser_option_strings(parser: argparse.ArgumentParser) -> frozenset[str]:
    """Return every option string declared directly on ``parser``.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Parser whose registered actions are inspected.

    Returns
    -------
    frozenset[str]
        All short and long option flags (for example ``{"-h", "--help", "--token"}``)
        currently registered on ``parser``.
    """
    return frozenset(opt for action in parser._actions for opt in action.option_strings)


def parser_long_options(parser: argparse.ArgumentParser) -> tuple[str, ...]:
    """Return unique long option strings (``--flag``) on ``parser`` in registration order.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Parser whose non-subparser actions are inspected.

    Returns
    -------
    tuple[str, ...]
        Deduplicated long option flags preserving first-seen registration order.
    """
    options: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            continue
        options.extend(opt for opt in action.option_strings if opt.startswith("--"))
    return tuple(dict.fromkeys(options))


def parser_subparsers(parser: argparse.ArgumentParser) -> tuple[SubparserEntry, ...]:
    """Return every subcommand parser attached to ``parser`` in registration order.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Parent parser whose subparser groups are inspected.

    Returns
    -------
    tuple[SubparserEntry, ...]
        One entry per registered subcommand with its help summary and child parser.
    """
    entries: list[SubparserEntry] = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        helps = {choice.dest: str(choice.help or "") for choice in action._choices_actions}
        for sub_name, sub_parser in action.choices.items():
            entries.append(
                SubparserEntry(
                    name=sub_name,
                    summary=helps.get(sub_name, ""),
                    parser=sub_parser,
                )
            )
    return tuple(entries)
