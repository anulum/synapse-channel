# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journeys for the experimental surface.

These are the newer, advisory-by-design commands: lease TTL advice and memory
recall over the event log; semantic task routing and resource bids over the hub
(explicitly advisory — they never claim, assign, or reserve); and the declarative
workflow DSL and WASM sandbox manifest validators. Each is driven exactly as a
user would run it, and the advisory guardrail wording is asserted where present.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cli_e2e_helpers import isolated_hub, run_cli


def _declare(uri: str) -> None:
    """Declare a single board task for the routing/bidding journeys."""
    run_cli("task", "declare", "BUILD", "--title", "build step", uri=uri)


# --- event-log advisories ----------------------------------------------------


def test_ttl_advice_reads_the_log(tmp_path: Path) -> None:
    """``ttl-advice`` derives an advisory default TTL, preserving manual TTLs."""
    with isolated_hub(tmp_path) as hub:
        _declare(hub.uri)
        run_cli("lock", "BUILD", "--paths", "src/app.py", "--", "true", uri=hub.uri)
        result = run_cli("ttl-advice", str(hub.db_path))
        assert result.ok(), result.output
        assert "advisory" in result.stdout
        assert "recommended_default_seconds" in result.stdout


def test_memory_recall_tokenises_a_query(tmp_path: Path) -> None:
    """``memory-recall <db> <query>`` recalls log events for a query."""
    with isolated_hub(tmp_path) as hub:
        _declare(hub.uri)
        result = run_cli("memory-recall", str(hub.db_path), "BUILD")
        assert result.ok(), result.output
        assert "Memory recall for: BUILD" in result.stdout


# --- advisory hub directory hints -------------------------------------------


def test_route_task_is_advisory_and_needs_a_task(tmp_path: Path) -> None:
    """``route-task <task>`` recommends agents and states it is advisory only."""
    with isolated_hub(tmp_path) as hub:
        _declare(hub.uri)
        result = run_cli("route-task", "BUILD", uri=hub.uri)
        assert result.ok(), result.output
        assert "Route recommendations for BUILD" in result.stdout
        assert "advisory only" in result.stdout.lower()


def test_resource_bids_are_advisory_directory_hints(tmp_path: Path) -> None:
    """``resource-bids <task>`` lists advisory bids that reserve nothing."""
    with isolated_hub(tmp_path) as hub:
        _declare(hub.uri)
        result = run_cli("resource-bids", "BUILD", uri=hub.uri)
        assert result.ok(), result.output
        assert "Resource bids for BUILD" in result.stdout
        assert "advisory" in result.stdout.lower()


# --- declarative validators --------------------------------------------------


def test_workflow_validate_accepts_a_dag(tmp_path: Path) -> None:
    """``workflow validate`` accepts a well-formed step DAG."""
    spec = tmp_path / "release.workflow.json"
    spec.write_text(
        json.dumps(
            {
                "name": "release",
                "steps": [
                    {"id": "build", "title": "Build", "task_class": "ci"},
                    {"id": "test", "title": "Test", "depends_on": ["build"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    result = run_cli("workflow", "validate", str(spec))
    assert result.ok(), result.output
    assert "is valid" in result.stdout
    assert "2 steps" in result.stdout


def test_sandbox_validate_requires_a_sha256_digest(tmp_path: Path) -> None:
    """``sandbox validate`` accepts a manifest and requires a ``sha256:`` digest."""
    digest = "sha256:" + hashlib.sha256(b"tool").hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tool_id": "calc",
                "content_digest": digest,
                "resources": {"memory_bytes": 1 << 20, "fuel": 100_000, "wall_clock_ms": 2_000},
            }
        ),
        encoding="utf-8",
    )
    valid = run_cli("sandbox", "validate", str(manifest))
    assert valid.ok(), valid.output

    # A bare hex digest without the algorithm prefix is rejected.
    manifest.write_text(
        json.dumps(
            {
                "tool_id": "calc",
                "content_digest": hashlib.sha256(b"tool").hexdigest(),
                "resources": {"memory_bytes": 1 << 20, "fuel": 100_000, "wall_clock_ms": 2_000},
            }
        ),
        encoding="utf-8",
    )
    rejected = run_cli("sandbox", "validate", str(manifest))
    assert rejected.returncode == 2
    assert "sha256:" in rejected.output
