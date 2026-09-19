# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — pi claim binding tests
"""Exercise real Git target resolution and snapshot epoch refusal."""

from __future__ import annotations

import json
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.state_models import GitContext
from synapse_channel.pi_claim_guard import (
    MAX_PI_HOOK_BYTES,
    PiGuardContext,
    _parse_event,
    claim_epoch_from_snapshot,
    evaluate_pi_hook,
)


def _repo(tmp_path: Path) -> Path:
    """Create a real local Git worktree for path-identity checks."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)  # nosec B603
    (root / "target.txt").write_text("unchanged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "target.txt"], check=True)  # nosec B603
    subprocess.run(  # nosec B603
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        check=True,
    )
    return root


def _event(root: Path, *, path: str = "target.txt", tool: str = "write") -> str:
    """Construct a native pi event without hidden tool or path inference."""
    return json.dumps(
        {
            "event": "tool_call",
            "tool_name": tool,
            "tool_call_id": "call-1",
            "session_id": "session-1",
            "cwd": str(root),
            "input": {"path": path},
        }
    )


def _claim(root: Path, *, epoch: int = 7) -> dict[str, Any]:
    """Provide the same binding fields as an authoritative active claim."""
    return {
        "task_id": "TASK-1",
        "owner": "PROJECT/seat",
        "status": "claimed",
        "worktree": str(root),
        "paths": ["target.txt"],
        "epoch": epoch,
        "git": {"branch": "main", "base": "main", "auto_release_on": "manual"},
    }


async def test_live_shape_claim_allows_only_covered_file_and_epoch(tmp_path: Path) -> None:
    """A real worktree resolves through the normal file guard after epoch binding."""
    root = _repo(tmp_path)
    context = PiGuardContext("PROJECT/seat", "PROJECT", root, "TASK-1", 7, "session-1")
    active = {"active_claims": [_claim(root)]}

    async def fetch(**_kwargs: object) -> dict[str, Any]:
        return active

    allowed = await evaluate_pi_hook(
        _event(root),
        context=context,
        uri="ws://unused",
        token=None,
        timeout=1,
        state_fetcher=fetch,
    )
    uncovered = await evaluate_pi_hook(
        _event(root, path="other.txt"),
        context=context,
        uri="ws://unused",
        token=None,
        timeout=1,
        state_fetcher=fetch,
    )
    active["active_claims"] = [_claim(root, epoch=8)]
    stale = await evaluate_pi_hook(
        _event(root),
        context=context,
        uri="ws://unused",
        token=None,
        timeout=1,
        state_fetcher=fetch,
    )
    assert allowed.allowed
    assert not uncovered.allowed and "claim required" in uncovered.reason
    assert not stale.allowed and "epoch" in stale.reason
    assert (root / "target.txt").read_text(encoding="utf-8") == "unchanged\n"


async def test_shell_and_session_mismatch_never_query_hub(tmp_path: Path) -> None:
    """Unsafe effects and mismatched sessions fail before any state request."""
    root = _repo(tmp_path)
    context = PiGuardContext("PROJECT/seat", "PROJECT", root, "TASK-1", 7, "other")

    async def unreachable(**_kwargs: object) -> dict[str, Any]:
        raise AssertionError("hub query must not happen")

    session = await evaluate_pi_hook(
        _event(root),
        context=context,
        uri="ws://unused",
        token=None,
        timeout=1,
        state_fetcher=unreachable,
    )
    shell = await evaluate_pi_hook(
        _event(root, tool="bash"),
        context=context,
        uri="ws://unused",
        token=None,
        timeout=1,
        state_fetcher=unreachable,
    )
    assert not session.allowed and "session" in session.reason
    assert not shell.allowed and "sandbox" in shell.reason


async def test_hub_outage_and_foreign_project_deny_file_write(tmp_path: Path) -> None:
    """An unavailable authority cannot be interpreted as an empty clean claim set."""
    root = _repo(tmp_path)
    context = PiGuardContext("PROJECT/seat", "PROJECT", root, "TASK-1", 7, "session-1")
    outage = await evaluate_pi_hook(
        _event(root),
        context=context,
        uri="ws://127.0.0.1:1",
        token=None,
        timeout=0.2,
    )
    foreign = await evaluate_pi_hook(
        _event(root),
        context=PiGuardContext("PROJECT/seat", "FOREIGN", root, "TASK-1", 7, "session-1"),
        uri="ws://127.0.0.1:1",
        token=None,
        timeout=0.2,
    )
    assert not outage.allowed and "unavailable" in outage.reason.lower()
    assert not foreign.allowed and "project" in foreign.reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner", "OTHER/seat"),
        ("status", "released"),
        ("worktree", "/different/repo"),
        ("epoch", 0),
        ("epoch", "7"),
    ],
)
def test_epoch_lookup_rejects_substituted_or_stale_claim(
    tmp_path: Path, field: str, value: object
) -> None:
    """No claim field can be silently replaced during launch binding."""
    claim = _claim(tmp_path)
    claim[field] = value
    epoch = claim_epoch_from_snapshot(
        {"active_claims": [claim]},
        identity="PROJECT/seat",
        project="PROJECT",
        repository=tmp_path,
        task_id="TASK-1",
    )
    assert epoch is None


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"active_claims": "not a list"},
        {"active_claims": [None]},
    ],
)
def test_epoch_lookup_refuses_malformed_snapshot(tmp_path: Path, snapshot: dict[str, Any]) -> None:
    """Missing or malformed authority cannot produce a launch epoch."""
    assert (
        claim_epoch_from_snapshot(
            snapshot,
            identity="PROJECT/seat",
            project="PROJECT",
            repository=tmp_path,
            task_id="TASK-1",
        )
        is None
    )


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("not json", "must be JSON"),
        ("[]", "tool_call event"),
        (json.dumps({"event": "other"}), "tool_call event"),
        (json.dumps({"event": "tool_call", "tool_name": "read"}), "only write or edit"),
        ("x" * (MAX_PI_HOOK_BYTES + 1), "byte limit"),
    ],
)
def test_malformed_pi_tool_event_is_refused(tmp_path: Path, raw: str, error: str) -> None:
    """The extension cannot turn a malformed native event into a file allowance."""
    context = PiGuardContext("PROJECT/seat", "PROJECT", tmp_path, "TASK-1", 7, "session-1")
    with pytest.raises(ValueError, match=error):
        _parse_event(raw, context)


def test_duplicate_exact_claim_is_not_a_unique_epoch(tmp_path: Path) -> None:
    """A duplicated task record is ambiguous even when both rows agree."""
    claim = _claim(tmp_path)
    assert (
        claim_epoch_from_snapshot(
            {"active_claims": [claim, claim]},
            identity="PROJECT/seat",
            project="PROJECT",
            repository=tmp_path,
            task_id="TASK-1",
        )
        is None
    )
    unrelated = _claim(tmp_path)
    unrelated["task_id"] = "OTHER-TASK"
    assert (
        claim_epoch_from_snapshot(
            {"active_claims": [unrelated, claim]},
            identity="PROJECT/seat",
            project="PROJECT",
            repository=tmp_path,
            task_id="TASK-1",
        )
        == 7
    )


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"project": "OTHER"}, "project"),
        ({"task_id": ""}, "task"),
        ({"session_id": ""}, "session"),
        ({"epoch": 0}, "epoch"),
        ({"repository": Path("relative")}, "repository"),
    ],
)
def test_invalid_launch_binding_is_refused(
    tmp_path: Path, change: dict[str, object], error: str
) -> None:
    """A process cannot turn partial environment fields into claim authority."""
    fields: dict[str, Any] = {
        "identity": "PROJECT/seat",
        "project": "PROJECT",
        "repository": tmp_path,
        "task_id": "TASK-1",
        "epoch": 7,
        "session_id": "session-1",
    }
    fields.update(change)
    with pytest.raises(ValueError, match=error):
        PiGuardContext(**fields).validate()


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"session_id": "other"}, "session"),
        ({"cwd": "relative"}, "cwd"),
        ({"input": []}, "exact path"),
        ({"input": {"path": " "}}, "non-empty"),
        ({"tool_call_id": ""}, "call ID"),
    ],
)
def test_invalid_file_tool_metadata_is_refused(
    tmp_path: Path, change: dict[str, object], error: str
) -> None:
    """Only native pi calls with exact path, cwd and session metadata proceed."""
    event = json.loads(_event(tmp_path))
    event.update(change)
    context = PiGuardContext("PROJECT/seat", "PROJECT", tmp_path, "TASK-1", 7, "session-1")
    with pytest.raises(ValueError, match=error):
        _parse_event(json.dumps(event), context)


@pytest.mark.parametrize("tool", ["write", "edit"])
@pytest.mark.parametrize(
    "path",
    ["~/outside.txt", "@target.txt", "file:///tmp/outside.txt", "inside\u00a0name.txt"],
)
def test_pi_rewritten_mutation_paths_are_refused(tmp_path: Path, tool: str, path: str) -> None:
    """Claim checks and Pi's eventual write/edit must resolve the same target."""
    context = PiGuardContext("PROJECT/seat", "PROJECT", tmp_path, "TASK-1", 7, "session-1")
    with pytest.raises(ValueError, match="rewritten Pi spelling"):
        _parse_event(_event(tmp_path, path=path, tool=tool), context)


