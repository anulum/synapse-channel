# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — manual release runtime journeys
"""Exercise manual release and receipt validation through the real packaged CLI."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path

import pytest

from cli_e2e_helpers import git_repo, run_cli
from hub_e2e_helpers import running_hub
from synapse_channel.core.acl import CLAIM, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore


@pytest.mark.asyncio
async def test_cleanup_acl_refusal_preserves_child_exit_and_owned_claim(tmp_path: Path) -> None:
    """A denied cleanup keeps the durable claim visible and the child's exit intact."""
    repo = git_repo(tmp_path / "repository")
    journal = EventStore(tmp_path / "hub.db")
    policy = AclPolicy([AclRule(CLAIM, "claim", "*")])
    try:
        async with running_hub(
            SynapseHub(journal=journal, acl_policy=policy, require_acl=True)
        ) as (hub, uri):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "cleanup-edit",
                "--name",
                "cleanup-owner",
                "--",
                sys.executable,
                "-c",
                "print('authorized-command-ran'); raise SystemExit(7)",
                uri=uri,
                cwd=repo,
            )
            assert result.returncode == 7, result.output
            assert result.stdout.strip() == "authorized-command-ran"
            assert hub.state.claims["cleanup-edit"].owner == "cleanup-owner"
            assert any(
                row.kind == "claim" and row.payload.get("task_id") == "cleanup-edit"
                for row in journal.iter_events()
            )
            assert not any(row.kind == "release" for row in journal.iter_events())
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["lock", "release"])
async def test_acl_denial_preserves_claim_and_reports_actual_reason(
    tmp_path: Path, operation: str
) -> None:
    """Enforced ACL refusals remain visible to the caller without mutating claims."""
    repo = git_repo(tmp_path / "repository")
    journal = EventStore(tmp_path / "hub.db")
    policy = AclPolicy(
        [AclRule(CLAIM, "claim", "*"), AclRule(CLAIM, "path", "*")]
        if operation == "release"
        else []
    )
    try:
        async with running_hub(
            SynapseHub(journal=journal, acl_policy=policy, require_acl=True)
        ) as (hub, uri):
            if operation == "release":
                claim = await asyncio.to_thread(
                    run_cli,
                    "git-claim",
                    "policy-edit",
                    "--name",
                    "policy-owner",
                    "--paths",
                    "README.md",
                    "--base",
                    "HEAD",
                    "--auto-release-on",
                    "manual",
                    uri=uri,
                    cwd=repo,
                )
                assert claim.ok(), claim.output
            arguments = (
                []
                if operation == "release"
                else ["--", sys.executable, "-c", "print('denied-command-ran')"]
            )
            result = await asyncio.to_thread(
                run_cli,
                operation,
                "policy-edit",
                "--name",
                "policy-owner",
                *arguments,
                uri=uri,
                cwd=repo,
            )
            assert result.returncode == 1, result.output
            assert "access denied:" in result.stdout, result.output
            assert "denied-command-ran" not in result.stdout
            if operation == "release":
                assert hub.state.claims["policy-edit"].owner == "policy-owner"
                assert not any(row.kind == "release" for row in journal.iter_events())
            else:
                assert "policy-edit" not in hub.state.claims
                assert not any(row.kind == "claim" for row in journal.iter_events())
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["lock", "release"])
async def test_transport_size_limit_refuses_unconfirmed_operation(
    tmp_path: Path, operation: str
) -> None:
    """A real oversized frame never confirms ownership or releases a held claim."""
    repo = git_repo(tmp_path / "repository")
    journal = EventStore(tmp_path / "hub.db")
    task = "oversized-edit" if operation == "release" else "x" * 8192
    try:
        async with running_hub(SynapseHub(journal=journal, max_msg_bytes=4096)) as (hub, uri):
            if operation == "release":
                claim = await asyncio.to_thread(
                    run_cli,
                    "git-claim",
                    task,
                    "--name",
                    "transport-owner",
                    "--paths",
                    "README.md",
                    "--base",
                    "HEAD",
                    "--auto-release-on",
                    "manual",
                    uri=uri,
                    cwd=repo,
                )
                assert claim.ok(), claim.output
            arguments = (
                ["--reply-timeout", "0.1"]
                + [
                    value
                    for index in range(10)
                    for value in ("--evidence", f"{index}:" + "x" * 498)
                ]
                if operation == "release"
                else ["--", sys.executable, "-c", "print('unconfirmed-command-ran')"]
            )
            result = await asyncio.to_thread(
                run_cli,
                operation,
                task,
                "--name",
                "transport-owner",
                *arguments,
                uri=uri,
                cwd=repo,
            )
            assert result.returncode == (3 if operation == "release" else 1), result.output
            assert "1009" in result.output, result.output
            assert "unconfirmed-command-ran" not in result.stdout
            if operation == "release":
                assert hub.state.claims[task].owner == "transport-owner"
                assert not any(row.kind == "release" for row in journal.iter_events())
            else:
                assert task not in hub.state.claims
                assert not any(row.kind == "claim" for row in journal.iter_events())
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_json", [False, True])
async def test_owner_release_is_confirmed_and_persisted(tmp_path: Path, receipt_json: bool) -> None:
    """Manual release removes the actual claim and persists its exact owner."""
    repo = git_repo(tmp_path / "repository")
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            claim = await asyncio.to_thread(
                run_cli,
                "git-claim",
                "manual-edit",
                "--name",
                "receipt-owner",
                "--paths",
                "README.md",
                "--base",
                "HEAD",
                "--auto-release-on",
                "manual",
                uri=uri,
                cwd=repo,
            )
            assert claim.ok(), claim.output
            assert hub.state.claims["manual-edit"].owner == "receipt-owner"
            result = await asyncio.to_thread(
                run_cli,
                "release",
                "manual-edit",
                "--name",
                "receipt-owner",
                *(["--receipt-json"] if receipt_json else []),
                uri=uri,
                cwd=repo,
            )
            assert result.ok(), result.output
            assert "manual-edit" not in hub.state.claims
            assert any(
                row.kind == "release" and row.payload.get("task_id") == "manual-edit"
                for row in journal.iter_events()
            )
            if receipt_json:
                receipt = json.loads(result.stdout)
                assert receipt["task_id"] == "manual-edit"
                assert receipt["owner"] == "receipt-owner"
                assert receipt["released"] is True
            else:
                assert result.stdout.strip() == "released 'manual-edit'"
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_other_owner_and_absent_claim_are_refused(tmp_path: Path) -> None:
    """A stranger cannot release another owner, and a missing task is never confirmed."""
    repo = git_repo(tmp_path / "repository")
    async with running_hub() as (hub, uri):
        claim = await asyncio.to_thread(
            run_cli,
            "git-claim",
            "owned-edit",
            "--name",
            "receipt-owner",
            "--paths",
            "README.md",
            "--base",
            "HEAD",
            "--auto-release-on",
            "manual",
            uri=uri,
            cwd=repo,
        )
        assert claim.ok(), claim.output
        for task, owner in (("owned-edit", "stranger"), ("absent-edit", "receipt-owner")):
            result = await asyncio.to_thread(
                run_cli,
                "release",
                task,
                "--name",
                owner,
                uri=uri,
                cwd=repo,
            )
            assert result.returncode == 1, result.output
            assert f"release refused for '{task}'" in result.stdout
            assert hub.state.claims["owned-edit"].owner == "receipt-owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("override_freshness", [False, True])
