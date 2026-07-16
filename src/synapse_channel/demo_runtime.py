# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — golden-demo runtime boundaries
"""Runtime helpers for the real hub, Git, guard, and receipt demo boundaries."""

from __future__ import annotations

import asyncio
import socket
import subprocess  # nosec B404
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect as ws_connect

from synapse_channel import SynapseAgent
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.release_verification import VerifiedReleaseReceipt
from synapse_channel.file_claim_guard import (
    GuardVerdict,
    MutationRequest,
    evaluate_mutation_request,
)

_SOURCE_PATH = Path("src/shared.py")
_TEST_PATH = Path("tests/test_shared.py")


class DemoInbox:
    """Record received messages and wait for predicate matches during demos."""

    def __init__(self) -> None:
        """Create an empty in-memory inbox."""
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, data: dict[str, Any]) -> None:
        """Append one received hub message to the inbox."""
        self.messages.append(data)

    async def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        start: int = 0,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Return the first new message matching ``predicate`` before timeout.

        Parameters
        ----------
        predicate : Callable[[dict[str, Any]], bool]
            Filter used to identify the expected hub message.
        start : int, optional
            First inbox index considered. Defaults to ``0``.
        timeout : float, optional
            Maximum seconds to wait. Defaults to ``5.0``.

        Returns
        -------
        dict[str, Any]
            First matching message.

        Raises
        ------
        TimeoutError
            If no matching message arrives before the deadline.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            for message in list(self.messages[start:]):
                if predicate(message):
                    return message
            await asyncio.sleep(0.01)
        raise TimeoutError("expected message did not arrive")


def _free_port() -> int:
    """Reserve and immediately release an ephemeral localhost TCP port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("localhost", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


async def _await_listening(port: int, timeout: float = 3.0) -> None:
    """Wait for one clean WebSocket handshake against the demo hub."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            connection = await ws_connect(
                f"ws://localhost:{port}", open_timeout=min(1.0, remaining)
            )
        except (OSError, TimeoutError, asyncio.TimeoutError):
            await asyncio.sleep(0.02)
            continue
        await connection.close()
        return
    raise TimeoutError(f"hub did not start listening on {port}")


def _run_git(workspace: Path, *args: str) -> str:
    """Run one bounded Git command in the disposable demo workspace."""
    result = subprocess.run(  # nosec B603 B607
        ["git", *args],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise RuntimeError(detail)
    return result.stdout.strip()


def _seed_workspace(workspace: Path) -> None:
    """Create a minimal committed Git repository for real claim enforcement."""
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / _SOURCE_PATH).write_text(
        'def coordination_status() -> str:\n    """Return the current handoff state."""\n'
        '    return "unassigned"\n',
        encoding="utf-8",
    )
    (workspace / _TEST_PATH).write_text(
        "import sys\n"
        "import unittest\n"
        "from pathlib import Path\n\n"
        'sys.path.insert(0, str(Path(__file__).parents[1] / "src"))\n'
        "from shared import coordination_status\n\n\n"
        "class CoordinationStatusTest(unittest.TestCase):\n"
        "    def test_status(self) -> None:\n"
        '        self.assertEqual(coordination_status(), "unassigned")\n\n\n'
        'if __name__ == "__main__":\n'
        "    unittest.main()\n",
        encoding="utf-8",
    )
    _run_git(workspace, "init", "-q")
    _run_git(workspace, "config", "user.name", "Synapse Demo")
    _run_git(workspace, "config", "user.email", "demo@localhost")
    _run_git(workspace, "add", "src/shared.py", "tests/test_shared.py")
    _run_git(workspace, "commit", "-qm", "seed golden demo")
    _run_git(workspace, "branch", "-M", "main")


def _request(workspace: Path, path: Path, call_id: str) -> MutationRequest:
    """Build one real provider-neutral mutation request."""
    return MutationRequest(
        session_id="synapse-golden-demo",
        tool_use_id=call_id,
        cwd=workspace.resolve(),
        file_paths=(path,),
    )


async def _guard(
    workspace: Path,
    path: Path,
    call_id: str,
    *,
    provider: str,
    identity: str,
    uri: str,
) -> GuardVerdict:
    """Evaluate one workspace mutation against the live hub claim state."""
    return await evaluate_mutation_request(
        _request(workspace, path, call_id),
        provider=provider,
        identity=identity,
        uri=uri,
        token=None,
        timeout=3.0,
    )


async def _post_story(
    agent: SynapseAgent,
    inbox: DemoInbox,
    *,
    task_id: str,
    text: str,
) -> None:
    """Post one milestone and wait until the hub records it."""
    start = len(inbox.messages)
    await agent.post_progress(task_id, text, kind="note")
    await inbox.wait_for(
        lambda message: (
            message.get("type") == MessageType.LEDGER_PROGRESS_POSTED
            and message.get("note", {}).get("text") == text
        ),
        start=start,
    )


def _receipt_fields(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Return canonical release keyword fields from an observed receipt."""
    return {
        "evidence": list(receipt.get("evidence", [])),
        "artifacts": list(receipt.get("artifacts", [])),
        "known_failures": list(receipt.get("known_failures", [])),
        "changed_files": list(receipt.get("changed_files", [])),
        "generated_artifacts": list(receipt.get("generated_artifacts", [])),
        "approvals": list(receipt.get("approvals", [])),
        "confidence": str(receipt.get("confidence", "")),
        "freshness_seconds": receipt.get("freshness_seconds"),
    }


def _validated_hub_receipt(raw: object) -> dict[str, Any]:
    """Return one supported hub receipt or raise a bounded demo failure."""
    if not isinstance(raw, dict):
        raise RuntimeError("hub did not return a release receipt")
    if raw.get("epistemic_status") != "supported":
        raise RuntimeError("hub did not accept the verified receipt as supported evidence")
    return raw


async def _release_with_receipt(
    agent: SynapseAgent,
    inbox: DemoInbox,
    task_id: str,
    receipt: VerifiedReleaseReceipt,
) -> dict[str, Any]:
    """Release one task and return the hub-confirmed receipt."""
    start = len(inbox.messages)
    await agent.release(task_id, **_receipt_fields(receipt))
    granted = await inbox.wait_for(
        lambda message: (
            message.get("type") == MessageType.RELEASE_GRANTED and message.get("task_id") == task_id
        ),
        start=start,
    )
    return _validated_hub_receipt(granted.get("receipt"))