def test_exact_edit_path_retains_semantic_permission(tmp_path: Path) -> None:
    """The edit tool alone can use the existing source symbol claim rule."""
    context = PiGuardContext("PROJECT/seat", "PROJECT", tmp_path, "TASK-1", 7, "session-1")
    edit = _parse_event(_event(tmp_path, tool="edit"), context)
    write = _parse_event(_event(tmp_path), context)
    assert edit.allow_semantic_source and not write.allow_semantic_source


def test_epoch_lookup_rejects_invalid_identity_or_worktree(tmp_path: Path) -> None:
    """An exact task under another project or malformed worktree has no epoch."""
    claim = _claim(tmp_path)
    assert (
        claim_epoch_from_snapshot(
            {"active_claims": [claim]},
            identity="PROJECT/seat",
            project="OTHER",
            repository=tmp_path,
            task_id="TASK-1",
        )
        is None
    )
    claim["worktree"] = 12
    assert (
        claim_epoch_from_snapshot(
            {"active_claims": [claim]},
            identity="PROJECT/seat",
            project="PROJECT",
            repository=tmp_path,
            task_id="TASK-1",
        )
        is None
    )


@pytest.mark.real_hub
async def test_expired_live_hub_claim_cannot_authorise_pi_write(tmp_path: Path) -> None:
    """The actual hub snapshot expires a lease before a fresh pi hook query."""
    root = _repo(tmp_path)
    hub = SynapseHub(hub_id="pi-expiry-test")
    context = PiGuardContext("PROJECT/seat", "PROJECT", root, "TASK-1", 0, "session-1")
    async with running_hub(hub) as (_, uri):
        granted, _ = hub.state.claim(
            context.identity,
            context.task_id,
            worktree=str(root),
            paths=["target.txt"],
            git=GitContext("main", "main", "manual"),
        )
        assert granted
        live_epoch = hub.state.claims[context.task_id].epoch
        context = PiGuardContext(
            context.identity,
            context.project,
            root,
            context.task_id,
            live_epoch,
            context.session_id,
        )
        active = await evaluate_pi_hook(
            _event(root),
            context=context,
            uri=uri,
            token=None,
            timeout=2,
        )
        assert active.allowed
        renewed, _ = hub.state.claim(
            context.identity,
            context.task_id,
            worktree=str(root),
            paths=["target.txt"],
            git=GitContext("main", "main", "manual"),
            ttl_seconds=30,
            now=time.time() - 90,
        )
        assert renewed
        expired = await evaluate_pi_hook(
            _event(root),
            context=context,
            uri=uri,
            token=None,
            timeout=2,
        )
    assert not expired.allowed
    assert (root / "target.txt").read_text(encoding="utf-8") == "unchanged\n"


def test_epoch_lookup_rejects_symlink_loop_in_root_or_claim(tmp_path: Path) -> None:
    """A path whose physical identity cannot be resolved has no claim authority."""
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    valid = _claim(tmp_path)
    invalid = _claim(tmp_path)
    invalid["worktree"] = str(loop)
    assert (
        claim_epoch_from_snapshot(
            {"active_claims": [valid]},
            identity="PROJECT/seat",
            project="PROJECT",
            repository=loop,
            task_id="TASK-1",
        )
        is None
    )
    assert (
        claim_epoch_from_snapshot(
            {"active_claims": [invalid]},
            identity="PROJECT/seat",
            project="PROJECT",
            repository=tmp_path,
            task_id="TASK-1",
        )
        is None
    )
