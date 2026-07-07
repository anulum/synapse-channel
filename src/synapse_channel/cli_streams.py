# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — file/event-store CLI commands (relay, ingest, compact)
"""The file- and event-store-oriented ``synapse`` subcommands.

These three commands read or maintain a hub's on-disk artefacts rather than
talking to a live hub over WebSocket: ``relay`` decodes the lite relay log,
``ingest`` streams durable events from the event store since a cursor (the
read-side memory seam), and ``compact`` applies a retention policy to bound the
durable log. They are grouped here, apart from the hub-client command flows, so
each module stays one responsibility; :func:`add_parsers` registers their
subparsers on the top-level CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from synapse_channel.core.archive_report import (
    ArchiveReportOptions,
    render_archive_report,
    write_archive_report,
)
from synapse_channel.core.compaction import RetentionPolicy, compact
from synapse_channel.core.journal import MEMORY_KINDS
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType, addresses_project, is_recipient
from synapse_channel.relay import decode_lite, load_offset, read_jsonl_since, save_offset


def _format_relay_line(message: dict[str, Any], *, hide_channel_body: bool = False) -> str:
    """Render one decoded relay event as a single human-readable line."""
    timestamp = message.get("timestamp", 0.0)
    channel = str(message.get("channel") or "")
    channel_note = f" channel={channel}" if channel else ""
    payload = (
        "<private channel body hidden>"
        if hide_channel_body and channel
        else str(message.get("payload", ""))
    )
    return (
        f"[{float(timestamp):.3f}] "
        f"{message.get('sender', '?')} -> {message.get('target', 'all')} "
        f"({message.get('type', 'chat')}{channel_note}): {payload}"
    )


def _cmd_relay(args: argparse.Namespace) -> int:
    """Decode and print a lite relay log a hub mirrored with ``--relay-log``.

    Reads the compact newline-delimited log, decodes each event back to a full
    envelope, and prints one line per event. With ``--cursor`` the read position
    is persisted between runs so repeated calls show only what was appended
    since; otherwise reading starts at the ``--since`` byte offset.
    """
    start = load_offset(args.cursor) if args.cursor else max(int(args.since), 0)
    events, cursor = read_jsonl_since(args.relay_log, start)
    roles = tuple(r.strip() for r in (getattr(args, "role", None) or ()) if r.strip())
    for lite in events:
        message = decode_lite(lite)
        channel = str(message.get("channel") or "")
        if args.channel is not None and channel != str(args.channel):
            continue
        if args.public_only and channel:
            continue
        if args.for_name or args.project or roles:
            is_chat = message.get("type") == MessageType.CHAT
            target = str(message.get("target", "all"))
            if args.project:
                keep = is_chat and addresses_project(target, args.project)
            else:
                # A role the reader holds also addresses it, so a per-agent inbox
                # surfaces messages sent to <project>/<role>, not only to its name.
                keep = is_chat and is_recipient(target, args.for_name or "", roles=roles)
            if not keep:
                continue
        print(_format_relay_line(message, hide_channel_body=bool(args.channel_metadata)))
    if args.cursor:
        save_offset(args.cursor, cursor)
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Stream durable events from a hub event store since a sequence cursor.

    This is the read-side ingest seam: it opens a hub's SQLite event store
    (``--db``, e.g. ``~/synapse/hub.db``), reads every event whose sequence is
    above the cursor, and prints one JSON object per line (``seq``/``ts``/``kind``/
    ``payload``). With ``--cursor`` the last sequence is persisted between runs so
    repeated calls walk the log forward with no loss or duplication; ``--memory``
    (or explicit ``--kind``) restricts the stream to the memory kinds a
    persistent-memory adapter ingests. The store is opened read-only-by-use — the
    live hub keeps writing through its own connection (SQLite WAL allows it).
    """
    start = load_offset(args.cursor) if args.cursor else max(int(args.since), 0)
    if args.memory:
        kinds: frozenset[str] | set[str] | None = MEMORY_KINDS
    elif args.kind:
        kinds = set(args.kind)
    else:
        kinds = None
    store = EventStore(args.db)
    try:
        events = store.read_since(start, kinds=kinds, limit=args.limit)
    finally:
        store.close()
    last = start
    for event in events:
        print(
            json.dumps(
                {"seq": event.seq, "ts": event.ts, "kind": event.kind, "payload": event.payload},
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )
        last = event.seq
    if args.cursor and events:
        save_offset(args.cursor, last)
    return 0


def _cmd_compact(args: argparse.Namespace) -> int:
    """Apply a retention policy to a hub event store, bounding the durable log.

    The opt-in compaction knob for the durable write log: resume checkpoints and
    authored findings are kept at full durability and otherwise accumulate
    without bound. It keeps the latest ``--max-checkpoints-per-task`` checkpoints
    per task and ages out findings whose validity window closed more than
    ``--finding-grace-seconds`` ago, deleting only events at or below a floor
    sequence so a downstream ingest cursor never loses an unconsumed event. The
    floor is the lowest sequence every memory consumer has already ingested: pass
    it with ``--floor-seq`` (e.g. the cursor REMANENTIA persists), or ``--all`` to
    treat the whole log as settled when no read-side consumer lags. ``--vacuum``
    reclaims the freed disk pages afterwards.
    """
    store = EventStore(args.db)
    try:
        if args.all:
            floor = store.max_seq()
        elif args.floor_seq is not None:
            floor = max(0, int(args.floor_seq))
        else:
            print(
                "compact needs a floor: pass --floor-seq <seq> (the lowest sequence every "
                "memory consumer has ingested) or --all to treat the whole log as settled.",
                file=sys.stderr,
            )
            return 2
        try:
            policy = RetentionPolicy(
                max_checkpoints_per_task=args.max_checkpoints_per_task,
                finding_grace_seconds=args.finding_grace_seconds,
            )
        except ValueError as exc:
            print(f"invalid retention policy: {exc}", file=sys.stderr)
            return 2
        if policy.is_noop:
            print(
                "compact needs a retention knob: --max-checkpoints-per-task N and/or "
                "--finding-grace-seconds S.",
                file=sys.stderr,
            )
            return 2
        archive_events = store.read_all() if args.archive_report else []
        result = compact(store, policy, floor_seq=floor)
        if args.vacuum:
            store.vacuum()
    finally:
        store.close()
    if args.archive_report:
        report = render_archive_report(
            archive_events,
            result=result,
            options=ArchiveReportOptions(
                source_path=str(args.db),
                generated_at=time.time(),
                max_items=int(args.archive_report_limit),
            ),
        )
        write_archive_report(args.archive_report, report)
    vacuum_note = " (vacuumed)" if args.vacuum else ""
    print(
        f"compacted below seq {result.floor_seq}: removed "
        f"{result.checkpoints_removed} checkpoint(s), "
        f"{result.findings_removed} finding(s){vacuum_note}"
    )
    if args.archive_report:
        print(f"archive report: {args.archive_report}")
    return 0


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``relay``, ``ingest``, and ``compact`` subparsers."""
    relay = subparsers.add_parser("relay", help="Decode and print a hub's lite relay log.")
    relay.add_argument("relay_log", help="Path to the lite relay log to read.")
    relay.add_argument("--since", type=int, default=0, help="Byte offset to start reading from.")
    relay.add_argument(
        "--cursor",
        default=None,
        help="File holding a persisted read offset; resumes where the last run left off.",
    )
    relay.add_argument(
        "--for",
        dest="for_name",
        default=None,
        help="Show only chats addressed to this name (or broadcast), dropping other "
        "traffic and presence noise — a per-agent inbox view.",
    )
    relay.add_argument(
        "--project",
        default=None,
        help="Show chats addressing any agent in this project (the name, 'project/...', "
        "or a broadcast) — a project-stable inbox that survives changing instance ids.",
    )
    relay.add_argument(
        "--role",
        action="append",
        default=None,
        metavar="PROJECT/ROLE",
        help="Also show chats addressed to this <project>/<role> you hold (repeatable), so "
        "a role-addressed message lands in your inbox alongside your name; combine with --for.",
    )
    channel_group = relay.add_mutually_exclusive_group()
    channel_group.add_argument(
        "--channel",
        default=None,
        help="Show only private-channel events for this channel id.",
    )
    channel_group.add_argument(
        "--public-only",
        action="store_true",
        help="Show only events without a private-channel id.",
    )
    relay.add_argument(
        "--channel-metadata",
        action="store_true",
        help="Hide private-channel payload bodies while showing sender, target, and channel id.",
    )
    relay.set_defaults(func=_cmd_relay)

    ingest = subparsers.add_parser(
        "ingest",
        help="Stream durable events from a hub event store since a sequence cursor "
        "(the persistent-memory read-side seam).",
    )
    ingest.add_argument("db", help="Path to the hub event store (e.g. ~/synapse/hub.db).")
    ingest.add_argument(
        "--since", type=int, default=0, help="Return events whose sequence is above this."
    )
    ingest.add_argument(
        "--cursor",
        default=None,
        help="Persist the last sequence to this file for incremental, loss-free resume.",
    )
    ingest.add_argument(
        "--kind",
        action="append",
        default=None,
        metavar="KIND",
        help="Restrict to this event kind (repeatable); omit for every kind.",
    )
    ingest.add_argument(
        "--memory",
        action="store_true",
        help="Restrict to the memory kinds (recall/finding/checkpoint/handoff).",
    )
    ingest.add_argument(
        "--limit", type=int, default=None, help="Cap the number of events returned."
    )
    ingest.set_defaults(func=_cmd_ingest)

    compact_parser = subparsers.add_parser(
        "compact",
        help="Bound the durable event log: keep the latest-N checkpoints per task and "
        "age out expired findings (the retention knob).",
    )
    compact_parser.add_argument("db", help="Path to the hub event store (e.g. ~/synapse/hub.db).")
    compact_parser.add_argument(
        "--max-checkpoints-per-task",
        type=int,
        default=None,
        metavar="N",
        help="Keep only the latest N resume checkpoints per task; older ones are removed.",
    )
    compact_parser.add_argument(
        "--finding-grace-seconds",
        type=float,
        default=None,
        metavar="S",
        help="Remove findings whose validity window closed more than S seconds ago.",
    )
    floor_group = compact_parser.add_mutually_exclusive_group()
    floor_group.add_argument(
        "--floor-seq",
        type=int,
        default=None,
        help="Only compact events at or below this sequence (the min ingested cursor).",
    )
    floor_group.add_argument(
        "--all",
        action="store_true",
        help="Treat the whole log as settled (use only when no read-side consumer lags).",
    )
    compact_parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Reclaim freed disk pages after compaction (rewrites the database file).",
    )
    compact_parser.add_argument(
        "--archive-report",
        default=None,
        metavar="PATH",
        help="Write a static HTML report from the pre-compaction event snapshot.",
    )
    compact_parser.add_argument(
        "--archive-report-limit",
        type=int,
        default=200,
        metavar="N",
        help="Maximum rows shown in each bounded archive-report section.",
    )
    compact_parser.set_defaults(func=_cmd_compact)
