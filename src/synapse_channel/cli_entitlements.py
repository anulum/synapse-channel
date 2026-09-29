# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — local account entitlement ledger CLI
"""Record private entitlement facts and inspect advisory window projections."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from synapse_channel.cli_messaging_types import AgentFactory
from synapse_channel.client.agent import SynapseAgent, default_hub_uri
from synapse_channel.connect_failures import closed_after_ready, describe_connect_failure
from synapse_channel.core.approvals import build_approval_report
from synapse_channel.core.compute_credit import (
    ComputeCreditError,
    approval_subject,
    suggest_compute_work,
    validate_compute_task,
)
from synapse_channel.core.entitlement_advert import EntitlementAdvertError, build_advert
from synapse_channel.core.entitlement_store import (
    EntitlementStoreError,
    append_event,
    default_entitlement_store,
    read_events,
)
from synapse_channel.core.entitlement_view import entitlement_view
from synapse_channel.core.entitlements import (
    EntitlementError,
    active_events,
    parse_time,
    validate_event,
)
from synapse_channel.core.journal import EventKind, replay
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.secure_path import SecurePathError, read_owner_only_file_bytes


def _store_path(args: argparse.Namespace) -> Path:
    """Resolve the operator-selected local store path."""
    return Path(args.store).expanduser() if args.store else default_entitlement_store()


def _read_input(path: str) -> dict[str, object]:
    """Load one owner-only JSON event without following a symlink."""
    raw = read_owner_only_file_bytes(path, purpose="entitlement input", max_bytes=65536)
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EntitlementError("entitlement input must be a UTF-8 JSON object") from exc
    if not isinstance(decoded, dict):
        raise EntitlementError("entitlement input must be a JSON object")
    return validate_event(decoded)


def _record(args: argparse.Namespace) -> int:
    """Append one operator or official-source fact to the private ledger."""
    try:
        event = _read_input(args.file)
        inserted = append_event(_store_path(args), event)
    except (EntitlementError, EntitlementStoreError, SecurePathError) as exc:
        print(f"entitlements: {exc}", file=sys.stderr)
        return 2
    outcome = "recorded" if inserted else "already recorded"
    print(f"{outcome}: {event['event_id']}")
    return 0


def _ollama_event(raw: bytes, window_id: str, window_event_id: str) -> dict[str, object]:
    """Extract only token counts from a final official Ollama response."""
    try:
        response = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EntitlementError("Ollama input must be one UTF-8 JSON object") from exc
    if not isinstance(response, dict) or response.get("done") is not True:
        raise EntitlementError("Ollama input must be one final response")
    if ("response" in response) == ("message" in response):
        raise EntitlementError("Ollama input must be a generate or chat response")
    endpoint = "generate" if "response" in response else "chat"
    model = response.get("model")
    if not isinstance(model, str) or not model or len(model) > 128:
        raise EntitlementError("Ollama response must identify a model")
    observed = response.get("created_at")
    parse_time(observed, "created_at")
    prompt_count = response.get("prompt_eval_count")
    output_count = response.get("eval_count")
    if any(type(count) is not int or count < 0 for count in (prompt_count, output_count)):
        raise EntitlementError("Ollama response must contain non-negative token counts")
    digest = hashlib.sha256(raw).hexdigest()
    return validate_event(
        {
            "event_id": f"ollama-{digest}",
            "kind": "usage",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "source": f"ollama:/api/{endpoint}:{model}",
            "confidence": "official",
            "window_id": window_id,
            "window_event_id": window_event_id,
            "source_event_id": f"ollama-{digest}",
            "amount": str(cast(int, prompt_count) + cast(int, output_count)),
            "observed_at": observed,
        }
    )


def _observe_ollama(args: argparse.Namespace) -> int:
    """Import one captured host response without retaining its content."""
    try:
        raw = read_owner_only_file_bytes(args.file, purpose="Ollama response", max_bytes=1048576)
        event = _ollama_event(raw, args.window_id, args.window_event_id)
        store = _store_path(args)
        previous_events = read_events(store)
        active_events(previous_events)
        for previous in previous_events:
            if previous["event_id"] == event["event_id"]:
                comparable = (
                    "kind",
                    "source",
                    "confidence",
                    "window_id",
                    "window_event_id",
                    "source_event_id",
                    "amount",
                    "observed_at",
                )
                if any(previous.get(key) != event.get(key) for key in comparable):
                    raise EntitlementError("Ollama response id conflicts with stored evidence")
                print(f"already recorded: {event['event_id']}")
                return 0
        inserted = append_event(store, event)
    except (EntitlementError, EntitlementStoreError, SecurePathError) as exc:
        print(f"entitlements: {exc}", file=sys.stderr)
        return 2
    print(f"{'recorded' if inserted else 'already recorded'}: {event['event_id']}")
    return 0


def _show(args: argparse.Namespace) -> int:
    """Render current private account and pool evidence as JSON."""
    try:
        report = entitlement_view(
            read_events(_store_path(args)), as_of=datetime.now(timezone.utc), private=True
        )
    except (EntitlementError, EntitlementStoreError, ValueError) as exc:
        print(f"entitlements: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _history(args: argparse.Namespace) -> int:
    """Render all immutable events, including superseded corrections."""
    try:
        events = read_events(_store_path(args))
        active_events(events)
    except (EntitlementError, EntitlementStoreError) as exc:
        print(f"entitlements: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"schema_version": 1, "events": events}, indent=2, sort_keys=True))
    return 0


def _compute_tasks(path: str) -> list[dict[str, object]]:
    """Read a private, bounded list of exact candidate work specifications."""
    raw = read_owner_only_file_bytes(path, purpose="compute tasks", max_bytes=65536)
    try:
        decoded = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ComputeCreditError("compute task input must be UTF-8 JSON") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"tasks"}:
        raise ComputeCreditError("compute task input must contain only tasks")
    tasks = decoded["tasks"]
    if not isinstance(tasks, list) or len(tasks) > 128:
        raise ComputeCreditError("compute task input must list at most 128 tasks")
    if not all(isinstance(item, dict) for item in tasks):
        raise ComputeCreditError("each compute task must be a JSON object")
    return [validate_compute_task(item) for item in tasks]


def _compute_subjects(args: argparse.Namespace) -> int:
    """Show approval subjects bound to the exact private task file."""
    try:
        tasks = _compute_tasks(args.file)
    except (ComputeCreditError, EntitlementError, SecurePathError) as exc:
        print(f"entitlements: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps({"subjects": {str(item["task_id"]): approval_subject(item) for item in tasks}})
    )
    return 0


def _suggest_compute(args: argparse.Namespace) -> int:
    """Match approved private work to fresh eligible ledger windows."""
    try:
        tasks = _compute_tasks(args.file)
        hub_path = Path(args.hub_db)
        if not hub_path.is_file():
            raise ComputeCreditError("hub event store is unavailable")
        board_store = EventStore(hub_path)
        try:
            observed = tuple(board_store.read_all())
            approvals = build_approval_report(observed)
            through_seq = observed[-1].seq if observed else 0
            board = replay(
                board_store,
                up_to_seq=through_seq,
                event_kinds=(EventKind.LEDGER_TASK,),
            ).blackboard
        finally:
            board_store.close()
        report = suggest_compute_work(
            read_events(_store_path(args)),
            tasks,
            approvals,
            board,
            reviewer=args.reviewer,
            as_of=datetime.now(timezone.utc),
            max_evidence_age_seconds=args.max_evidence_age_hours * 3600,
        )
    except (
        ComputeCreditError,
        EntitlementError,
        EntitlementStoreError,
        SecurePathError,
        ValueError,
    ) as exc:
        print(f"entitlements: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _advert(args: argparse.Namespace) -> dict[str, object] | None:
    """Build the redacted advertisement, or print why it cannot be built."""
    try:
        return build_advert(
            read_events(_store_path(args)),
            pool_id=args.pool,
            alias=args.alias,
            as_of=datetime.now(timezone.utc),
        )
    except (EntitlementError, EntitlementStoreError, EntitlementAdvertError, ValueError) as exc:
        print(f"entitlements: {exc}", file=sys.stderr)
        return None


def _advertise(args: argparse.Namespace) -> int:
    """Print, or send to the hub, one pool's redacted advertisement."""
    advert = _advert(args)
    if advert is None:
        return 2
    if args.dry_run:
        print(json.dumps(advert, indent=2, sort_keys=True))
        return 0
    return asyncio.run(
        _send_advert(
            advert,
            uri=args.uri,
            name=args.name,
            token=args.token,
            ready_timeout=args.ready_timeout,
            result_timeout=args.timeout,
        )
    )


