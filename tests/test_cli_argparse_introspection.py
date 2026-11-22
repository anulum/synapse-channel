# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — argparse introspection adapter regressions
"""Pin option and subparser introspection on real :class:`argparse.ArgumentParser` trees."""

from __future__ import annotations

import argparse

from synapse_channel.cli_argparse_introspection import (
    SubparserEntry,
    parser_long_options,
    parser_option_strings,
    parser_subparsers,
)


def test_parser_option_strings_collects_short_and_long_flags() -> None:
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("-t", "--token")
    parser.add_argument("positional")
    assert {"-h", "--help", "-t", "--token"} == parser_option_strings(parser)


def test_parser_long_options_preserves_order_and_deduplicates_without_subparsers() -> None:
    parser = argparse.ArgumentParser(prog="tool", conflict_handler="resolve")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--uri")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("ping", help="Ping the hub.")
    assert parser_long_options(parser) == ("--help", "--uri", "--verbose")


def test_parser_subparsers_returns_entries_with_help_summaries_and_blank_fallback() -> None:
    parser = argparse.ArgumentParser(prog="tool")
    parser.add_argument("--flag", action="store_true")
    assert parser_subparsers(parser) == ()

    sub = parser.add_subparsers(dest="cmd")
    first = sub.add_parser("alpha", help="Run alpha.")
    second = sub.add_parser("beta")
    entries = parser_subparsers(parser)
    assert entries == (
        SubparserEntry(name="alpha", summary="Run alpha.", parser=first),
        SubparserEntry(name="beta", summary="", parser=second),
    )
