# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake-target registry for the tmux transport
"""The local record of one tmux wake target."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from synapse_channel.agent_tmux_types import AgentTmuxConfig


@dataclass(frozen=True)
class RegistryRecord:
    """Local registry record for one tmux-backed terminal-agent wake target."""

    identity: str
    session: str
    cwd: str
    updated_at: float = field(default_factory=time.time)
    last_start_returncode: int | None = None
    last_inject_returncode: int | None = None
    pending_wake: bool = False
    wake_prompt_staged: bool = False
    pending_since: float | None = None
    wake_launch_nonce: str | None = None


def _safe_key(identity: str) -> str:
    """Return the filesystem-safe registry key for ``identity``."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in identity)


def _project_from_identity(identity: str) -> str:
    """Return the project segment for an identity."""
    return identity.split("/", 1)[0].strip()


def _registry_dir(config: AgentTmuxConfig) -> Path:
    """Return the registry directory for ``config`` (private, never world-writable).

    Materialises the directory through
    :func:`~synapse_channel.core.private_dir.ensure_private_dir` so a pre-existing
    symlink, foreign-owned, or loose directory fails closed.
    """
    from synapse_channel.core.private_dir import ensure_private_dir

    if config.registry_dir is not None:
        return ensure_private_dir(
            config.registry_dir,
            parents=True,
            purpose="agent-tmux registry directory",
        )
    root = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if root:
        runtime = Path(root) / "synapse-agent-tmux"
    else:
        from synapse_channel.reap import private_runtime_parent

        runtime = private_runtime_parent() / "synapse-agent-tmux"
    return ensure_private_dir(
        runtime,
        parents=True,
        purpose="agent-tmux registry directory",
    )


def registry_path(config: AgentTmuxConfig) -> Path:
    """Return the registry file path for ``config``."""
    return _registry_dir(config) / f"{_safe_key(config.identity)}.json"


def _record_wake_launch(
    config: AgentTmuxConfig,
    existing: Mapping[str, object],
    pending_wake: bool | None,
    wake_prompt_staged: bool | None,
) -> str | None:
    """Bind a native paste to its launch before any text reaches the pane."""
    if not config.opencode_editor_readback:
        return None
    from synapse_channel.opencode_composer_state import current_launch_nonce, set_stage_verified

    value = existing.get("wake_launch_nonce")
    previous = value if isinstance(value, str) else None
    if pending_wake is False:
        set_stage_verified(config, False)
        return None
    if wake_prompt_staged is False:
        return None
    if wake_prompt_staged is not True:
        return previous
    nonce = current_launch_nonce(config)
    if previous != nonce or existing.get("wake_prompt_staged") is not True:
        set_stage_verified(config, False)
    return nonce


def _write_registry(
    config: AgentTmuxConfig,
    *,
    last_start_returncode: int | None = None,
    last_inject_returncode: int | None = None,
    pending_wake: bool | None = None,
    wake_prompt_staged: bool | None = None,
) -> None:
    """Write the local wake-target registry atomically, preserving prior state."""
    # Parent is already an owner-only directory via :func:`_registry_dir`.
    path = registry_path(config)
    existing: dict[str, object] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = loaded
    except (OSError, json.JSONDecodeError):
        pass
    existing_pending = existing.get("pending_wake") is True
    launch_nonce = _record_wake_launch(config, existing, pending_wake, wake_prompt_staged)
    resolved_pending = pending_wake if pending_wake is not None else existing_pending
    existing_pending_since = existing.get("pending_since")
    pending_since = (
        float(existing_pending_since)
        if resolved_pending
        and existing_pending
        and isinstance(existing_pending_since, int | float)
        and not isinstance(existing_pending_since, bool)
        else time.time()
        if resolved_pending
        else None
    )
    record = RegistryRecord(
        identity=config.identity,
        session=config.session,
        cwd=str(config.cwd),
        last_start_returncode=(
            last_start_returncode
            if last_start_returncode is not None
            else _optional_int(existing.get("last_start_returncode"))
        ),
        last_inject_returncode=(
            last_inject_returncode
            if last_inject_returncode is not None
            else _optional_int(existing.get("last_inject_returncode"))
        ),
        pending_wake=resolved_pending,
        wake_prompt_staged=(
            wake_prompt_staged
            if wake_prompt_staged is not None
            else existing.get("wake_prompt_staged") is True
        ),
        pending_since=pending_since,
        wake_launch_nonce=launch_nonce,
    )
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(record.__dict__, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def record_waiter_stop(config: AgentTmuxConfig, reason: str) -> None:
    """Record in the seat's registry entry that its pane waiter stopped, and why.

    A seat whose waiter stopped still runs but is no longer woken; the entry
    lets a restore, a supervisor or a person see that. The next full registry
    write, by a new start or a new waiter, replaces the entry.
    """
    path = registry_path(config)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        loaded = {}
    entry = loaded if isinstance(loaded, dict) else {}
    entry["waiter_stopped"] = {
        "reason": reason,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def _optional_int(value: object) -> int | None:
    """Return a stored integer without accepting booleans or loose coercion."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _pending_wake(config: AgentTmuxConfig) -> bool:
    """Return whether a consumed wake remains queued for safe pane delivery."""
    try:
        payload = json.loads(registry_path(config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("pending_wake") is True


def _pending_since(config: AgentTmuxConfig) -> float | None:
    """Return when the current queued wake first became pending."""
    try:
        payload = json.loads(registry_path(config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("pending_since") if isinstance(payload, dict) else None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _wake_prompt_staged(config: AgentTmuxConfig) -> bool:
    """Return whether the queued wake prompt is already present in the pane."""
    try:
        payload = json.loads(registry_path(config).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    staged = (
        isinstance(payload, dict)
        and payload.get("pending_wake") is True
        and payload.get("wake_prompt_staged") is True
    )
    if staged and config.opencode_editor_readback:
        from synapse_channel.opencode_composer_state import launch_has_changed

        return not launch_has_changed(config, payload.get("wake_launch_nonce"))
    return staged