async def test_receipt_file_merges_cli_fields_and_persists_receipt(
    tmp_path: Path,
    override_freshness: bool,
) -> None:
    """Receipt input and explicit CLI evidence reach the durable release unchanged."""
    repo = git_repo(tmp_path / "repository")
    receipt_path = tmp_path / "receipt.json"
    payload = {
        "task_id": "receipt-edit",
        "owner": "receipt-owner",
        "evidence": ["runtime: owned claim exists"],
        "artifacts": [str(receipt_path)],
        "known_failures": [],
        "changed_files": ["README.md"],
        "generated_artifacts": [str(receipt_path)],
        "approvals": ["owner: receipt-owner"],
        "confidence": "observed",
        "freshness_seconds": 12.0,
    }
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            claim = await asyncio.to_thread(
                run_cli,
                "git-claim",
                "receipt-edit",
                "--name",
                "receipt-owner",
                "--paths",
                "README.md",
                "--base",
                "HEAD",
                "--auto-release-on",
                "manual",
                uri=uri,
                cwd=repo,
            )
            assert claim.ok(), claim.output
            assert hub.state.claims["receipt-edit"].owner == "receipt-owner"
            result = await asyncio.to_thread(
                run_cli,
                "release",
                "receipt-edit",
                "--name",
                "receipt-owner",
                "--receipt",
                str(receipt_path),
                "--receipt-json",
                "--evidence",
                "runtime: CLI release requested",
                "--artifact",
                str(tmp_path / "hub.db"),
                "--changed-file",
                "receipt.json",
                "--generated-artifact",
                "hub.db",
                "--approval",
                "owner: release requested",
                *(["--freshness-seconds", "0"] if override_freshness else []),
                uri=uri,
                cwd=repo,
            )
            assert result.ok(), result.output
            output = json.loads(result.stdout)
            assert output["evidence"] == [
                "runtime: owned claim exists",
                "runtime: CLI release requested",
            ]
            assert output["changed_files"] == ["README.md", "receipt.json"]
            assert output["generated_artifacts"] == [str(receipt_path), "hub.db"]
            assert output["approvals"] == ["owner: receipt-owner", "owner: release requested"]
            assert output["confidence"] == "observed"
            assert output["artifacts"] == [str(receipt_path), str(tmp_path / "hub.db")]
            assert output["epistemic_status"] == "unverified"
            assert output["freshness_seconds"] == (0.0 if override_freshness else 12.0)
            assert "receipt-edit" not in hub.state.claims
            rows = [row for row in journal.iter_events() if row.kind == "release"]
            assert any(row.payload.get("task_id") == "receipt-edit" for row in rows)
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                notes = [
                    row.payload
                    for row in journal.iter_events()
                    if row.kind == "ledger_progress"
                    and row.payload.get("task_id") == "receipt-edit"
                    and row.payload.get("kind") == "assessment"
                ]
                if notes:
                    break
                assert asyncio.get_running_loop().time() < deadline, (
                    "receipt assessment not persisted"
                )
                await asyncio.sleep(0.02)
            assert len(notes) == 1
            assert notes[0]["author"] == "receipt-owner"
            text = str(notes[0]["text"])
            for field in (
                "evidence",
                "artifacts",
                "changed_files",
                "generated_artifacts",
                "approvals",
            ):
                for value in output[field]:
                    assert value in text, (field, value, text)
            assert "confidence=observed" in text
            assert f"freshness_seconds={output['freshness_seconds']}" in text
            assert "epistemic_status=unverified" in text
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"task_id": "another-task"},
        {"owner": "another-owner"},
        {"evidence": "not-a-list"},
        {"artifacts": [1]},
        {"freshness_seconds": "not-a-number"},
        {"freshness_seconds": True},
        {"freshness_seconds": float("nan")},
        {"freshness_seconds": float("inf")},
        {"freshness_seconds": float("-inf")},
        {"freshness_seconds": 10**400},
    ],
)
async def test_invalid_receipt_cannot_release_live_claim(tmp_path: Path, payload: object) -> None:
    """Invalid receipt shape or identity fails before mutating the owned claim."""
    repo = git_repo(tmp_path / "repository")
    receipt_path = tmp_path / "invalid.json"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")
    async with running_hub() as (hub, uri):
        claim = await asyncio.to_thread(
            run_cli,
            "git-claim",
            "guarded-edit",
            "--name",
            "receipt-owner",
            "--paths",
            "README.md",
            "--base",
            "HEAD",
            "--auto-release-on",
            "manual",
            uri=uri,
            cwd=repo,
        )
        assert claim.ok(), claim.output
        result = await asyncio.to_thread(
            run_cli,
            "release",
            "guarded-edit",
            "--name",
            "receipt-owner",
            "--receipt",
            str(receipt_path),
            uri=uri,
            cwd=repo,
        )
        assert result.returncode == 1, result.output
        assert "invalid release receipt for 'guarded-edit'" in result.stdout
        assert hub.state.claims["guarded-edit"].owner == "receipt-owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("freshness", ["nan", "inf", "-inf"])
