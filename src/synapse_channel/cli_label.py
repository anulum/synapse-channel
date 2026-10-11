# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — command line for a seat's terminal tab label and status lines
"""``synapse label``: say in the terminal which seat works in it.

Run inside a tmux session, the command sets that session's tab label and
status lines from the seat identity, the group profile and the details given
on the command line. Details given once are kept on the session, so a later
call adds to them; ``SYN_IDENTITY`` is used only until a seat is stored.
Outside tmux, and without ``--session``, the command does nothing and exits
0: a terminal that cannot be labelled is not an error.
"""

from __future__ import annotations

import argparse
import os
import subprocess  # nosec B404
from dataclasses import replace
from pathlib import Path

from synapse_channel.service_setup import default_synapse_bin
from synapse_channel.terminal_label import (
    NOT_DECLARED,
    ProfileError,
    TerminalDetails,
    detail_fields,
    details_for,
    load_profile,
    tab_label,
)
from synapse_channel.terminal_label_tmux import apply, own_session, restore, stored_details


def _details(args: argparse.Namespace, session: str | None) -> TerminalDetails:
    """Return the stored details with the identity and the given flags laid over them."""
    details = (
        stored_details(args.tmux_bin, session, runner=subprocess.run)
        if session is not None
        else TerminalDetails()
    )
    # An identity given on the command line always applies. The ambient one
    # applies only while no seat is stored, so it cannot replace a declared seat.
    ambient = os.environ.get("SYN_IDENTITY", "") if details.seat == NOT_DECLARED else ""
    identity = args.identity or ambient
    if identity and not args.refresh:
        derived = details_for(identity, load_profile(Path(args.profile) if args.profile else None))
        details = replace(
            details,
            group=derived.group,
            abbreviation=derived.abbreviation,
            abbreviation_derived=derived.abbreviation_derived,
            project=derived.project,
            seat=derived.seat,
        )

    def given(name: str, stored: str) -> str:
        value = getattr(args, name)
        return value.strip() if isinstance(value, str) and value.strip() else stored

    return replace(
        details,
        focus=given("focus", details.focus),
        model=given("model", details.model),
        vendor=given("vendor", details.vendor),
        session_name=given("session_name", details.session_name),
        native_session=given("native_session", details.native_session),
        terminal_identity=given("terminal_identity", details.terminal_identity),
    )


def _cmd_label(args: argparse.Namespace) -> int:
    """Dispatch ``synapse label``.

    Returns 0 when the label was set, shown or restored, or when there is
    nothing to label; 1 when tmux refused or the named session is missing;
    2 when the profile file cannot be used.
    """
    session = args.session or own_session(args.tmux_bin, runner=subprocess.run, environ=os.environ)
    try:
        details = _details(args, session)
    except ProfileError as exc:
        print(f"synapse label: {exc}")
        return 2
    if args.show:
        print(f"tab label: {tab_label(details)}")
        for field in detail_fields(details):
            print(field)
        return 0
    if session is None:
        return 0
    if args.restore:
        result = restore(args.tmux_bin, session, runner=subprocess.run)
    else:
        result = apply(
            args.tmux_bin,
            session,
            details,
            runner=subprocess.run,
            refresh_command=[
                args.synapse_bin or default_synapse_bin(),
                "label",
                "--refresh",
                f"--session={session}",
                f"--tmux-bin={args.tmux_bin}",
            ],
        )
    print(result.detail)
    if result.applied or result.detail.startswith("disabled"):
        return 0
    return 1


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``label`` command."""
    label = subparsers.add_parser(
        "label",
        help="Set the terminal tab label and status lines that identify this seat.",
    )
    label.add_argument(
        "--session", default=None, help="Exact tmux session; defaults to the one this runs in."
    )
    label.add_argument("--identity", default=None, help="Seat identity; defaults to SYN_IDENTITY.")
    label.add_argument("--focus", default=None, help="Specific focus assigned to this seat.")
    label.add_argument("--vendor", default=None, help="Vendor of the agent in this terminal.")
    label.add_argument("--model", default=None, help="Exact model id; never guessed.")
    label.add_argument("--session-name", default=None, help="Name of the agent session.")
    label.add_argument("--native-session", default=None, help="Native session id of the agent.")
    label.add_argument("--terminal-identity", default=None, help="Terminal identity, if any.")
    label.add_argument("--profile", default=None, help="Group profile file (JSON).")
    label.add_argument("--tmux-bin", default="tmux", help="tmux executable.")
    label.add_argument(
        "--synapse-bin", default=None, help="Synapse executable the resize hook runs."
    )
    mode = label.add_mutually_exclusive_group()
    mode.add_argument("--show", action="store_true", help="Print the label and every detail.")
    mode.add_argument(
        "--refresh", action="store_true", help="Lay the stored details out again for the width."
    )
    mode.add_argument(
        "--restore", action="store_true", help="Put back the session's earlier label and status."
    )
    label.set_defaults(func=_cmd_label)
