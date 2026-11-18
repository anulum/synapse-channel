# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a component's own state through a real hub: declare, approve, run, verify
"""Change a component's setting through a real hub, and verify it as another identity.

The executor declares the observed setting write with ``synapse ops run --hub
--declare --expect-projection``; another identity of the project approves the
intent's digest; ``--intent-sha256`` runs the write once inside the attempt.
Then the reviewer runs ``synapse verify-result component`` under its own
grants and publishes the record, which the hub takes as an independent
``component_read``. After the setting is changed back, the same verifier says
``not_verified``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli import main
from synapse_channel.core.protocol import MessageType
from synapse_channel.effect_suite.session import HubSession
from synapse_channel.effect_suite.workspace import SuiteHub, suite_hub
from synapse_channel.operations.projection import projection_digest
from test_cli_ops_hub import DEADLINE
from test_cli_ops_hub_real import EXECUTOR, REVIEWER, TASK, _approve, _claim, _line
from test_operations_component_state import ObservedWorld, _command

STRICT = projection_digest({"mode": "strict"})


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ObservedWorld:
    return ObservedWorld(tmp_path, monkeypatch)


@pytest.fixture
async def hub(tmp_path: Path) -> AsyncIterator[SuiteHub]:
    async with suite_hub(tmp_path) as serving:
        yield serving


async def _run(world: ObservedWorld, hub: SuiteHub, epoch: int, *step: str) -> int:
    """Run the observed write through the hub as the executor, in a worker thread."""
    argv = _command(
        world,
        "--hub", hub.uri, "--task", TASK, "--epoch", str(epoch), "--name", EXECUTOR,
        "--intent-deadline", str(DEADLINE), "--expect-projection", str(world.expected), *step,
    )  # fmt: skip
    return await asyncio.to_thread(main, argv)


def _reviewer_grants(world: ObservedWorld) -> Path:
    """Write the reviewer's own owner-only grants: it may read the vendor's settings."""
    path = world.grants.with_name("reviewer-grants.json")
    grant = {
        "subject": REVIEWER,
        "permissions": ["operation-read"],
        "component": "cli",
        "expires_at": time.time() + 3600,
    }
    path.write_text(json.dumps({"grants": [grant]}), encoding="utf-8")
    path.chmod(0o600)
    return path


async def _verify(world: ObservedWorld, grants: Path) -> int:
    """Run ``verify-result component`` as the reviewer; return its exit status."""
    argv = [
        "verify-result", "component", "--observed-by", "setting.read",
        "--host", "workstation", "--vendor", "acme", "--component", "cli",
        "--component-version", "1.0.0", "--os", "linux", "--expect-sha256", STRICT,
        "--subject", REVIEWER, "--grants", str(grants), "--profile", "personal",
        "--catalogue", str(world.catalogue_file), "--journal", str(world.journal),
        "--operation-id", "op-1",
    ]  # fmt: skip
    return await asyncio.to_thread(main, argv)


async def _publish(hub: SuiteHub, record: dict[str, Any]) -> str:
    """Publish the reviewer's verification on the task and return the hub's status."""
    async with HubSession(REVIEWER, hub.uri) as session:
        request_id = f"verify-{session.mark()}"
        answer = await session.ask(
            MessageType.OPERATION_RECORD,
            lambda frame: (
                frame.get("type") == MessageType.OPERATION_RECORD_RESULT
                and frame.get("request_id") == request_id
            ),
            what="a verification answer",
            record="verification",
            task_id=TASK,
            operation_id="op-1",
            verification=record,
            request_id=request_id,
        )
    return str(answer.get("status") or "")


async def test_a_setting_changed_through_the_hub_is_verified_by_another_identity(
    world: ObservedWorld, hub: SuiteHub, capsys: pytest.CaptureFixture[str]
) -> None:
    epoch = await _claim(hub)
    assert await _run(world, hub, epoch, "--declare") == 0
    declared = _line(capsys)
    assert declared["status"] == "accepted", declared
    assert world.invocations() == ["setting show --json"]
    await _approve(hub, str(declared["intent_sha256"]))
    assert await _run(world, hub, epoch, "--intent-sha256", declared["intent_sha256"]) == 0
    report = _line(capsys)
    assert (report["state"], report["outcome_published"]["status"]) == (
        "confirmed_done",
        "accepted",
    )
    assert world.invocations()[-1] == "setting set mode strict"

    grants = _reviewer_grants(world)
    status = await _verify(world, grants)
    record = _line(capsys)
    assert (status, record["verifier"], record["result"]) == (0, "component_read", "verified")
    assert record["evidence"]["observed_sha256"] == STRICT
    assert await _publish(hub, record) == "accepted"

    world.set_mode("lax")
    status = await _verify(world, grants)
    changed_back = _line(capsys)
    assert (status, changed_back["result"]) == (1, "not_verified")


async def test_a_state_changed_after_the_approval_is_refused_before_the_attempt(
    world: ObservedWorld, hub: SuiteHub, capsys: pytest.CaptureFixture[str]
) -> None:
    epoch = await _claim(hub)
    assert await _run(world, hub, epoch, "--declare") == 0
    digest = str(_line(capsys)["intent_sha256"])
    await _approve(hub, digest)
    world.set_mode("someone-else")
    assert await _run(world, hub, epoch, "--intent-sha256", digest) == 2
    assert "the component's state or the identity changed" in capsys.readouterr().err
    assert "setting set mode strict" not in world.invocations()
