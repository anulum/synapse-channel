# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — JetBrains ACP lifecycle cardinality tests
"""Verify exact JetBrains chat, process, and trace lifecycle cardinality."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from e2e.opencode_editors.jetbrains_lifecycle import JetBrainsLifecycleGuard

_AGENT_ID = "acp.synapse-opencode-e2e"
_AGENT_NAME = "SYNAPSE OpenCode E2E"
_CHAT_ID = "9ed4b67f-0b36-49ef-8b75-fe3336a67872"
_SECOND_CHAT_ID = "3f35175e-3f51-4544-ba86-7780fff0b51f"


def _guard(tmp_path: Path) -> JetBrainsLifecycleGuard:
    log_root = tmp_path / "log"
    log_root.mkdir()
    (log_root / "idea.log").write_text("baseline\n", encoding="utf-8")
    return JetBrainsLifecycleGuard.capture(
        log_root,
        tmp_path / "trace.jsonl",
        agent_id=_AGENT_ID,
        agent_name=_AGENT_NAME,
    )


def _append_lifecycle(
    guard: JetBrainsLifecycleGuard,
    *,
    chat_id: str = _CHAT_ID,
    process_id: str = "284153",
) -> None:
    events = (
        "2026 INFO - Creating AcpSessionLifecycleManager for agent "
        f"'{_AGENT_ID}' in chat {chat_id}\n"
        f"2026 INFO - [{_AGENT_NAME}] Process started with PID: "
        f"LocalPid(value={process_id})\n"
    )
    with guard.idea_log.open("a", encoding="utf-8") as stream:
        stream.write(events)


def test_lifecycle_guard_accepts_one_real_log_lifecycle_and_base_trace(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    _append_lifecycle(guard)
    guard.trace.write_text('{"method":"initialize"}\n', encoding="utf-8")

    observation = guard.require_exactly_one()

    assert observation.chat_ids == (_CHAT_ID,)
    assert observation.process_ids == (284153,)
    assert observation.trace_segments == (guard.trace,)


def test_lifecycle_guard_reconciles_mirrored_logs_without_double_counting(
    tmp_path: Path,
) -> None:
    guard = _guard(tmp_path)
    guard.acp_log.parent.mkdir()
    guard.acp_log.write_text("pre-existing ACP line\n", encoding="utf-8")
    guard = JetBrainsLifecycleGuard.capture(
        guard.idea_log.parent,
        guard.trace,
        agent_id=_AGENT_ID,
        agent_name=_AGENT_NAME,
    )
    _append_lifecycle(guard)
    mirrored = guard.idea_log.read_text(encoding="utf-8").removeprefix("baseline\n")
    with guard.acp_log.open("a", encoding="utf-8") as stream:
        stream.write(mirrored)
    guard.trace.touch()

    observation = guard.require_exactly_one()

    assert observation.chat_ids == (_CHAT_ID,)
    assert observation.process_ids == (284153,)


def test_lifecycle_guard_uses_the_longer_coherent_mirror(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    guard.acp_log.parent.mkdir()
    guard.acp_log.touch()
    guard = JetBrainsLifecycleGuard.capture(
        guard.idea_log.parent,
        guard.trace,
        agent_id=_AGENT_ID,
        agent_name=_AGENT_NAME,
    )
    _append_lifecycle(guard)
    first = guard.idea_log.read_text(encoding="utf-8").removeprefix("baseline\n")
    with guard.acp_log.open("a", encoding="utf-8") as stream:
        stream.write(first)
        stream.write(
            "2026 INFO - Creating AcpSessionLifecycleManager for agent "
            f"'{_AGENT_ID}' in chat {_SECOND_CHAT_ID}\n"
        )

    observation = guard.observe()

    assert observation.chat_ids == (_CHAT_ID, _SECOND_CHAT_ID)


def test_lifecycle_guard_allows_zero_or_one_during_startup(tmp_path: Path) -> None:
    guard = _guard(tmp_path)

    assert guard.assert_at_most_one().chat_ids == ()
    _append_lifecycle(guard)
    assert guard.assert_at_most_one().chat_ids == (_CHAT_ID,)


def test_lifecycle_guard_requires_no_evidence_before_confirmation(tmp_path: Path) -> None:
    """Refuse a chat lifecycle that starts while the selector is still open."""
    guard = _guard(tmp_path)
    assert guard.require_none().chat_ids == ()
    _append_lifecycle(guard)

    with pytest.raises(RuntimeError, match="before agent confirmation"):
        guard.require_none()


def test_lifecycle_guard_reads_only_post_baseline_idea_evidence(tmp_path: Path) -> None:
    """Expose readiness evidence without admitting pre-selection log entries."""
    guard = _guard(tmp_path)
    with guard.idea_log.open("a", encoding="utf-8") as stream:
        stream.write("plugins ready\n")

    assert guard.idea_contents() == "plugins ready\n"


@pytest.mark.parametrize("duplicate", ["chat", "process", "trace"])
def test_lifecycle_guard_rejects_each_duplicate_backend_surface(
    tmp_path: Path,
    duplicate: str,
) -> None:
    guard = _guard(tmp_path)
    _append_lifecycle(guard)
    guard.trace.touch()
    if duplicate == "chat":
        with guard.idea_log.open("a", encoding="utf-8") as stream:
            stream.write(
                "2026 INFO - Creating AcpSessionLifecycleManager for agent "
                f"'{_AGENT_ID}' in chat {_SECOND_CHAT_ID}\n"
            )
    elif duplicate == "process":
        with guard.idea_log.open("a", encoding="utf-8") as stream:
            stream.write(
                f"2026 INFO - [{_AGENT_NAME}] Process started with PID: LocalPid(value=284154)\n"
            )
    else:
        Path(f"{guard.trace}.1").touch()

    with pytest.raises(RuntimeError, match="multiple ACP lifecycles"):
        guard.assert_at_most_one()


@pytest.mark.parametrize(
    "present",
    [(), ("chat",), ("process",), ("trace",), ("chat", "process")],
)
def test_lifecycle_guard_requires_every_exact_component(
    tmp_path: Path,
    present: tuple[str, ...],
) -> None:
    guard = _guard(tmp_path)
    if "chat" in present:
        with guard.idea_log.open("a", encoding="utf-8") as stream:
            stream.write(
                "2026 INFO - Creating AcpSessionLifecycleManager for agent "
                f"'{_AGENT_ID}' in chat {_CHAT_ID}\n"
            )
    if "process" in present:
        with guard.idea_log.open("a", encoding="utf-8") as stream:
            stream.write(
                f"2026 INFO - [{_AGENT_NAME}] Process started with PID: LocalPid(value=284153)\n"
            )
    if "trace" in present:
        guard.trace.touch()

    with pytest.raises(RuntimeError, match="did not create exactly one"):
        guard.require_exactly_one()


def test_lifecycle_guard_rejects_a_rotated_first_trace(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    Path(f"{guard.trace}.1").touch()

    with pytest.raises(RuntimeError, match="did not start at the base path"):
        guard.assert_at_most_one()


def test_lifecycle_guard_rejects_preexisting_trace_and_empty_identity(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.touch()
    with pytest.raises(RuntimeError, match="exists before agent selection"):
        JetBrainsLifecycleGuard.capture(
            tmp_path,
            trace,
            agent_id=_AGENT_ID,
            agent_name=_AGENT_NAME,
        )
    trace.unlink()
    with pytest.raises(RuntimeError, match="identities must be non-empty"):
        JetBrainsLifecycleGuard.capture(
            tmp_path,
            trace,
            agent_id="",
            agent_name=_AGENT_NAME,
        )


@pytest.mark.parametrize(
    ("chat_id", "process_id", "message"),
    [
        ("not-a-uuid", "284153", "invalid ACP chat id"),
        (_CHAT_ID.upper(), "284153", "non-canonical ACP chat id"),
        (_CHAT_ID, "not-a-pid", "invalid ACP process id"),
        (_CHAT_ID, "0", "invalid ACP process id"),
    ],
)
def test_lifecycle_guard_rejects_malformed_identifiers(
    tmp_path: Path,
    chat_id: str,
    process_id: str,
    message: str,
) -> None:
    guard = _guard(tmp_path)
    _append_lifecycle(guard, chat_id=chat_id, process_id=process_id)

    with pytest.raises(RuntimeError, match=message):
        guard.observe()


def test_lifecycle_guard_rejects_divergent_mirrored_logs(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    guard.acp_log.parent.mkdir()
    guard.acp_log.touch()
    guard = JetBrainsLifecycleGuard.capture(
        guard.idea_log.parent,
        guard.trace,
        agent_id=_AGENT_ID,
        agent_name=_AGENT_NAME,
    )
    _append_lifecycle(guard)
    with guard.acp_log.open("a", encoding="utf-8") as stream:
        stream.write(
            "2026 INFO - Creating AcpSessionLifecycleManager for agent "
            f"'{_AGENT_ID}' in chat {_SECOND_CHAT_ID}\n"
        )

    with pytest.raises(RuntimeError, match="chat lifecycle logs disagree"):
        guard.observe()


def test_lifecycle_guard_rejects_truncated_disappeared_and_non_utf8_logs(
    tmp_path: Path,
) -> None:
    guard = _guard(tmp_path)
    guard.idea_log.unlink()
    with pytest.raises(RuntimeError, match="log disappeared"):
        guard.observe()

    guard.idea_log.write_bytes(b"baseline\n\xff")
    guard = JetBrainsLifecycleGuard(
        idea_log=guard.idea_log,
        acp_log=guard.acp_log,
        trace=guard.trace,
        idea_offset=len(b"baseline\n"),
        acp_offset=0,
        agent_id=_AGENT_ID,
        agent_name=_AGENT_NAME,
    )
    with pytest.raises(RuntimeError, match="not valid UTF-8"):
        guard.observe()

    guard.idea_log.write_text("short", encoding="utf-8")
    with pytest.raises(RuntimeError, match="log was truncated"):
        guard.observe()


def test_lifecycle_guard_rejects_unsafe_log_and_trace_files(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.touch()
    idea_log = tmp_path / "idea.log"
    idea_log.symlink_to(target)
    with pytest.raises(RuntimeError, match="unsafe JetBrains lifecycle log"):
        JetBrainsLifecycleGuard.capture(
            tmp_path,
            tmp_path / "trace.jsonl",
            agent_id=_AGENT_ID,
            agent_name=_AGENT_NAME,
        )

    idea_log.unlink()
    trace = tmp_path / "trace.jsonl"
    trace.symlink_to(target)
    with pytest.raises(RuntimeError, match="unsafe JetBrains ACP trace segment"):
        JetBrainsLifecycleGuard.capture(
            tmp_path,
            trace,
            agent_id=_AGENT_ID,
            agent_name=_AGENT_NAME,
        )


def test_lifecycle_guard_rejects_a_symlink_swapped_after_capture(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    target = tmp_path / "target-log"
    target.touch()
    guard.idea_log.unlink()
    guard.idea_log.symlink_to(target)

    with pytest.raises(RuntimeError, match="log could not be opened"):
        guard.observe()


def test_lifecycle_guard_rejects_a_log_that_changes_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = _guard(tmp_path)
    with guard.idea_log.open("a", encoding="utf-8") as stream:
        stream.write("new evidence\n")
    monkeypatch.setattr(os, "read", lambda _fd, _length: b"")

    with pytest.raises(RuntimeError, match="changed while reading"):
        guard.observe()


def test_lifecycle_guard_rejects_oversized_post_baseline_log(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    with guard.idea_log.open("ab") as stream:
        stream.truncate(guard.idea_offset + 4_194_305)

    with pytest.raises(RuntimeError, match="exceeded four MiB"):
        guard.observe()


def test_lifecycle_guard_rejects_a_negative_offset(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    invalid = JetBrainsLifecycleGuard(
        idea_log=guard.idea_log,
        acp_log=guard.acp_log,
        trace=guard.trace,
        idea_offset=-1,
        acp_offset=0,
        agent_id=_AGENT_ID,
        agent_name=_AGENT_NAME,
    )

    with pytest.raises(RuntimeError, match="offset cannot be negative"):
        invalid.observe()


def test_lifecycle_guard_rejects_an_unopenable_log(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    directory = tmp_path / "directory-log"
    directory.mkdir()
    invalid = JetBrainsLifecycleGuard(
        idea_log=directory,
        acp_log=guard.acp_log,
        trace=guard.trace,
        idea_offset=0,
        acp_offset=0,
        agent_id=_AGENT_ID,
        agent_name=_AGENT_NAME,
    )

    with pytest.raises(RuntimeError, match="unsafe JetBrains lifecycle log"):
        invalid.observe()


def test_lifecycle_guard_ignores_non_numeric_trace_companions(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    Path(f"{guard.trace}.lock").touch()

    assert guard.assert_at_most_one().trace_segments == ()
