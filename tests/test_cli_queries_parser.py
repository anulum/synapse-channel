# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the read-only hub query commands (who/state/board/manifest/health)

from __future__ import annotations

from synapse_channel import cli, cli_queries


def test_parser_who() -> None:
    args = cli.build_parser().parse_args(["who", "--project", "quantum"])
    assert args.project == "quantum"
    assert args.me is False
    assert args.all_mailbox_pending is False
    assert args.func is cli_queries._cmd_who


def test_parser_who_me() -> None:
    args = cli.build_parser().parse_args(["who", "--name", "quantum/codex-1", "--me"])
    assert args.name == "quantum/codex-1"
    assert args.me is True
    assert args.func is cli_queries._cmd_who


def test_parser_who_all_mailbox_pending_aliases() -> None:
    explicit = cli.build_parser().parse_args(["who", "--all-mailbox-pending"])
    concise = cli.build_parser().parse_args(["who", "--all"])

    assert explicit.all_mailbox_pending is True
    assert concise.all_mailbox_pending is True


def test_parser_state() -> None:
    args = cli.build_parser().parse_args(["state", "--owner", "quantum"])
    assert args.owner == "quantum"
    assert args.func is cli_queries._cmd_state


def test_parser_board() -> None:
    args = cli.build_parser().parse_args(["board", "--name", "WATCH"])
    assert args.name == "WATCH"
    assert args.func is cli_queries._cmd_board


def test_parser_manifest() -> None:
    manifest = cli.build_parser().parse_args(["manifest", "--name", "WATCH"])
    assert manifest.name == "WATCH"
    assert manifest.func is cli_queries._cmd_manifest


def test_parser_health() -> None:
    args = cli.build_parser().parse_args(["health", "--uri", "ws://x"])
    assert args.func is cli_queries._cmd_health
    assert args.uri == "ws://x"
