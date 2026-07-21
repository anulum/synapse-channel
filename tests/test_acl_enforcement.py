# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the ACL frame-to-access mapper and authoriser

from __future__ import annotations

import pytest

from synapse_channel.core.acl import (
    BOARD,
    CLAIM,
    EVIDENCE,
    MESSAGE,
    PIN_RECLAIM,
    RECALL,
    RELEASE,
    AclPolicy,
    AclRule,
    Target,
)
from synapse_channel.core.acl_enforcement import (
    GATED_MUTATIONS,
    authorise_frame,
    project_of,
    required_accesses,
)
from synapse_channel.core.protocol import MessageType


def test_project_of_extracts_namespace() -> None:
    assert project_of("SYNAPSE-CHANNEL/claude-e57b") == "SYNAPSE-CHANNEL"
    assert project_of("bare-name") == ""
    assert project_of("") == ""


def test_chat_maps_to_message_on_agent_or_channel() -> None:
    assert required_accesses(MessageType.CHAT, {"target": "bob"}) == [
        (MESSAGE, Target("agent", "bob"))
    ]
    assert required_accesses(MessageType.CHAT, {"channel": "ops"}) == [
        (MESSAGE, Target("channel", "ops"))
    ]
    assert required_accesses(MessageType.CHAT, {}) == [(MESSAGE, Target("agent", "all"))]


def test_claim_maps_task_id_and_each_path() -> None:
    # A claim needs its task-id access AND each path access — the task-id access
    # is never dropped when paths are present (the git-lease bypass fix).
    accesses = required_accesses(
        MessageType.CLAIM, {"task_id": "t", "paths": ["src/a.py", "src/b.py"]}
    )
    assert accesses == [
        (CLAIM, Target("claim", "t")),
        (CLAIM, Target("path", "src/a.py")),
        (CLAIM, Target("path", "src/b.py")),
    ]


def test_claim_without_paths_uses_task_id() -> None:
    assert required_accesses(MessageType.CLAIM, {"task_id": "T1"}) == [
        (CLAIM, Target("claim", "T1"))
    ]


def test_claim_task_id_falls_back_to_payload_like_the_handler() -> None:
    # The handler resolves task_id from payload when task_id is absent; the mapper
    # must mirror that so a payload-smuggled id can't evade a task-id rule.
    assert required_accesses(MessageType.CLAIM, {"payload": "  PROJ-1  "}) == [
        (CLAIM, Target("claim", "PROJ-1"))
    ]


def test_claim_paths_are_normalised_before_mapping() -> None:
    # `src/..` widens to the worktree root in the handler; the mapper checks the
    # normalised root scope, not the literal `src/..`.
    accesses = required_accesses(MessageType.CLAIM, {"task_id": "t", "paths": ["src/.."]})
    path_targets = [target.value for permission, target in accesses if target.kind == "path"]
    assert path_targets == [""]  # root scope, which `path:src/*` will not match


def test_resource_advertise_and_channel_verbs_are_gated() -> None:
    assert required_accesses("resource", {"name": "gpu"}) == [(BOARD, Target("resource", "gpu"))]
    assert required_accesses(MessageType.ADVERTISE, {"agent": "P/a"}) == [
        (BOARD, Target("capability", "P/a"))
    ]
    assert required_accesses(MessageType.CHANNEL_JOIN, {"channel": "secret"}) == [
        (MESSAGE, Target("channel", "secret"))
    ]
    assert required_accesses(MessageType.IDENTITY_PIN_RECLAIM, {"pin_name": "PROJ/agent"}) == [
        (PIN_RECLAIM, Target("agent", "PROJ/agent"))
    ]


def test_release_checkpoint_and_board_verbs() -> None:
    assert required_accesses(MessageType.RELEASE, {"task_id": "T1"}) == [
        (RELEASE, Target("claim", "T1"))
    ]
    assert required_accesses(MessageType.CHECKPOINT, {"task_id": "T1"}) == [
        (CLAIM, Target("claim", "T1"))
    ]
    assert required_accesses(MessageType.FINDING, {"task_id": "T1"}) == [
        (BOARD, Target("board", "T1"))
    ]
    assert required_accesses(MessageType.LEDGER_TASK, {}) == [(BOARD, Target("board", "*"))]


def test_guard_denial_maps_to_evidence_permission() -> None:
    assert required_accesses(MessageType.GUARD_DENIAL, {}) == [
        (EVIDENCE, Target("evidence", "guard-denial"))
    ]


def test_ungated_verbs_require_no_access() -> None:
    assert required_accesses(MessageType.HEARTBEAT, {}) == []
    assert required_accesses(MessageType.STATE_REQUEST, {}) == []