async def test_nonfinite_cli_freshness_cannot_release_live_claim(
    tmp_path: Path, freshness: str
) -> None:
    """Explicit CLI freshness must be finite before any release reaches the hub."""
    repo = git_repo(tmp_path / "repository")
    async with running_hub() as (hub, uri):
        claim = await asyncio.to_thread(
            run_cli,
            "git-claim",
            "guarded-edit",
            "--name",
            "receipt-owner",
            "--paths",
            "README.md",
            "--base",
            "HEAD",
            "--auto-release-on",
            "manual",
            uri=uri,
            cwd=repo,
        )
        assert claim.ok(), claim.output
        result = await asyncio.to_thread(
            run_cli,
            "release",
            "guarded-edit",
            "--name",
            "receipt-owner",
            f"--freshness-seconds={freshness}",
            uri=uri,
            cwd=repo,
        )
        assert result.returncode == 1, result.output
        assert "invalid release receipt for 'guarded-edit'" in result.stdout
        assert "must be finite" in result.stdout
        assert not result.stderr
        assert hub.state.claims["guarded-edit"].owner == "receipt-owner"


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["lock", "release"])
async def test_unreachable_hub_refuses_cli_operation(verb: str) -> None:
    """A real bound, non-listening socket cannot grant a lock or confirm a release."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        reserved.bind(("127.0.0.1", 0))
        uri = f"ws://127.0.0.1:{reserved.getsockname()[1]}"
        arguments = [verb, "offline-task", "--name", "offline-owner", "--ready-timeout", "0.2"]
        if verb == "lock":
            arguments.extend(["--", sys.executable, "-c", "print('unreachable-command-ran')"])
        result = await asyncio.to_thread(run_cli, *arguments, uri=uri)
        assert result.returncode == 1, result.output
        assert uri in result.output
        assert "unreachable-command-ran" not in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("path,accepted", [("README.md", True), ("../outside.md", False)])
async def test_git_file_lock_uses_canonical_scope_or_refuses_escape(
    tmp_path: Path,
    path: str,
    accepted: bool,
) -> None:
    """A real Git file lock cannot downgrade an escaping path to an unscoped mutex."""
    repo = git_repo(tmp_path / "repository")
    (tmp_path / "outside.md").write_text("outside\n", encoding="utf-8")
    journal = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            result = await asyncio.to_thread(
                run_cli,
                "lock",
                "file-lock",
                "--name",
                "file-owner",
                "--paths",
                path,
                "--",
                sys.executable,
                "-c",
                "print('scoped-command-ran')",
                uri=uri,
                cwd=repo,
            )
            assert result.returncode == (0 if accepted else 1), result.output
            assert ("scoped-command-ran" in result.stdout) is accepted
            assert "file-lock" not in hub.state.claims
            claims = [row.payload for row in journal.iter_events() if row.kind == "claim"]
            if accepted:
                assert len(claims) == 1
                assert claims[0]["owner"] == "file-owner"
                assert claims[0]["worktree"] == repo.resolve().as_posix()
                assert claims[0]["paths"] == ["README.md"]
                assert claims[0]["path_identity"] is not None
            else:
                assert not claims
                assert "Could not acquire lock 'file-lock'" in result.stdout
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_waiting_cli_retries_after_actual_owner_release(tmp_path: Path) -> None:
    """A denied contender waits, then runs only after the current owner releases."""
    repo = git_repo(tmp_path / "repository")
    marker = tmp_path / "command.ran"
    async with running_hub() as (hub, uri):
        claim = await asyncio.to_thread(
            run_cli,
            "git-claim",
            "queued-edit",
            "--name",
            "queue-owner",
            "--paths",
            "README.md",
            "--base",
            "HEAD",
            "--auto-release-on",
            "manual",
            uri=uri,
            cwd=repo,
        )
        assert claim.ok(), claim.output
        contender = asyncio.create_task(
            asyncio.to_thread(
                run_cli,
                "lock",
                "queued-edit",
                "--name",
                "waiting-owner",
                "--wait-timeout",
                "10",
                "--",
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
                str(marker),
                uri=uri,
                cwd=repo,
                timeout=15,
            )
        )
        try:
            deadline = asyncio.get_running_loop().time() + 5
            while hub.counters.claims_denied == 0:
                assert not contender.done(), "contender ended before the first denial"
                assert asyncio.get_running_loop().time() < deadline, "contender did not reach hub"
                await asyncio.sleep(0.02)
            assert not marker.exists()
            release = await asyncio.to_thread(
                run_cli,
                "release",
                "queued-edit",
                "--name",
                "queue-owner",
                uri=uri,
                cwd=repo,
            )
            assert release.ok(), release.output
            result = await contender
            assert result.ok(), result.output
            assert marker.exists()
            assert "queued-edit" not in hub.state.claims
        finally:
            # The finite subprocess runner owns its timeout; wait for it before closing its hub.
            if not contender.done():
                await contender
