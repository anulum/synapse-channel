# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — manual release uncertainty and durable recovery runtime journeys
"""Drive actual CLI processes and clients over isolated sockets and SQLite journals."""

from __future__ import annotations

import asyncio
import json
import shlex
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from cli_e2e_helpers import CliResult, git_repo, git_run, run_cli
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.core.acl import CLAIM, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.release_confirmation import ReleaseIntent


class LostReleaseReplyHub(SynapseHub):
    """Deliberately lose or hold only the broadcast after the real release commit."""

    def __init__(self, *, journal: EventStore | None, hold: bool = False) -> None:
        super().__init__(journal=journal, require_fencing_epoch=True)
        self.hold = hold
        self.committed = asyncio.Event()
        self.resume = asyncio.Event()

    async def broadcast(self, data: dict[str, Any]) -> frozenset[str]:
        """Keep production mutation/persistence, injecting only response loss."""
        if data.get("type") == MessageType.RELEASE_GRANTED:
            self.committed.set()
            if self.hold:
                await self.resume.wait()
            return frozenset()
        return await super().broadcast(data)


class LostReleaseConfirmationHub(LostReleaseReplyHub):
    """Lose the first post-commit read as well as the actual release grant."""

    def __init__(self, *, journal: EventStore) -> None:
        super().__init__(journal=journal)
        self.confirmation_lost = False

    async def _route(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> None:
        if (
            msg_type == MessageType.STATE_REQUEST
            and "release_confirmation" in data
            and self.committed.is_set()
            and not self.confirmation_lost
        ):
            self.confirmation_lost = True
            return
        await super()._route(sender, msg_type, data, websocket)


class PrecommitReleaseReplyHub(LostReleaseReplyHub):
    """Hold a real accepted release before routing it to the mutation actor."""

    async def _route(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> None:
        if msg_type == MessageType.RELEASE:
            await self.resume.wait()
        await super()._route(sender, msg_type, data, websocket)


class LegacyReleaseReplyHub(SynapseHub):
    """Exercise a real legacy wire profile without optional reply/query fields."""

    async def broadcast(self, data: dict[str, Any]) -> frozenset[str]:
        if data.get("type") == MessageType.RELEASE_GRANTED:
            data = {
                key: value
                for key, value in data.items()
                if key not in {"release_operation_id", "request_digest"}
            }
        return await super().broadcast(data)

    async def _route(
        self, sender: str, msg_type: str, data: dict[str, Any], websocket: Any
    ) -> None:
        if msg_type == MessageType.STATE_REQUEST:
            data = {key: value for key, value in data.items() if key != "release_confirmation"}
        await super()._route(sender, msg_type, data, websocket)


class UnrelatedErrorReleaseReplyHub(LostReleaseReplyHub):
    """Emit an unrelated asynchronous error after commit, losing only the release reply."""

    async def broadcast(self, data: dict[str, Any]) -> frozenset[str]:
        if data.get("type") == MessageType.RELEASE_GRANTED:
            await super().broadcast(
                self.system(
                    "unrelated asynchronous error",
                    msg_type=MessageType.ERROR,
                    target=data["owner"],
                )
            )
        return await super().broadcast(data)


async def command(repo: Path, uri: str, *args: str) -> CliResult:
    """Invoke the actual candidate CLI with a bounded subprocess lifetime."""
    return await asyncio.to_thread(run_cli, *args, uri=uri, cwd=repo, timeout=8)


async def claim(repo: Path, uri: str) -> None:
    """Obtain a real scoped claim whose on-disk fence the next process loads."""
    result = await command(
        repo,
        uri,
        "git-claim",
        "release-proof",
        "--name",
        "release-owner",
        "--paths",
        "README.md",
        "--base",
        "HEAD",
        "--auto-release-on",
        "manual",
        "--reply-timeout",
        "1",
    )
    assert result.ok(), result.output


def recovery_arguments(result: CliResult) -> list[str]:
    """Read the exact operator recovery command, including its original digest."""
    line = next(
        line for line in result.stdout.splitlines() if line.startswith("Read-only recovery:")
    )
    values = shlex.split(line.removeprefix("Read-only recovery: "))[1:]
    index = next(index for index, value in enumerate(values) if value.startswith("--uri="))
    del values[index]
    values.insert(values.index("--"), "--reply-timeout=0.2")
    return values


@pytest.mark.asyncio
async def test_lost_reply_recovers_exact_atomic_receipt_without_releasing_again(
    tmp_path: Path,
) -> None:
    """Read-only recovery proves the actual committed operation, not claim absence."""
    repo = git_repo(tmp_path / "repository")
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(LostReleaseReplyHub(journal=journal)) as (hub, uri):
            await claim(repo, uri)
            result = await command(
                repo,
                uri,
                "release",
                "release-proof",
                "--name",
                "release-owner",
                "--reply-timeout",
                "0.2",
                "--receipt-json",
                "--idem-key",
                "unique-release",
                "--evidence",
                "real CLI proof",
            )
            assert result.ok(), result.output
            receipt = json.loads(result.stdout)
            assert receipt["evidence"] == ["real CLI proof"]
            assert receipt["owner"] == "release-owner"
            assert "release-proof" not in hub.state.claims
            rows = tuple(journal.iter_events())
            assert sum(row.kind == "release" for row in rows) == 1
            assert sum(row.kind == "ledger_progress" for row in rows) == 1
            assert sum(row.kind == "idempotency" for row in rows) == 1


@pytest.mark.asyncio
async def test_unrelated_error_does_not_deny_the_committed_release(tmp_path: Path) -> None:
    """The actual CLI ignores an uncorrelated error and confirms its exact durable receipt."""
    repo = git_repo(tmp_path / "repository")
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(UnrelatedErrorReleaseReplyHub(journal=journal)) as (hub, uri):
            await claim(repo, uri)
            result = await command(
                repo,
                uri,
                "release",
                "release-proof",
                "--name=release-owner",
                "--reply-timeout=0.1",
                "--receipt-json",
            )
            assert result.ok(), result.output
            assert json.loads(result.stdout)["released"] is True
            assert "release-proof" not in hub.state.claims
            assert sum(row.kind == "release" for row in journal.iter_events()) == 1


@pytest.mark.asyncio
async def test_python_sdk_prepares_and_confirms_its_exact_wire_release(tmp_path: Path) -> None:
    """The real Python SDK retains its intent before sending and reads the same durable receipt."""
    repo = git_repo(tmp_path / "sdk-repository")
    raw_task = "\x85\ufeffsdk-release-é-😀\x85"
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal, require_fencing_epoch=True)) as (
            _hub,
            uri,
        ):
            agent = await connect_agent("python-sdk-release-owner", uri)
            try:
                await agent.agent.claim(raw_task, worktree=repo.as_posix(), paths=["README.md"])
                await agent.recorder.wait_for(
                    lambda data: data.get("type") == MessageType.CLAIM_GRANTED
                )
                request = agent.agent.prepare_release(
                    raw_task, idem_key="python-sdk-key", evidence=["actual Python SDK journey"]
                )
                intent = ReleaseIntent.from_request(request)
                assert intent.task_id == "\ufeffsdk-release-é-😀"
                await agent.agent.release(
                    intent.task_id,
                    epoch=request["epoch"],
                    idem_key=intent.operation_id,
                    evidence=["actual Python SDK journey"],
                )
                grant = await agent.recorder.wait_for(
                    lambda data: data.get("type") == MessageType.RELEASE_GRANTED
                )
                assert intent.matching_receipt(grant) == grant["receipt"]
                before = tuple(journal.iter_events())
                await agent.agent.request_release_confirmation(
                    raw_task, intent.operation_id, intent.request_digest, "python-sdk-query"
                )
                proof = await agent.recorder.wait_for(
                    lambda data: data.get("request_id") == "python-sdk-query"
                )
                assert proof["target"] == intent.owner
                assert proof["release_confirmation"]["status"] == "confirmed"
                assert proof["release_confirmation"]["receipt"] == grant["receipt"]
                assert tuple(journal.iter_events()) == before
            finally:
                await close_agents(agent)


