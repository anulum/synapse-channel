# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — CLI reference (docs/cli.md) drift guard
"""Guard `docs/cli.md` against drift from the live CLI command registry.

`test_surface_taxonomy.py` already binds the live subcommands to the stability
taxonomy and to `docs/public-surface.md`. This module complements it by binding
the live registry to the human `docs/cli.md` reference in both directions, plus
locking the Wave-2 wording the reviewer flagged (auth-timeout reap window,
default-identity derivation, and the read-only multi-hub observe/follow framing)
so a doc edit cannot silently reintroduce those drifts.
"""

from __future__ import annotations

import re
from pathlib import Path

from synapse_channel.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
_CLI_DOC = ROOT / "docs" / "cli.md"

# A `synapse <command>` mention: the first token names a top-level subcommand.
_COMMAND_MENTION = re.compile(r"`synapse ([a-z][a-z0-9-]*)")


def _live_commands() -> set[str]:
    """Return the top-level subcommand names on the live CLI parser."""
    parser = build_parser()
    assert parser._subparsers is not None
    choices = parser._subparsers._group_actions[0].choices or {}
    return {str(name) for name in choices}


def _documented_commands() -> set[str]:
    """Return the top-level commands mentioned as ``synapse <command>`` in cli.md."""
    return set(_COMMAND_MENTION.findall(_CLI_DOC.read_text(encoding="utf-8")))


def test_every_live_command_is_documented_in_the_cli_reference() -> None:
    # the drift guard: a new subcommand cannot ship without a cli.md entry
    undocumented = sorted(_live_commands() - _documented_commands())
    assert undocumented == [], f"live commands missing from docs/cli.md: {undocumented}"


def test_cli_reference_mentions_no_unknown_command() -> None:
    # the reverse guard: a removed/typo'd command cannot linger in cli.md
    stale = sorted(_documented_commands() - _live_commands())
    assert stale == [], f"docs/cli.md names non-existent commands: {stale}"


def test_cli_reference_states_the_auth_timeout_reap_window() -> None:
    # Wave-2: the ghost window (a name lingers until the never-bound socket is
    # reaped) must stay documented with its timeout default and close code.
    doc = _CLI_DOC.read_text(encoding="utf-8")
    assert "reap sockets that never bind a name within `--auth-timeout`" in doc
    assert "`4012`" in doc


def test_cli_reference_states_default_identity_derivation() -> None:
    # Wave-2: an omitted --name must document its environment/git-project derivation.
    doc = _CLI_DOC.read_text(encoding="utf-8")
    assert "an omitted `--name` resolves from an agreeing environment or `<git-project>/mcp`" in doc


def test_cli_reference_frames_multihub_as_read_only_observe_follow() -> None:
    # Wave-2: multi-hub must read as read-only observe/follow of a peer's log, not
    # as an operator-configurable claim-forwarding surface (the flagged contradiction).
    doc = _CLI_DOC.read_text(encoding="utf-8")
    assert "Observe or follow a peer hub's event log" in doc
    assert "synapse multihub observe" in doc
    assert "synapse multihub follow" in doc
    # cli.md must not present operator claim-forwarding configuration as a shipped CLI route.
    assert "claim_peers" not in doc
