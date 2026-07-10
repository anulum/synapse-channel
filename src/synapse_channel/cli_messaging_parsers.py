# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — messaging CLI parser registration
"""Parser registration for send, wait, and listen commands."""

from __future__ import annotations

import argparse

from synapse_channel.cli_messaging_listen import _cmd_listen
from synapse_channel.cli_messaging_send import _cmd_send
from synapse_channel.cli_messaging_wait import _cmd_wait
from synapse_channel.client.agent import default_hub_uri
from synapse_channel.core.wake_capability import WAKE_PASSIVE


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``send``, ``wait``, and ``listen`` subparsers."""
    send = subparsers.add_parser("send", help="Send one message and optionally await replies.")
    send.add_argument("--uri", default=default_hub_uri())
    send.add_argument("--name", default="USER")
    send.add_argument("--target", default="all")
    send.add_argument(
        "--channel",
        default="",
        help="Deliver only to this private channel's online members (you must be a member).",
    )
    send.add_argument("--wait-seconds", type=float, default=2.0)
    send.add_argument(
        "--priority",
        action="store_true",
        help="Mark as priority so it wakes even directed-only waiters (use sparingly).",
    )
    send.add_argument(
        "--require-recipient",
        action="store_true",
        help=(
            "Print a positive receipt and fail if the hub returns no receipt; directed sends "
            "already fail by default when no consume-live recipient matches."
        ),
    )
    send.add_argument(
        "--receipt-timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for a directed delivery receipt.",
    )
    send.add_argument(
        "--encrypt-key-file",
        default=None,
        help="Encrypt the message payload with this 32-byte local key file before sending.",
    )
    send.add_argument(
        "--encrypt-key-id",
        default="",
        help="Visible key id to carry in the encrypted payload envelope.",
    )
    send.add_argument(
        "--encrypt-recipient",
        dest="encrypt_recipients",
        action="append",
        default=None,
        help="Recipient identity bound into encrypted payload AAD; repeatable.",
    )
    send.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    send.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    send.add_argument("message")
    send.set_defaults(func=_cmd_send)

    wait = subparsers.add_parser(
        "wait", help="Block until a message addressed to you arrives, then exit (a wake trigger)."
    )
    wait.add_argument("--uri", default=default_hub_uri())
    wait.add_argument("--name", default="USER")
    wait.add_argument(
        "--for",
        dest="for_name",
        default=None,
        help="Whose messages to wake on (one, a group, or broadcast); defaults to --name.",
    )
    wait.add_argument(
        "--timeout", type=float, default=0.0, help="Seconds to wait; 0 waits indefinitely."
    )
    wait.add_argument(
        "--directed-only",
        action="store_true",
        help="Wake only on messages that name you (or a group you are in), not broadcasts.",
    )
    wait.add_argument(
        "--role",
        action="append",
        default=None,
        metavar="PROJECT/ROLE",
        help="A <project>/<role> name you also answer to (repeatable), so a message "
        "addressed to the role wakes you, not only your instance name.",
    )
    wait.add_argument(
        "--wake-jitter",
        type=float,
        default=8.0,
        help="Random seconds (0..N) to delay exiting on a broadcast wake, so many "
        "terminals do not re-invoke at once and trip the provider rate limit; 0 disables.",
    )
    wait.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    wait.add_argument(
        "--wake-capability",
        default=WAKE_PASSIVE,
        choices=("direct", "passive", "pane_bridge"),
        help=argparse.SUPPRESS,
    )
    wait.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    wait.set_defaults(func=_cmd_wait)

    listen = subparsers.add_parser("listen", help="Stream channel messages until interrupted.")
    listen.add_argument("--uri", default=default_hub_uri())
    listen.add_argument("--name", default="USER")
    listen.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    listen.add_argument(
        "--ready-timeout", type=float, default=5.0, help="Seconds to await hub readiness."
    )
    listen.add_argument(
        "--for",
        dest="for_name",
        default=None,
        help="Show only chats addressed to this name (or broadcast) and suppress "
        "presence updates — a focused per-agent inbox.",
    )
    listen.add_argument(
        "--decrypt-key-file",
        default=None,
        help="Decrypt encrypted chat payloads with this 32-byte local key file.",
    )
    listen.set_defaults(func=_cmd_listen)