@pytest.mark.asyncio
async def test_per_message_auth_refusal_is_correlated_for_keyed_and_legacy_requests(
    tmp_path: Path,
) -> None:
    """Real keyed/legacy authentication refusals and malformed JSON retain their own contracts."""
    repo = git_repo(tmp_path / "repository")
    async with running_hub(SynapseHub(require_per_message_auth=True)) as (_hub, uri):
        result = await command(
            repo, uri, "release", "release-proof", "--name=release-owner", "--reply-timeout=0.1"
        )
        assert result.returncode == 1, result.output
        assert "per-message authentication failed" in result.stdout
        agent = await connect_agent("legacy-release-owner", uri)
        try:
            await agent.agent.send_message(
                MessageType.RELEASE, task_id="release-proof", freshness_seconds=float("nan")
            )
            reply = await agent.recorder.wait_for(
                lambda data: data.get("type") == MessageType.ERROR
            )
            assert reply["target"] == "all"
            assert "release_operation_id" not in reply
            await agent.agent.send_message(MessageType.RELEASE, task_id="release-proof")
            reply = await agent.recorder.wait_for(
                lambda data: (
                    data.get("type") == MessageType.ERROR
                    and data.get("target") == "legacy-release-owner"
                )
            )
            assert reply["target"] == "legacy-release-owner"
            assert "release_operation_id" not in reply
            assert "authentication failed" in reply["payload"]
        finally:
            await close_agents(agent)


