# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an observation cannot approve a different subject
"""Prove a stored command observation is not an approval.

The seat declares the SHA-256 of a command, its exit code and the hashes of
the outputs it kept, then records that it observed that object. Approving a
different subject that only cites the observation must be refused by the
existing verdict gate. A review verdict on that subject itself still follows
the existing approval path. No new command is added.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from cli_e2e_helpers import isolated_hub, run_cli

_NAMESPACE = "LAB"
_COMMAND = "python -m pytest tests/test_cli_e2e_durable_handoff_restart.py"
_REFUSAL = "approval gate: no verdict recorded"


def _sha256(payload: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of ``payload``."""
    return hashlib.sha256(payload).hexdigest()


def _until(predicate: Any, *args: str, timeout: float = 8.0) -> dict[str, Any]:
    """Poll one JSON command until ``predicate`` accepts its object."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        result = run_cli(*args, timeout=20)
        if result.ok():
            loaded = json.loads(result.stdout)
            if isinstance(loaded, dict):
                last = loaded
                if predicate(loaded):
                    return loaded
        time.sleep(0.05)
    message = "timed out waiting for the hub journal"
    raise AssertionError(message if last is None else last)


def test_observation_cannot_approve_a_different_subject(tmp_path: Path) -> None:
    """Citing a command observation does not approve another SHA-256 subject."""
    output = b"observed check passed\n"
    output_hash = _sha256(output)
    observation = json.dumps(
        {"command": _COMMAND, "exit_code": 0, "output_sha256": [output_hash]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    evaluation = _sha256(observation)
    change = _sha256(b"the subject under review\n")
    assert evaluation != change

    with isolated_hub(tmp_path) as hub:
        database = hub.db_path
        writer = ("--name", "LAB/writer", "--task", "EVAL", "--uri", hub.uri)
        reviewer = ("--name", "LAB/reviewer", "--task", "EVAL", "--uri", hub.uri)

        def declare(digest: str) -> None:
            declared = run_cli(
                "verdict",
                "evidence",
                "--sha256",
                digest,
                "--namespace",
                _NAMESPACE,
                *writer,
                timeout=20,
            )
            assert declared.ok(), declared.output

        declare(output_hash)
        declare(evaluation)
        declare(change)
        observed = run_cli(
            "verdict",
            "record",
            "--namespace",
            _NAMESPACE,
            "--subject",
            evaluation,
            "--outcome",
            "accepted",
            "--input",
            output_hash,
            "--author",
            "LAB/writer",
            "--sequence",
            "1",
            *reviewer,
            timeout=20,
        )
        assert observed.ok(), observed.output

        cited = run_cli(
            "approval",
            "decide",
            "--approve",
            "--subject",
            change,
            "--reason",
            f"cites {evaluation}",
            "--name",
            "LAB/approver",
            "--uri",
            hub.uri,
            timeout=20,
        )
        assert cited.returncode == 0, cited.output
        report = _until(
            lambda body: any(
                row.get("kind") == "approval" and row.get("task_id") == change
                for row in body.get("refused_records", [])
            ),
            "verdict",
            "status",
            str(database),
            "--json",
        )
        refusal = next(
            row
            for row in report["refused_records"]
            if row.get("kind") == "approval" and row.get("task_id") == change
        )
        assert refusal["reason"] == _REFUSAL
        assert refusal["author"] == "LAB/approver"
        assert report["subjects"][f"{_NAMESPACE}:{evaluation}"]["passed"] is True
        assert f"{_NAMESPACE}:{change}" not in report["subjects"]
        before = _until(
            lambda body: True,
            "approval",
            "status",
            str(database),
            "--json",
        )
        assert all(row["subject"] != change for row in before["statuses"])

        reviewed = run_cli(
            "verdict",
            "record",
            "--namespace",
            _NAMESPACE,
            "--subject",
            change,
            "--outcome",
            "accepted",
            "--input",
            evaluation,
            "--author",
            "LAB/writer",
            "--sequence",
            "1",
            *reviewer,
            timeout=20,
        )
        assert reviewed.ok(), reviewed.output
        _until(
            lambda body: body["subjects"][f"{_NAMESPACE}:{change}"]["passed"] is True,
            "verdict",
            "status",
            str(database),
            "--json",
        )
        decided = run_cli(
            "approval",
            "decide",
            "--approve",
            "--subject",
            change,
            "--reason",
            "review of this subject",
            "--name",
            "LAB/approver",
            "--uri",
            hub.uri,
            timeout=20,
        )
        assert decided.returncode == 0, decided.output
        approved = _until(
            lambda body: any(
                row["subject"] == change and row["current_state"] == "approved"
                for row in body["statuses"]
            ),
            "approval",
            "status",
            str(database),
            "--json",
        )
    matching = [row for row in approved["statuses"] if row["subject"] == change]
    assert len(matching) == 1
    assert matching[0]["decision_reason"] == "review of this subject"
    assert matching[0]["decided_by"] == "LAB/approver"
