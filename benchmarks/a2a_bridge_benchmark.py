# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — benchmark A2A bridge local operations
"""Measure local Agent2Agent bridge operation costs.

This benchmark stays in-process and dependency-free. It times bridge task
creation, SYNAPSE reply correlation, task listing, push-delivery callback
dispatch, and bounded subscription fanout. It does not measure third-party A2A
conformance, remote webhook latency, or real network throughput.
"""

from __future__ import annotations

import argparse
import json
import platform
import threading
import time
from pathlib import Path

from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_server import A2ABridge

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = BENCHMARK_DIR / "results" / "a2a_bridge_benchmark.json"
DEFAULT_TASK_COUNT = 250
DEFAULT_SUBSCRIBER_COUNT = 32


class _Agent:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def chat(self, message: str, *, target: str = "all") -> None:
        self.messages.append((target, message))


def host_profile() -> dict[str, str]:
    """Return host metadata for interpreting local wall-clock numbers."""
    return {
        "cpu": platform.processor() or "unknown",
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def _message(index: int) -> JsonMap:
    return {
        "messageId": f"m-{index}",
        "role": "ROLE_USER",
        "taskId": f"task-{index}",
        "contextId": f"ctx-{index}",
        "parts": [{"text": f"benchmark task {index}"}],
    }


def _bridge(*, push_deliveries: list[JsonMap] | None = None) -> A2ABridge:
    def deliver(delivery: JsonMap) -> None:
        if push_deliveries is not None:
            push_deliveries.append(delivery)

    return A2ABridge(
        agent=_Agent(),
        agent_card={},
        target="WORKER",
        push_deliverer=deliver,
        task_timeout_seconds=0.0,
    )


def _rate(count: int, seconds: float) -> float:
    return count / seconds if seconds > 0.0 else 0.0


def profile(
    *, task_count: int = DEFAULT_TASK_COUNT, subscriber_count: int = DEFAULT_SUBSCRIBER_COUNT
) -> JsonMap:
    """Run the in-process A2A bridge benchmark and return a JSON summary."""
    task_count = max(task_count, 1)
    subscriber_count = max(subscriber_count, 1)
    push_deliveries: list[JsonMap] = []
    bridge = _bridge(push_deliveries=push_deliveries)

    start = time.perf_counter()
    tasks = [bridge.create_working_task(_message(index)) for index in range(task_count)]
    creation_seconds = time.perf_counter() - start

    for task in tasks:
        bridge.create_push_notification_config(
            str(task["id"]),
            {"id": f"cfg-{task['id']}", "webhookUrl": "https://example.test/a2a"},
        )

    start = time.perf_counter()
    for task in tasks:
        task_id = str(task["id"])
        bridge.handle_synapse_frame(
            {
                "type": "chat",
                "sender": "WORKER",
                "payload": f"done {task_id}\n[A2A-TASK:{task_id}]",
            }
        )
    correlation_seconds = time.perf_counter() - start

    start = time.perf_counter()
    listed = bridge.list_tasks(page_size=task_count)
    listing_seconds = time.perf_counter() - start

    fanout_bridge = _bridge()
    fanout_task = fanout_bridge.create_working_task(_message(task_count + 1))
    fanout_task_id = str(fanout_task["id"])
    received: list[list[JsonMap]] = []
    threads = [
        threading.Thread(
            target=lambda: received.append(
                fanout_bridge.subscribe_task_events(fanout_task_id, wait_seconds=1.0) or []
            )
        )
        for _ in range(subscriber_count)
    ]
    for thread in threads:
        thread.start()
    start = time.perf_counter()
    fanout_bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": f"fanout done\n[A2A-TASK:{fanout_task_id}]",
        }
    )
    for thread in threads:
        thread.join(timeout=2.0)
    fanout_seconds = time.perf_counter() - start
    fanout_events = sum(
        1
        for events in received
        if any(event["task"]["status"]["state"] == "TASK_STATE_COMPLETED" for event in events)
    )

    return {
        "host": host_profile(),
        "tasks": task_count,
        "subscriber_count": subscriber_count,
        "task_creation": {
            "seconds": creation_seconds,
            "tasks_per_sec": _rate(task_count, creation_seconds),
        },
        "correlation": {
            "seconds": correlation_seconds,
            "tasks_per_sec": _rate(task_count, correlation_seconds),
        },
        "listing": {
            "seconds": listing_seconds,
            "tasks": listed["totalSize"],
            "tasks_per_sec": _rate(int(listed["totalSize"]), listing_seconds),
        },
        "push_delivery": {
            "deliveries": len(push_deliveries),
            "deliveries_per_sec": _rate(len(push_deliveries), correlation_seconds),
        },
        "subscriber_fanout": {
            "seconds": fanout_seconds,
            "events": fanout_events,
            "events_per_sec": _rate(fanout_events, fanout_seconds),
        },
        "correlated_replies": sum(
            1
            for task in bridge.list_tasks()["tasks"]
            if task.get("status", {}).get("state") == "TASK_STATE_COMPLETED"
        ),
    }


def run(
    results: Path | None = DEFAULT_RESULTS,
    *,
    task_count: int = DEFAULT_TASK_COUNT,
    subscriber_count: int = DEFAULT_SUBSCRIBER_COUNT,
) -> JsonMap:
    """Run the benchmark and optionally write a JSON result file."""
    summary = profile(task_count=task_count, subscriber_count=subscriber_count)
    if results is not None:
        results.parent.mkdir(parents=True, exist_ok=True)
        results.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--task-count", type=int, default=DEFAULT_TASK_COUNT)
    parser.add_argument("--subscriber-count", type=int, default=DEFAULT_SUBSCRIBER_COUNT)
    args = parser.parse_args(argv)

    summary = run(
        args.results,
        task_count=args.task_count,
        subscriber_count=args.subscriber_count,
    )
    print("A2A bridge benchmark")
    print(f"tasks: {summary['tasks']}")
    print(f"task creation tasks/s: {summary['task_creation']['tasks_per_sec']:.0f}")
    print(f"correlation tasks/s: {summary['correlation']['tasks_per_sec']:.0f}")
    print(f"push deliveries: {summary['push_delivery']['deliveries']}")
    print(f"subscriber fanout events: {summary['subscriber_fanout']['events']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
