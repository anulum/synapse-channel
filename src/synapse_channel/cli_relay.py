# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse federation relay` CLI: relay a governed action to a peer hub
"""``synapse federation relay`` — relay a governed operator action to a peer hub.

An operator on one domain relays a bounded, governed action to a peer hub over the
federation transport — the first being a **force-release** of a stuck lease the peer holds.
The peer authorises the relay deny-by-default (mutual TLS + federation scope + namespace
ownership) and audits it, so an operator can free a stuck cross-hub lease without a shell on
the peer, and every such action is attributable.

The command connects directly to the peer hub, exactly as ``federation fetch`` does. The
policy lives in :mod:`synapse_channel.core.operator_relay`, the wire codec in
:mod:`synapse_channel.core.operator_relay_wire`, and the transport in
:mod:`synapse_channel.core.operator_relay_transport`; this is the thin I/O shell, with an
injectable relayer so the command is testable without a real peer.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from collections.abc import Callable, Coroutine
from typing import Any

from synapse_channel.core.operator_relay import RELAYABLE_ACTIONS
from synapse_channel.core.operator_relay_transport import (
    DEFAULT_RELAY_TIMEOUT,
    RelayTransportError,
    relay_operator_action,
)
from synapse_channel.core.operator_relay_wire import RelayActionRequest, RelayActionResult
from synapse_channel.terminal_text import terminal_text

DEFAULT_LOCAL_ID = "operator-relay"
"""Default origin identity stamped on the relay; must match a grant on the peer's policy."""

Relayer = Callable[..., Coroutine[Any, Any, RelayActionResult]]


def _default_operator() -> str:
    """Return the local operating-system user for the relay's audit provenance.

    Falls back to a literal when the user cannot be resolved (an environment with no login
    name), so the relay still carries an operator identity rather than failing to build.
    """
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - getuser only fails on an unusual host environment.
        return "operator"


def _cmd_relay(args: argparse.Namespace, *, relayer: Relayer = relay_operator_action) -> int:
    """Relay a governed action to a peer hub and report the peer's verdict.

    Exit codes: ``0`` when the peer applied the action, ``1`` when the peer refused it or it
    was a no-op (an authorised release of an unclaimed task), ``2`` on a transport failure — the
    relay never reached a verdict, the fail-closed case — and ``3`` when the peer recorded the
    relay pending a second operator's approval under a two-person policy.
    """
    request = RelayActionRequest(
        action=args.action,
        namespace=args.namespace,
        task_id=args.task,
        operator=args.operator or _default_operator(),
        origin_hub_id=args.local_id,
        reason=args.reason or "",
        break_glass=args.break_glass,
    )
    try:
        result = asyncio.run(
            relayer(
                request,
                uri=args.peer,
                local_id=args.local_id,
                token=args.peer_token,
                timeout=args.timeout,
            )
        )
    except RelayTransportError as exc:
        print(f"could not relay the action: {terminal_text(exc)}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {
                    "applied": result.applied,
                    "pending": result.pending,
                    "action": result.action,
                    "namespace": result.namespace,
                    "task_id": result.task_id,
                    "owner_hub_id": result.owner_hub_id,
                    "detail": result.detail,
                },
                indent=2,
            )
        )
    else:
        if result.applied:
            verb = "applied"
        elif result.pending:
            verb = "recorded pending a second operator for"
        else:
            verb = "refused"
        print(
            f"peer {terminal_text(result.owner_hub_id)} {verb} relay "
            f"{terminal_text(result.action)!r}: {terminal_text(result.detail)}"
        )
    if result.pending:
        return 3
    return 0 if result.applied else 1


def add_relay_parser(group: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``relay`` subcommand under a parent command group.

    Composed into ``synapse federation`` by :func:`synapse_channel.cli_federation.add_parsers`,
    keeping this action-performing command in its own module while it lives in the federation
    command tree alongside the peering-management verbs.
    """
    relay = group.add_parser(
        "relay",
        help="Relay a governed operator action (e.g. a force-release) to a peer hub.",
    )
    relay.add_argument(
        "action",
        choices=sorted(RELAYABLE_ACTIONS),
        help="The governed action to relay; the peer resolves it against its allowlist.",
    )
    relay.add_argument("--peer", required=True, help="Peer hub websocket URI (ws:// or wss://).")
    relay.add_argument(
        "--namespace", required=True, help="Namespace the action acts in (owned by the peer)."
    )
    relay.add_argument("--task", required=True, help="Task id the action targets.")
    relay.add_argument(
        "--operator",
        default=None,
        help="Operator identity recorded in the peer's audit trail (default: the OS user).",
    )
    relay.add_argument(
        "--local-id",
        default=DEFAULT_LOCAL_ID,
        help="Origin identity stamped on the relay; must match a grant on the peer's policy.",
    )
    relay.add_argument("--peer-token", default=None, help="Token for a secured peer hub.")
    relay.add_argument(
        "--reason",
        default=None,
        help="Why the action is relayed, recorded in the audit on both hubs. A hub started "
        "with reason-required receipts refuses a relay without one.",
    )
    relay.add_argument(
        "--break-glass",
        action="store_true",
        help="Tag the relay a break-glass emergency override, marked distinctly in the audit.",
    )
    relay.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_RELAY_TIMEOUT,
        help="Seconds to wait for the peer's verdict before failing closed.",
    )
    relay.add_argument("--json", action="store_true", help="Emit the verdict as JSON.")
    relay.set_defaults(func=_cmd_relay)
