# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — representative current JSON coordination wire measurement
"""Measure the current JSON wire on fixed, non-secret coordination frames."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from pathlib import Path
from typing import Any

from synapse_channel.core.protocol import build_envelope, loads_bounded

DEFAULT_RESULTS = Path(__file__).parent / "results" / "coordination_wire_benchmark.json"


def frames() -> dict[str, dict[str, Any]]:
    """Return fixed current-wire examples without vendor text or credentials."""
    sender = "PROJECT/agent-a"
    target = "PROJECT/agent-b"
    now = 1_700_000_000.0
    return {
        "heartbeat": build_envelope(sender, "heartbeat", now=now, status="ready"),
        "claim": build_envelope(
            sender,
            "claim",
            now=now,
            task_id="TASK-1",
            worktree="/repo",
            paths=["src/example.py"],
            ttl=300,
            idem_key="claim-1",
        ),
        "directed_chat": build_envelope(
            sender,
            "chat",
            target=target,
            payload="Review the committed change and report the result.",
            now=now,
            client_msg_id="msg-1",
            receipt_requested=True,
        ),
        "mailbox_ack": build_envelope(sender, "ack", now=now, seq=42, mailbox_for=sender),
        "delivery_receipt": {
            "sender": "SynapseHub",
            "target": sender,
            "type": "delivery_receipt",
            "payload": "",
            "timestamp": now,
            "hub_id": "hub-a",
            "client_msg_id": "msg-1",
            "delivered": False,
            "reason": "no_online_recipient",
            "receipt_notification_id": "receipt-1",
        },
        "error": {
            "sender": "SynapseHub",
            "target": sender,
            "type": "error",
            "payload": "Guard evidence unavailable",
            "timestamp": now,
            "hub_id": "hub-a",
            "error_code": "guard_evidence_unavailable",
        },
    }


def measure(iterations: int) -> dict[str, Any]:
    """Check round-trip fidelity and record byte sizes and local codec latency."""
    if iterations < 1:
        raise ValueError("iterations must be positive")
    rows: dict[str, dict[str, float | int]] = {}
    for name, frame in frames().items():
        wire = json.dumps(frame, ensure_ascii=False)
        compact = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        if loads_bounded(wire) != frame:
            raise ValueError(f"JSON round trip failed for {name}")
        encode_times: list[int] = []
        decode_times: list[int] = []
        for _ in range(iterations):
            start = time.perf_counter_ns()
            encoded = json.dumps(frame, ensure_ascii=False)
            middle = time.perf_counter_ns()
            loads_bounded(encoded)
            end = time.perf_counter_ns()
            encode_times.append(middle - start)
            decode_times.append(end - middle)
        rows[name] = {
            "wire_bytes": len(wire.encode("utf-8")),
            "compact_bytes": len(compact.encode("utf-8")),
            "encode_median_us": round(statistics.median(encode_times) / 1000, 3),
            "decode_median_us": round(statistics.median(decode_times) / 1000, 3),
        }
    return {
        "method": (
            "stdlib json.dumps default spacing; production loads_bounded; synthetic fixed frames"
        ),
        "python": platform.python_version(),
        "iterations_per_frame": iterations,
        "rows": rows,
        "total_wire_bytes": sum(row["wire_bytes"] for row in rows.values()),
        "total_compact_bytes": sum(row["compact_bytes"] for row in rows.values()),
    }


def main() -> None:
    """Write a reproducible local result file for the compatibility decision."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    result = measure(args.iterations)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