@pytest.mark.asyncio
async def test_held_reply_unknown_then_confirm_after_restart_with_no_replay(tmp_path: Path) -> None:
    """A held real response gives exit 3; a fresh hub confirms the original transaction."""
    repo = git_repo(tmp_path / "repository")
    db = tmp_path / "hub.db"
    with EventStore(db) as journal:
        async with running_hub(LostReleaseReplyHub(journal=journal, hold=True)) as (hub, uri):
            assert isinstance(hub, LostReleaseReplyHub)
            await claim(repo, uri)
            try:
                result = await command(
                    repo,
                    uri,
                    "release",
                    "release-proof",
                    "--name",
                    "release-owner",
                    "--reply-timeout",
                    "0.15",
                    "--idem-key",
                    "held-release",
                    "--evidence",
                    "committed before broadcast",
                )
                assert result.returncode == 3, result.output
                assert "outcome unknown" in result.stdout
                assert "refused" not in result.stdout
                assert hub.committed.is_set()
                assert "release-proof" not in hub.state.claims
                rows = tuple(journal.iter_events())
                assert sum(row.kind == "release" for row in rows) == 1
                assert sum(row.kind == "ledger_progress" for row in rows) == 1
                arguments = recovery_arguments(result)
            finally:
                # Release the injected barrier before running_hub shutdown joins handlers.
                hub.resume.set()
    with EventStore(db) as journal:
        async with running_hub(SynapseHub(journal=journal, require_fencing_epoch=True)) as (
            hub,
            uri,
        ):
            assert "release-proof" not in hub.state.claims
            before = tuple(journal.iter_events())
            json_arguments = list(arguments)
            json_arguments.insert(json_arguments.index("--"), "--receipt-json")
            resumed = await command(repo, uri, *json_arguments)
            assert resumed.ok(), resumed.output
            assert json.loads(resumed.stdout)["released"] is True
            for flag, value in (
                ("--name", "foreign-owner"),
                ("--idem-key", "foreign-key"),
                ("--request-digest", "0" * 64),
            ):
                altered = list(arguments)
                index = next(
                    i for i, argument in enumerate(altered) if argument.startswith(flag + "=")
                )
                altered[index] = flag + "=" + value
                refused_proof = await command(repo, uri, *altered)
                assert refused_proof.returncode == 3, refused_proof.output
                assert "durable confirmation" not in refused_proof.stdout
            await claim(repo, uri)
            new_epoch = hub.state.claims["release-proof"].epoch
            historical = await command(repo, uri, *arguments)
            assert historical.ok(), historical.output
            assert hub.state.claims["release-proof"].epoch == new_epoch
            conflict = await command(
                repo,
                uri,
                "release",
                "release-proof",
                "--name",
                "release-owner",
                "--reply-timeout",
                "0.2",
                "--idem-key",
                "held-release",
            )
            assert conflict.returncode == 1, conflict.output
            assert "different request" in conflict.stdout
            assert hub.state.claims["release-proof"].epoch == new_epoch
            assert sum(row.kind == "release" for row in journal.iter_events()) == 1
            assert tuple(journal.iter_events())[: len(before)] == before


