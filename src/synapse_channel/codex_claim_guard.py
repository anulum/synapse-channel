# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — fail-closed Codex apply_patch claim guard
"""Adapt Codex ``PreToolUse`` apply-patch events to live Synapse claims."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from synapse_channel.claim_state import fetch_state_snapshot
from synapse_channel.file_claim_guard import (
    FileClaimGuardError,
    GuardVerdict,
    MutationRequest,
    StateFetcher,
    evaluate_mutation_request,
)
from synapse_channel.git.gitclaim import GitRunner, _default_git_runner

SUPPORTED_TOOL = "apply_patch"
_FILE_PREFIXES = ("*** Add File: ", "*** Update File: ", "*** Delete File: ")
_MOVE_PREFIXES = ("*** Move to: ", "*** Move from: ")


class CodexClaimGuardError(FileClaimGuardError):
    """Codex hook input is malformed or outside the guarded surface."""

    code = "codex_claim_guard"


def _required_string(data: Mapping[str, Any], key: str, *, location: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CodexClaimGuardError(f"Codex hook input needs a non-empty {location}.{key} string.")
    return value.strip()


def parse_apply_patch_paths(command: str) -> tuple[Path, ...]:
    """Extract every source and destination path from one Codex patch command."""
    lines = command.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise CodexClaimGuardError("Codex apply_patch input needs exact begin and end markers.")

    paths: list[Path] = []
    for line in lines[1:-1]:
        prefix = next(
            (item for item in (*_FILE_PREFIXES, *_MOVE_PREFIXES) if line.startswith(item)),
            None,
        )
        if prefix is not None:
            raw_path = line.removeprefix(prefix)
            if not raw_path.strip() or raw_path != raw_path.strip() or "\0" in raw_path:
                raise CodexClaimGuardError("Codex apply_patch contains an invalid file path.")
            paths.append(Path(raw_path))
        elif line.startswith("*** ") and line != "*** End of File":
            raise CodexClaimGuardError("Codex apply_patch contains an unsupported control line.")
    unique = tuple(dict.fromkeys(paths))
    if not unique:
        raise CodexClaimGuardError("Codex apply_patch contains no file mutation.")
    return unique


def parse_hook_request(raw: str) -> MutationRequest:
    """Parse and validate one Codex ``PreToolUse`` apply-patch event."""
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise CodexClaimGuardError("Codex hook input is not valid JSON.") from exc
    if not isinstance(decoded, dict):
        raise CodexClaimGuardError("Codex hook input must be a JSON object.")
    if _required_string(decoded, "hook_event_name", location="input") != "PreToolUse":
        raise CodexClaimGuardError("Codex claim guard accepts only PreToolUse events.")
    if _required_string(decoded, "tool_name", location="input") != SUPPORTED_TOOL:
        raise CodexClaimGuardError("Codex claim guard accepts only apply_patch calls.")
    tool_input = decoded.get("tool_input")
    if not isinstance(tool_input, dict):
        raise CodexClaimGuardError("Codex hook input needs a tool_input object.")
    cwd = Path(_required_string(decoded, "cwd", location="input"))
    if not cwd.is_absolute():
        raise CodexClaimGuardError("Codex hook cwd must be absolute.")
    return MutationRequest(
        session_id=_required_string(decoded, "session_id", location="input"),
        tool_use_id=_required_string(decoded, "tool_use_id", location="input"),
        cwd=cwd,
        file_paths=parse_apply_patch_paths(
            _required_string(tool_input, "command", location="tool_input")
        ),
    )


async def evaluate_hook_event(
    raw: str,
    *,
    identity: str,
    uri: str,
    token: str | None,
    timeout: float,
    state_fetcher: StateFetcher = fetch_state_snapshot,
    git_runner: GitRunner = _default_git_runner,
) -> GuardVerdict:
    """Evaluate one raw Codex event against the authoritative live claims."""
    try:
        request = parse_hook_request(raw)
    except CodexClaimGuardError as exc:
        return GuardVerdict(False, str(exc))
    return await evaluate_mutation_request(
        request,
        provider="Codex",
        identity=identity,
        uri=uri,
        token=token,
        timeout=timeout,
        state_fetcher=state_fetcher,
        git_runner=git_runner,
    )