def test_history_and_resume_map_to_global_recall() -> None:
    # F1: the two global-history reads become ACL-gated so a deny-by-default hub
    # no longer leaks its full backlog. Both map to one RECALL access on the shared
    # history:global target, so a single grant governs history and resume together.
    expected = [(RECALL, Target("history", "global"))]
    assert required_accesses(MessageType.HISTORY_REQUEST, {"limit": 5}) == expected
    assert required_accesses(MessageType.RESUME_REQUEST, {"since": 9}) == expected


def test_recall_reads_are_not_counted_as_mutations() -> None:
    # They are gated reads, not mutations: they must stay out of GATED_MUTATIONS so
    # the mutation-completeness invariant is not diluted, yet still map to an access.
    assert MessageType.HISTORY_REQUEST not in GATED_MUTATIONS
    assert MessageType.RESUME_REQUEST not in GATED_MUTATIONS


def test_history_recall_denied_by_default_and_grantable() -> None:
    empty = AclPolicy([])
    assert (
        authorise_frame(sender="P/a", msg_type=MessageType.HISTORY_REQUEST, data={}, policy=empty)
        is not None
    )
    granted = AclPolicy([AclRule(RECALL, "history", "global", "", "recall ok")])
    assert (
        authorise_frame(
            sender="P/a", msg_type=MessageType.RESUME_REQUEST, data={"since": 0}, policy=granted
        )
        is None
    )


def test_authorise_allows_when_every_access_is_granted() -> None:
    policy = AclPolicy(
        [AclRule(CLAIM, "claim", "*", "P", "tasks"), AclRule(CLAIM, "path", "src/*", "P", "files")]
    )
    decision = authorise_frame(
        sender="P/claude",
        msg_type=MessageType.CLAIM,
        data={"task_id": "t", "paths": ["src/a.py", "src/b.py"]},
        policy=policy,
    )
    assert decision is None


def test_authorise_denies_on_the_first_disallowed_path() -> None:
    policy = AclPolicy(
        [AclRule(CLAIM, "claim", "*", "P", "tasks"), AclRule(CLAIM, "path", "src/*", "P", "files")]
    )
    decision = authorise_frame(
        sender="P/claude",
        msg_type=MessageType.CLAIM,
        data={"task_id": "t", "paths": ["src/a.py", "secrets/x"]},
        policy=policy,
    )
    assert decision is not None
    assert decision.decision == "would_deny"
    assert decision.target.value == "secrets/x"


def test_path_grant_alone_cannot_grab_a_task_lease() -> None:
    # Regression for the git-lease bypass: a file-scope (path) grant must not let
    # an agent acquire a task-id lease (e.g. the project git lease) by attaching
    # paths to the claim.
    policy = AclPolicy([AclRule(CLAIM, "path", "src/*", "P", "files only")])
    decision = authorise_frame(
        sender="P/claude",
        msg_type=MessageType.CLAIM,
        data={"task_id": "synapse-channel:git", "paths": ["src/x"]},
        policy=policy,
    )
    assert decision is not None
    assert decision.decision == "would_deny"
    assert decision.target == Target("claim", "synapse-channel:git")


def test_path_traversal_widening_is_denied_by_a_narrow_path_grant() -> None:
    # Regression for the normalisation-widening escalation: `src/..` widens to root
    # in the handler, so a narrow `path:src/*` grant must not authorise it.
    policy = AclPolicy(
        [AclRule(CLAIM, "claim", "*", "P", "tasks"), AclRule(CLAIM, "path", "src/*", "P", "files")]
    )
    decision = authorise_frame(
        sender="P/claude",
        msg_type=MessageType.CLAIM,
        data={"task_id": "t", "paths": ["src/.."]},
        policy=policy,
    )
    assert decision is not None
    assert decision.decision == "would_deny"


def test_every_gated_mutation_maps_to_an_access() -> None:
    # No mutating verb may be silently ungated: each produces at least one access.
    for msg_type in GATED_MUTATIONS:
        assert required_accesses(msg_type, {}), f"{msg_type} is an unmapped mutation"


def test_unmapped_mutation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Defence in depth: if a gated mutation ever produced no accesses, the gate
    # denies rather than silently allowing it.
    monkeypatch.setattr(
        "synapse_channel.core.acl_enforcement.required_accesses", lambda msg_type, data: []
    )
    decision = authorise_frame(
        sender="P/a", msg_type=MessageType.CLAIM, data={}, policy=AclPolicy([])
    )
    assert decision is not None
    assert decision.decision == "would_deny"
    assert "no ACL mapping" in decision.reason


def test_authorise_passes_ungated_frames() -> None:
    decision = authorise_frame(
        sender="P/claude", msg_type=MessageType.HEARTBEAT, data={}, policy=AclPolicy([])
    )
    assert decision is None


def test_authorise_denies_under_empty_policy() -> None:
    decision = authorise_frame(
        sender="P/claude",
        msg_type=MessageType.CHAT,
        data={"target": "bob"},
        policy=AclPolicy([]),
    )
    assert decision is not None
    assert decision.decision == "would_deny"
