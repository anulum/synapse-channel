# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real CLI human app task journey
"""Exercise the packaged CLI with owner-only input and durable task state."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from test_app_tasks import _bundle, _ledger, _result


def test_cli_offer_to_verified_history(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    ledger = tmp_path / "ledger" / "ledger.sqlite3"
    _ledger(ledger, now)
    store = tmp_path / "queue" / "queue.sqlite3"
    bundle_file = tmp_path / "offer.json"
    result_file = tmp_path / "result.json"
    bundle_file.write_text(json.dumps(_bundle("cli-task", now)))
    result_file.write_text(json.dumps(_result("cli-task")))
    bundle_file.chmod(0o600)
    result_file.chmod(0o600)

    def run(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "synapse_channel.cli",
                "app-task",
                "--store",
                str(store),
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    for command, expected in (
        (("offer", "--file", str(bundle_file), "--ledger", str(ledger)), "offered"),
        (("accept", "cli-task"), "accepted"),
        (("start", "cli-task"), "running"),
    ):
        completed = run(*command)
        assert completed.returncode == 0, completed.stderr
        assert json.loads(completed.stdout)["state"] == expected
    result_file.write_text(json.dumps(_result("wrong-task")))
    refused = run("attach", "cli-task", "--file", str(result_file))
    assert refused.returncode == 2
    assert "exactly its task" in refused.stderr
    result_file.write_text(json.dumps(_result("cli-task")))
    assert (
        json.loads(run("attach", "cli-task", "--file", str(result_file)).stdout)["state"]
        == "result_attached"
    )
    assert json.loads(run("verify", "cli-task").stdout)["state"] == "verified"
    assert (
        json.loads(run("correct-usage", "cli-task", "--amount", "2", "--reason", "receipt").stdout)[
            "usage"
        ]["amount"]
        == "2"
    )
    history = json.loads(run("history", "cli-task").stdout)
    attach_event = next(event for event in history if event["action"] == "attach")
    assert attach_event["detail"]["actor"] == "operator:cli"
    assert "Untrusted result text" not in json.dumps(history)