async def _send_advert(
    advert: dict[str, object],
    *,
    uri: str,
    name: str,
    token: str | None,
    ready_timeout: float,
    result_timeout: float,
    agent_factory: AgentFactory = SynapseAgent,
) -> int:
    """Send one advertisement as ``name`` and print the hub's verdict.

    Returns ``0`` when recorded, ``1`` when refused, ``2`` when no verdict arrived.
    """
    replies: list[dict[str, object]] = []

    async def collect(data: dict[str, object]) -> None:
        if data.get("type") in {MessageType.ENTITLEMENT_ADVERT_RESULT, MessageType.ERROR}:
            replies.append(data)

    agent = agent_factory(name, collect, uri=uri, verbose=False, token=token)
    connection = asyncio.create_task(agent.connect())
    try:
        if not await agent.wait_until_ready(timeout=ready_timeout) or await closed_after_ready(
            agent
        ):
            print(
                describe_connect_failure(
                    name,
                    uri,
                    close_code=agent.last_close_code,
                    close_reason=agent.last_close_reason,
                )
            )
            return 2
        await agent.send_message(MessageType.ENTITLEMENT_ADVERT, target="System", advert=advert)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(result_timeout, 0.0)
        while not replies and loop.time() <= deadline:
            await asyncio.sleep(0.01)
        if not replies:
            print("advertisement failed: the hub returned no verdict")
            return 2
        verdict = replies[-1]
        detail = str(verdict.get("payload") or "hub refused the advertisement")
        if verdict.get("applied") is True:
            print(f"{detail} (audit seq {verdict.get('audit_seq')})")
            return 0
        print(f"advertisement refused: {detail}")
        return 1
    finally:
        agent.running = False
        connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection


