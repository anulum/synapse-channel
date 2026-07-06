# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — auto-action reactor introspection and policy CLI command
"""CLI for the auto-action reactor: preview its model and manage the armed policy it persists.

The reactor turns a chosen subset of the advisor's per-round signals into opt-in automatic actions.
The bare ``synapse auto-action`` command previews the *static model* — which advisory signals map to
which actions, which map to none, and (given ``--arm``/``--all``) how a hypothetical armed set would
read — touching no files and firing nothing.

The ``arm``/``disarm``/``clear``/``show`` subcommands manage the *durable policy* the orchestration
loop reads (:mod:`~synapse_channel.participants.auto_action_store`): a JSON file in the coordination
home (``$SYN_HOME``/``~/synapse``, overridable with ``--store``). ``show`` renders the persisted
posture; ``arm`` and ``disarm`` add or remove actions; ``clear`` disarms everything. Persisting a
policy still arms nothing until an orchestration harness loads it and supplies handlers — these
commands change what *would* fire, never fire anything themselves.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from synapse_channel.ergonomics import syn_home
from synapse_channel.participants.auto_action import (
    AutoAction,
    AutoActionPolicy,
    auto_action_report_to_json,
    describe_auto_actions,
    render_auto_action_report,
)
from synapse_channel.participants.auto_action_store import (
    POLICY_FILENAME,
    AutoActionStoreError,
    load_policy,
    save_policy,
)


def _actions_from_tags(raw: str) -> frozenset[AutoAction]:
    """Resolve a comma-separated tag list into a set of actions, skipping empty segments.

    Raises
    ------
    ValueError
        When a non-empty segment is not the value of an :class:`AutoAction`.
    """
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


def _parse_armed(raw: str | None, *, all_on: bool) -> frozenset[AutoAction]:
    """Resolve the ``--arm``/``--all`` preview selection into a set of armed actions.

    Raises
    ------
    ValueError
        When ``raw`` names an action tag that is not an :class:`AutoAction`.
    """
    if all_on:
        return frozenset(AutoAction)
    if not raw:
        return frozenset()
    return _actions_from_tags(raw)


def _resolve_store_path(store: str | None) -> Path:
    """Return the policy-file path: ``--store`` when given, else the coordination-home default."""
    if store:
        return Path(store)
    return syn_home(os.environ) / POLICY_FILENAME


def _cmd_auto_action(args: argparse.Namespace) -> int:
    """Preview the static auto-action model for a hypothetical armed set (reads nothing)."""
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


def _cmd_show(args: argparse.Namespace) -> int:
    """Render the durable armed policy the orchestration loop would read."""
    path = _resolve_store_path(args.store)
    try:
        policy = load_policy(path)
    except AutoActionStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    report = describe_auto_actions(policy.armed)
    if args.json:
        payload = auto_action_report_to_json(report)
        payload["store"] = {"path": str(path), "exists": path.exists()}
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        state = "persisted" if path.exists() else "not yet created — arms nothing"
        print(f"Durable auto-action policy ({path}, {state}):")
        print(render_auto_action_report(report))
    return 0


def _persist_selection(args: argparse.Namespace, *, disarm: bool) -> int:
    """Arm or disarm the actions named in ``args.actions`` in the durable policy."""
    path = _resolve_store_path(args.store)
    try:
        selected = _actions_from_tags(args.actions)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        current = load_policy(path)
    except AutoActionStoreError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    armed = current.armed - selected if disarm else current.armed | selected
    save_policy(path, AutoActionPolicy(armed=armed))
    _report_persisted(armed, path)
    return 0


def _cmd_arm(args: argparse.Namespace) -> int:
    """Add the named actions to the durable policy."""
    return _persist_selection(args, disarm=False)


def _cmd_disarm(args: argparse.Namespace) -> int:
    """Remove the named actions from the durable policy."""
    return _persist_selection(args, disarm=True)


def _cmd_clear(args: argparse.Namespace) -> int:
    """Disarm every action in the durable policy."""
    path = _resolve_store_path(args.store)
    save_policy(path, AutoActionPolicy())
    _report_persisted(frozenset(), path)
    return 0


def _report_persisted(armed: frozenset[AutoAction], path: Path) -> None:
    """Print the resulting armed set and where it was written."""
    if armed:
        tags = ", ".join(sorted(action.value for action in armed))
        print(f"Armed auto-actions: {tags}")
    else:
        print("No auto-actions armed.")
    print(f"Persisted to {path}")


def _add_store_argument(parser: argparse.ArgumentParser) -> None:
    """Attach the shared ``--store`` override to a subcommand parser."""
    parser.add_argument(
        "--store",
        default=None,
        metavar="PATH",
        help="Policy file to read/write (default: <coordination home>/auto_action_policy.json, "
        "where the home is $SYN_HOME or ~/synapse).",
    )


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``auto-action`` command and its policy subcommands."""
    parser = subparsers.add_parser(
        "auto-action",
        help=(
            "Introspect the opt-in auto-action reactor and manage the durable armed policy the "
            "orchestration loop reads."
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

    policy = parser.add_subparsers(dest="policy_command")

    show = policy.add_parser("show", help="Show the durable armed policy the loop would read.")
    show.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    _add_store_argument(show)
    show.set_defaults(func=_cmd_show)

    arm = policy.add_parser("arm", help="Arm the named actions in the durable policy.")
    arm.add_argument("actions", metavar="ACTIONS", help="Comma-separated actions to arm.")
    _add_store_argument(arm)
    arm.set_defaults(func=_cmd_arm)

    disarm = policy.add_parser("disarm", help="Disarm the named actions in the durable policy.")
    disarm.add_argument("actions", metavar="ACTIONS", help="Comma-separated actions to disarm.")
    _add_store_argument(disarm)
    disarm.set_defaults(func=_cmd_disarm)

    clear = policy.add_parser("clear", help="Disarm every action in the durable policy.")
    _add_store_argument(clear)
    clear.set_defaults(func=_cmd_clear)
