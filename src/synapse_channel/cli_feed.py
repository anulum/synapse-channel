# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — `synapse feed`: read and acknowledge the forward event feed
"""Read one feed page, acknowledge one, or bind a legacy position, as JSON.

Exit status 0 means the hub served the page, kept the acknowledgement or bound
the position, 1 that it refused the request (the reason is in ``error``), and 2
that it could not be reached.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from synapse_channel.client.agent_lifecycle import default_hub_uri
from synapse_channel.core.feed import FEED_KINDS, FEED_MAX_LIMIT
from synapse_channel.hub_feed import (
    FeedUnavailable,
    request_feed_page,
    send_feed_ack,
    send_feed_bind,
)


def _print(body: dict[str, Any]) -> int:
    """Print the hub's answer and return its exit status."""
    print(json.dumps(body, ensure_ascii=False, sort_keys=True), flush=True)
    return 1 if body.get("error") else 0


def _cmd_read(args: argparse.Namespace) -> int:
    """Read one feed page for ``--consumer`` after the given cursor."""
    try:
        body = asyncio.run(
            request_feed_page(
                uri=args.uri,
                identity=args.name,
                token=args.token,
                consumer=args.consumer,
                kinds=args.kind,
                after_seq=args.after,
                hub_id=args.hub_id,
                journal_instance=args.instance,
                limit=args.limit,
                timeout=args.timeout,
            )
        )
    except FeedUnavailable as exc:
        print(f"feed read: {exc}", file=sys.stderr)
        return 2
    return _print(body)


def _cmd_ack(args: argparse.Namespace) -> int:
    """Acknowledge every row through ``--seq`` for ``--consumer``."""
    try:
        body = asyncio.run(
            send_feed_ack(
                uri=args.uri,
                identity=args.name,
                token=args.token,
                consumer=args.consumer,
                hub_id=args.hub_id,
                journal_instance=args.instance,
                acked_seq=args.seq,
                timeout=args.timeout,
            )
        )
    except FeedUnavailable as exc:
        print(f"feed ack: {exc}", file=sys.stderr)
        return 2
    return _print(body)


def _cmd_bind(args: argparse.Namespace) -> int:
    """Bind ``--consumer``'s legacy position by the proof of its last ingested row."""
    if (args.seq is None) != (args.legacy_seq is None):
        print("feed bind: --seq and --legacy-seq go together", file=sys.stderr)
        return 2
    try:
        body = asyncio.run(
            send_feed_bind(
                uri=args.uri,
                identity=args.name,
                token=args.token,
                consumer=args.consumer,
                kind=args.kind,
                payload_sha256=args.digest,
                seq=args.seq,
                legacy_seq=args.legacy_seq,
                hub_id=args.hub_id,
                timeout=args.timeout,
            )
        )
    except FeedUnavailable as exc:
        print(f"feed bind: {exc}", file=sys.stderr)
        return 2
    return _print(body)


def _connection(parser: argparse.ArgumentParser) -> None:
    """Add the hub connection and consumer arguments both subcommands share."""
    parser.add_argument("--uri", default=default_hub_uri())
    parser.add_argument("--name", required=True, help="This connection's identity.")
    parser.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    parser.add_argument(
        "--consumer",
        required=True,
        help="The consumer: this identity or one of its roles, as granted feed-read.",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait.")


def _page_limit(text: str) -> int:
    """Parse ``--limit``, 1 to the hub's largest page."""
    value = int(text)
    if not 1 <= value <= FEED_MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"--limit must be 1 to {FEED_MAX_LIMIT}")
    return value


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``synapse feed read`` and ``synapse feed ack``."""
    feed = subparsers.add_parser(
        "feed",
        help="Read and acknowledge the hub's forward event feed (findings and marked chat).",
    )
    group = feed.add_subparsers(dest="feed_command", required=True)

    read = group.add_parser("read", help="Print one feed page after a cursor.")
    _connection(read)
    read.add_argument(
        "--kind",
        action="append",
        choices=sorted(FEED_KINDS),
        required=True,
        help="Feed kind to read; repeat for both.",
    )
    read.add_argument("--after", type=int, default=0, help="Exclusive sequence cursor.")
    read.add_argument("--hub-id", default="", help="The cursor's hub; empty on a first read.")
    read.add_argument(
        "--instance", default="", help="The cursor's journal instance; empty on a first read."
    )
    read.add_argument("--limit", type=_page_limit, default=100, help="Rows per page.")
    read.set_defaults(func=_cmd_read)

    ack = group.add_parser("ack", help="Acknowledge every row through a sequence.")
    _connection(ack)
    ack.add_argument("--hub-id", required=True, help="The hub of the acknowledged page.")
    ack.add_argument("--instance", required=True, help="The page's journal instance.")
    ack.add_argument("--seq", type=int, required=True, help="Highest ingested sequence.")
    ack.set_defaults(func=_cmd_ack)

    bind = group.add_parser(
        "bind", help="Bind a legacy position once, by the proof of the last row ingested."
    )
    _connection(bind)
    bind.add_argument(
        "--kind", choices=sorted(FEED_KINDS), required=True, help="The proof row's feed kind."
    )
    bind.add_argument("--digest", required=True, help="The proof row's payload_sha256.")
    bind.add_argument("--seq", type=int, default=None, help="The proof row's sequence.")
    bind.add_argument(
        "--legacy-seq", type=int, default=None, help="The legacy cursor; needs --seq."
    )
    bind.add_argument("--hub-id", default="", help="The hub the legacy position was read from.")
    bind.set_defaults(func=_cmd_bind)