def add_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register owner-local entitlement record, show and history commands."""
    root = subparsers.add_parser(
        "entitlements", help="Manage the private local account and quota ledger."
    )
    commands = root.add_subparsers(dest="entitlement_command", required=True)
    record = commands.add_parser("record", help="Append one owner-only JSON event file.")
    record.add_argument("--file", required=True, help="Owner-only JSON event path (mode 0600).")
    record.add_argument(
        "--store",
        help="Private SQLite ledger path; default: user-local XDG state directory.",
    )
    record.set_defaults(func=_record)
    observe = commands.add_parser(
        "observe-ollama", help="Import final Ollama API token counts from an owner-only JSON file."
    )
    observe.add_argument("--file", required=True, help="Owner-only final API response JSON.")
    observe.add_argument("--window-id", required=True, help="Existing token quota window id.")
    observe.add_argument(
        "--window-event-id", required=True, help="Current window revision event id."
    )
    observe.add_argument("--store", help="Private SQLite ledger path.")
    observe.set_defaults(func=_observe_ollama)
    show = commands.add_parser(
        "show", help="Show private account, pool and forecast evidence as JSON."
    )
    show.add_argument("--store", help="Private SQLite ledger path.")
    show.set_defaults(func=_show)
    history = commands.add_parser(
        "history", help="Show immutable event and correction history as JSON."
    )
    history.add_argument("--store", help="Private SQLite ledger path.")
    history.set_defaults(func=_history)
    subjects = commands.add_parser(
        "compute-subjects", help="Print exact approval subjects for private compute tasks."
    )
    subjects.add_argument("--file", required=True, help="Owner-only compute task JSON file.")
    subjects.set_defaults(func=_compute_subjects)
    suggest = commands.add_parser(
        "suggest-compute", help="Advisory approved work matching private compute credits."
    )
    suggest.add_argument("--file", required=True, help="Owner-only compute task JSON file.")
    suggest.add_argument("--hub-db", required=True, help="Hub event store containing approvals.")
    suggest.add_argument("--reviewer", required=True, help="Exact authorised reviewer identity.")
    suggest.add_argument("--max-evidence-age-hours", type=int, default=168)
    suggest.add_argument("--store", help="Private SQLite entitlement ledger path.")
    suggest.set_defaults(func=_suggest_compute)
    advertise = commands.add_parser(
        "advertise",
        help="Share one pool, redacted and under an alias, with fleet planners via the hub.",
    )
    advertise.add_argument("--pool", required=True, help="Private pool id to advertise.")
    advertise.add_argument(
        "--alias", required=True, help="Name fleet planners see instead of the pool id."
    )
    advertise.add_argument("--store", help="Private SQLite entitlement ledger path.")
    advertise.add_argument(
        "--dry-run", action="store_true", help="Print the advertisement; send nothing."
    )
    advertise.add_argument("--uri", default=default_hub_uri())
    advertise.add_argument("--name", default="", help="Proven owner identity that sends it.")
    advertise.add_argument("--token", default=None, help="Shared-secret token for a secured hub.")
    advertise.add_argument(
        "--token-file", default=None, help="Read the hub token from this file instead."
    )
    advertise.add_argument("--ready-timeout", type=float, default=5.0)
    advertise.add_argument("--timeout", type=float, default=5.0)
    advertise.set_defaults(func=_advertise)