@pytest.mark.asyncio
async def test_no_journal_does_not_use_memory_cache_or_absence_as_confirmation(
    tmp_path: Path,
) -> None:
    """An applied but unjournalled release stays unknown when its live reply is lost."""
    repo = git_repo(tmp_path / "repository")
    async with running_hub(LostReleaseReplyHub(journal=None)) as (hub, uri):
        await claim(repo, uri)
        result = await command(
            repo,
            uri,
            "release",
            "release-proof",
            "--name",
            "release-owner",
            "--reply-timeout",
            "0.15",
        )
        assert result.returncode == 3, result.output
        assert "release-proof" not in hub.state.claims
        confirm = await command(repo, uri, *recovery_arguments(result))
        assert confirm.returncode == 3, confirm.output


@pytest.mark.asyncio
async def test_precommit_timeout_can_commit_later_and_never_authorizes_a_retry(
    tmp_path: Path,
) -> None:
    """A reply deadline does not cancel an accepted server mutation or prove refusal."""
    repo = git_repo(tmp_path / "repository")
    with EventStore(tmp_path / "hub.db") as journal:
        candidate = PrecommitReleaseReplyHub(journal=journal)
        async with running_hub(candidate) as (hub, uri):
            await claim(repo, uri)
            try:
                result = await command(
                    repo,
                    uri,
                    "release",
                    "release-proof",
                    "--name",
                    "release-owner",
                    "--reply-timeout",
                    "0.1",
                    "--idem-key",
                    "precommit-release",
                )
                assert result.returncode == 3, result.output
                assert "release-proof" in hub.state.claims
                assert not any(row.kind == "release" for row in journal.iter_events())
                arguments = recovery_arguments(result)
            finally:
                candidate.resume.set()
            await asyncio.wait_for(candidate.committed.wait(), 2)
            deadline = asyncio.get_running_loop().time() + 2
            while "release-owner" in hub.online_agents():
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            recovered = await command(repo, uri, *arguments)
            assert recovered.ok(), recovered.output
            assert sum(row.kind == "release" for row in journal.iter_events()) == 1


@pytest.mark.asyncio
async def test_legacy_snapshot_is_not_an_exact_confirmation(tmp_path: Path) -> None:
    """A legacy live response refuses a fresh mutation without losing the claim."""
    repo = git_repo(tmp_path / "repository")
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(LegacyReleaseReplyHub(journal=journal)) as (hub, uri):
            await claim(repo, uri)
            result = await command(
                repo,
                uri,
                "release",
                "release-proof",
                "--name",
                "release-owner",
                "--reply-timeout",
                "0.1",
                "--idem-key",
                "legacy-release",
            )
            assert result.returncode == 1, result.output
            assert "no release sent" in result.stdout
            assert "release-proof" in hub.state.claims
            assert journal.get_operation("release-owner\0release\0legacy-release") is None
            assert not any(row.kind == "release" for row in journal.iter_events())


@pytest.mark.asyncio
async def test_existing_live_identity_is_refused_before_release(tmp_path: Path) -> None:
    """Admission refusal remains exit 1 and leaves the actual claim intact."""
    repo = git_repo(tmp_path / "repository")
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal)) as (hub, uri):
            await claim(repo, uri)
            holder = await connect_agent("release-owner", uri)
            try:
                result = await command(
                    repo,
                    uri,
                    "release",
                    "release-proof",
                    "--name",
                    "release-owner",
                    "--reply-timeout",
                    "0.2",
                )
                assert result.returncode == 1, result.output
                assert "4009" in result.stdout
                assert hub.state.claims["release-proof"].owner == "release-owner"
                assert not any(row.kind == "release" for row in journal.iter_events())
            finally:
                await close_agents(holder)


