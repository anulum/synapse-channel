# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — auto-action reactor introspection CLI command
"""CLI wrapper for read-only auto-action reactor introspection.

The auto-action reactor is armed in-process by an orchestration loop, not by a persistent
daemon-side toggle, so there is no live policy to mutate from the terminal. This command exists for
discoverability: it prints which advisory signals map to which opt-in automatic actions, which
signals deliberately map to none, and — given ``--arm``/``--all`` — previews the armed posture a
policy would have. It reads only the static model; it starts nothing and fires nothing.
"""

from __future__ import annotations

import argparse
import json
import sys

from synapse_channel.participants.auto_action import (
    AutoAction,
    auto_action_report_to_json,
    describe_auto_actions,
    render_auto_action_report,
)


def _parse_armed(raw: str | None, *, all_on: bool) -> frozenset[AutoAction]:
    """Resolve the ``--arm``/``--all`` selection into a set of armed actions.

    Raises
    ------
    ValueError
        When ``raw`` names an action tag that is not an :class:`AutoAction`.
    """
    if all_on:
        return frozenset(AutoAction)
    if not raw:
        return frozenset()
    by_tag = {action.value: action for action in AutoAction}
    selected: set[AutoAction] = set()
    for tag in (part.strip() for part in raw.split(",")):
        if not tag:
            continue
        if tag not in by_tag:
            choices = ", ".join(sorted(by_tag))
            raise ValueError(f"unknown auto-action '{tag}'; choose from: {choices}")
        selected.add(by_tag[tag])
    return frozenset(selected)


def _cmd_auto_action(args: argparse.Namespace) -> int:
    """Introspect the auto-action reactor model and print it."""
    try:
        armed = _parse_armed(args.arm, all_on=args.all)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    report = describe_auto_actions(armed)
    if args.json:
        print(json.dumps(auto_action_report_to_json(report), indent=2, sort_keys=True))
    else:
        print(render_auto_action_report(report))
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``auto-action`` subparser."""
    parser = subparsers.add_parser(
        "auto-action",
        help=(
            "Introspect the opt-in auto-action reactor: which advisory signals map to which "
            "automatic actions, and preview an armed policy (read-only)."
        ),
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--arm",
        default=None,
        metavar="ACTIONS",
        help="Comma-separated actions to preview as armed, e.g. 'compact,log,handover'.",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="Preview every action as armed (mirrors AutoActionPolicy.all_on()).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.set_defaults(func=_cmd_auto_action)