@pytest.mark.asyncio
async def test_recovery_command_preserves_option_like_task_and_key(tmp_path: Path) -> None:
    """The printed command remains executable for task ids and keys beginning with dashes."""
    repo = git_repo(tmp_path / "repository")
    task = "--release proof"
    key = "--operation with apostrophe's"
    async with running_hub(LostReleaseReplyHub(journal=None)) as (_hub, uri):
        owned = await command(
            repo,
            uri,
            "git-claim",
            "--name=release-owner",
            "--paths",
            "README.md",
            "--base",
            "HEAD",
            "--auto-release-on",
            "manual",
            "--",
            task,
        )
        assert owned.ok(), owned.output
        result = await command(
            repo,
            uri,
            "release",
            "--name=release-owner",
            "--reply-timeout=0.1",
            "--idem-key=" + key,
            "--",
            task,
        )
        assert result.returncode == 3, result.output
        arguments = recovery_arguments(result)
        assert arguments[-1] == task
        assert "--idem-key=" + key in arguments
        recovered = await command(repo, uri, *arguments)
        assert recovered.returncode == 3, recovered.output
        assert "invalid release" not in recovered.stdout


@pytest.mark.asyncio
async def test_nonblocking_git_hook_does_not_claim_success_from_a_refused_send(
    tmp_path: Path,
) -> None:
    """The actual hook retains its exit contract while labelling only an attempted release."""
    repo = git_repo(tmp_path / "repository")
    policy = AclPolicy([AclRule(CLAIM, "claim", "*"), AclRule(CLAIM, "path", "*")])
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(
            SynapseHub(journal=journal, acl_policy=policy, require_acl=True)
        ) as (hub, uri):
            owned = await command(
                repo,
                uri,
                "git-claim",
                "release-proof",
                "--name=release-owner",
                "--paths",
                "README.md",
                "--base",
                "HEAD",
                "--auto-release-on",
                "commit",
            )
            assert owned.ok(), owned.output
            (repo / "README.md").write_text("committed change\n", encoding="utf-8")
            git_run(repo, "add", "README.md")
            git_run(repo, "commit", "-q", "-m", "owned change")
            result = await command(
                repo, uri, "git-release", "--trigger", "commit", "--name=release-owner"
            )
            assert result.ok(), result.output
            assert "release requested on commit: release-proof" in result.stdout
            assert "released on" not in result.stdout
            assert hub.state.claims["release-proof"].owner == "release-owner"
            assert not any(row.kind == "release" for row in journal.iter_events())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "response_json",
        "response_sha256",
        "request_digest",
        "first_event_seq",
        "commit_seq",
        "release_witness",
        "commit_witness",
        "commit_response",
        "commit_first_seq",
        "commit_seq_field",
        "nonfinite_response",
        "missing_operations_table",
    ],
)
async def test_corrupt_durable_proof_never_confirms_or_leaks_storage_details(
    tmp_path: Path,
    damage: str,
) -> None:
    """Real SQL corruption invalidates the exact proof and keeps reads value-free."""
    repo = git_repo(tmp_path / "repository")
    db = tmp_path / "hub.db"
    with EventStore(db) as journal:
        async with running_hub(LostReleaseConfirmationHub(journal=journal)) as (hub, uri):
            assert isinstance(hub, LostReleaseConfirmationHub)
            await claim(repo, uri)
            try:
                result = await command(
                    repo,
                    uri,
                    "release",
                    "release-proof",
                    "--name",
                    "release-owner",
                    "--reply-timeout",
                    "1",
                    "--idem-key",
                    "corrupt-release",
                )
                assert result.returncode == 3, result.output
                assert hub.committed.is_set() and hub.confirmation_lost
                stored = journal.get_operation("release-owner\0release\0corrupt-release")
                assert stored is not None
                assert stored.request_digest is not None
                arguments = [
                    "release",
                    "release-proof",
                    "--name",
                    "release-owner",
                    "--confirm-only",
                    "--idem-key",
                    "corrupt-release",
                    "--request-digest",
                    stored.request_digest,
                    "--reply-timeout",
                    "0.2",
                ]
                recovery = recovery_arguments(result)
                recovery[recovery.index("--reply-timeout=0.2")] = "--reply-timeout=1"
                assert f"--request-digest={stored.request_digest}" in recovery
                recovered = await command(repo, uri, *recovery)
                assert recovered.ok(), recovered.output
                assert "historical operation; no mutation replay" in recovered.stdout
                assert sum(row.kind == "release" for row in journal.iter_events()) == 1
                with sqlite3.connect(db) as conn:
                    if damage == "missing_operations_table":
                        conn.execute("DROP TABLE operations")
                    elif damage in {"release_witness", "commit_witness"}:
                        seq = (
                            stored.first_event_seq
                            if damage == "release_witness"
                            else stored.commit_seq
                        )
                        conn.execute("UPDATE events SET payload=? WHERE seq=?", ("{}", seq))
                    elif damage in {"commit_response", "commit_first_seq", "commit_seq_field"}:
                        witness = json.loads(
                            conn.execute(
                                "SELECT payload FROM events WHERE seq=?",
                                (stored.commit_seq,),
                            ).fetchone()[0]
                        )
                        field, value = {
                            "commit_response": ("response", {}),
                            "commit_first_seq": ("first_event_seq", True),
                            "commit_seq_field": ("commit_seq", 999999),
                        }[damage]
                        witness[field] = value
                        conn.execute(
                            "UPDATE events SET payload=? WHERE seq=?",
                            (json.dumps(witness), stored.commit_seq),
                        )
                    elif damage == "nonfinite_response":
                        conn.execute("UPDATE operations SET response_json=?", ('{"x":NaN}',))
                    else:
                        values: dict[str, str | int] = {
                            "response_json": "{}",
                            "response_sha256": "bad",
                            "request_digest": "0" * 64,
                            "first_event_seq": 999999,
                            "commit_seq": 999999,
                        }
                        conn.execute(f"UPDATE operations SET {damage}=?", (values[damage],))
                confirmed = await command(repo, uri, *arguments)
                assert confirmed.returncode == 3, confirmed.output
                assert "hub.db" not in confirmed.stdout
                assert "operations" not in confirmed.stdout
                assert "Traceback" not in confirmed.output
                assert "name already online" not in confirmed.output
                assert sum(row.kind == "release" for row in journal.iter_events()) == 1
            finally:
                hub.resume.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        None,
        [],
        {},
        {"task_id": 3},
        {"task_id": "release-proof", "operation_id": "", "request_digest": "0" * 64},
        {"task_id": "release-proof", "operation_id": "x" * 129, "request_digest": "0" * 64},
        {"task_id": "release-proof", "operation_id": "x\0y", "request_digest": "0" * 64},
        {"task_id": "release-proof", "operation_id": "x", "request_digest": "bad"},
    ],
)
async def test_private_invalid_query_returns_unknown_without_full_snapshot(
    tmp_path: Path,
    query: Any,
) -> None:
    """Malformed wire requests receive a private safe projection, never unrelated state."""
    with EventStore(tmp_path / "hub.db") as journal:
        async with running_hub(SynapseHub(journal=journal)) as (_hub, uri):
            agent = await connect_agent("query-owner", uri)
            try:
                before = tuple(journal.iter_events())
                await agent.agent.send_message(
                    MessageType.STATE_REQUEST,
                    target="System",
                    release_confirmation=query,
                    request_id="exact-query",
                )
                reply = await agent.recorder.wait_for(
                    lambda data: data.get("request_id") == "exact-query"
                )
                assert reply["target"] == "query-owner"
                assert reply["release_confirmation"]["status"] == "unknown"
                assert "snapshot" not in reply
                assert tuple(journal.iter_events()) == before
            finally:
                await close_agents(agent)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--reply-timeout=nan"],
        ["--reply-timeout=inf"],
        ["--reply-timeout=0"],
        ["--ready-timeout=301"],
        ["--idem-key", ""],
        ["--request-digest", "0" * 64],
        ["--confirm-only"],
        ["--confirm-only", "--idem-key", "known", "--request-digest", "bad"],
        [
            "--confirm-only",
            "--idem-key",
            "known",
            "--request-digest",
            "0" * 64,
            "--evidence",
            "new",
        ],
    ],
)
def test_invalid_recovery_arguments_fail_before_connection(
    tmp_path: Path, arguments: list[str]
) -> None:
    """The real parser/dispatch refuses invalid bounded recovery or mutation combinations."""
    result = run_cli(
        "release",
        "release-proof",
        "--name",
        "release-owner",
        *arguments,
        uri="ws://127.0.0.1:1",
        cwd=tmp_path,
        timeout=5,
    )
    assert result.returncode == 1, result.output
    assert "invalid release" in result.stdout
    assert "Could not reach" not in result.stdout
