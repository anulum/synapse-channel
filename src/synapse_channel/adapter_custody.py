# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — exact-byte ownership of generic adapter targets
"""Install and remove recorded adapter bytes without taking ownership from markers.

Each target has one private adjacent custody directory. It retains a dedicated
target's original bytes and a prepared update until the filesystem result can
be reconciled. Local cooperating installers use the same advisory lock; other
writers and cross-host filesystem locking are outside this contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from synapse_channel.adapter_custody_files import (
    AdapterCustodyError,
    clear_replacement,
    replace_target,
    snapshot_value,
    validate_auxiliary,
    validate_directory,
    validate_target,
)
from synapse_channel.adapter_custody_store import CustodyStore, Ownership, PendingUpdate
from synapse_channel.adapters import APPEND_MODE, FILE_MODE, MARKER_BEGIN, MARKER_END, AdapterTool
from synapse_channel.advisory_lock import locked
from synapse_channel.opencode_adapter_files import FileSnapshot, read_text_snapshot

AdapterAction = Literal["install", "uninstall", "recover"]


def _check_markers(text: str, *, required: bool) -> None:
    counts = (text.count(MARKER_BEGIN), text.count(MARKER_END))
    if counts != ((1, 1) if required else (0, 0)):
        raise AdapterCustodyError("Adapter markers are missing or ambiguous.")
    if required and text.index(MARKER_BEGIN) >= text.index(MARKER_END):
        raise AdapterCustodyError("Adapter marker order is invalid.")


def _current_owned(snapshot: FileSnapshot, ownership: Ownership, tool: AdapterTool) -> str:
    current = snapshot_value(snapshot)
    if ownership.tool != tool.key or ownership.mode != tool.mode or current is None:
        raise AdapterCustodyError("Adapter ownership does not match the current target.")
    _check_markers(current, required=True)
    matches = (
        current == ownership.owned
        if tool.mode == FILE_MODE
        else (current.count(ownership.owned) == 1)
    )
    if not matches:
        raise AdapterCustodyError("Recorded adapter bytes changed; preserving the target.")
    return current


def _removal(
    tool: AdapterTool,
    snapshot: FileSnapshot,
    owned: Ownership | None,
) -> tuple[str | None, None]:
    """Remove only the exact owned segment or restore an adopted dedicated file."""
    before = snapshot_value(snapshot)
    if owned is None:
        if before is not None and (MARKER_BEGIN in before or MARKER_END in before):
            raise AdapterCustodyError("Unrecorded adapter markers do not grant ownership.")
        return before, None
    current = _current_owned(snapshot, owned, tool)
    after = owned.original if tool.mode == FILE_MODE else current.replace(owned.owned, "", 1)
    if tool.mode == APPEND_MODE and not after and owned.original is None:
        after = None
    return after, None


def _plan(
    tool: AdapterTool,
    snapshot: FileSnapshot,
    owned: Ownership | None,
    *,
    action: AdapterAction,
    block: str,
    adopt_existing: bool,
) -> tuple[str | None, Ownership | None]:
    before = snapshot_value(snapshot)
    if action == "uninstall":
        return _removal(tool, snapshot, owned)
    _check_markers(block, required=True)
    if owned is not None:
        current = _current_owned(snapshot, owned, tool)
        if tool.mode == FILE_MODE:
            return block, Ownership(tool.key, tool.mode, owned.original, block)
        original = owned.original
        separator = "" if not original else ("\n" if original.endswith("\n") else "\n\n")
        replacement = separator + block
        return current.replace(owned.owned, replacement, 1), Ownership(
            tool.key, tool.mode, owned.original, replacement
        )
    if tool.mode == FILE_MODE:
        if before is not None and not adopt_existing:
            raise AdapterCustodyError("Existing dedicated target requires explicit adoption.")
        return block, Ownership(tool.key, tool.mode, before, block)
    if before is not None:
        _check_markers(before, required=False)
    separator = "" if not before else ("\n" if before.endswith("\n") else "\n\n")
    segment = separator + block
    return (before or "") + segment, Ownership(tool.key, tool.mode, before, segment)


def _reconcile(store: CustodyStore, directory: Path) -> Ownership | None:
    owned, pending = store.load()
    if pending is None:
        if (directory / "replacement").exists() or (directory / "replacement").is_symlink():
            raise AdapterCustodyError("Unrecorded adapter replacement requires inspection.")
        return owned
    current = snapshot_value(read_text_snapshot(store.target))
    if current == pending.after:
        settled = pending.ownership
    elif current == pending.before:
        settled = owned
    else:
        raise AdapterCustodyError("Interrupted adapter update conflicts with current bytes.")
    clear_replacement(directory)
    store.save(settled, None)
    return settled


def _preview(
    tool: AdapterTool,
    target: Path,
    *,
    action: AdapterAction,
    block: str,
    adopt_existing: bool,
) -> str:
    """Validate a proposed operation without creating or settling filesystem state."""
    directory = target.parent / f".{target.name}.synapse-custody"
    journal = directory / "custody.sqlite3"
    owned = None
    if directory.exists() or directory.is_symlink():
        # Validate the directory without tightening permissions during preview.
        validate_directory(directory)
        with CustodyStore(journal, target, readonly=True) as store:
            owned, pending = store.load()
        if pending is not None or (directory / "replacement").exists():
            raise AdapterCustodyError("Interrupted adapter transaction requires recovery.")
    if action != "recover":
        _plan(
            tool,
            read_text_snapshot(target),
            owned,
            action=action,
            block=block,
            adopt_existing=adopt_existing,
        )
    return "would write"


def _apply(
    store: CustodyStore,
    tool: AdapterTool,
    directory: Path,
    *,
    action: AdapterAction,
    block: str,
    adopt_existing: bool,
) -> str:
    """Settle previous intent and commit the next operation under the caller's lock."""
    owned = _reconcile(store, directory)
    if action == "recover":
        return "recovered"
    snapshot = read_text_snapshot(store.target)
    before = snapshot_value(snapshot)
    after, following = _plan(
        tool, snapshot, owned, action=action, block=block, adopt_existing=adopt_existing
    )
    if after == before and following == owned:
        return "unchanged" if owned is not None else "not-installed"
    store.save(owned, PendingUpdate(before, after, following))
    if after != before:
        replace_target(store.target, after, snapshot, directory=directory)
    store.save(following, None)
    if action == "uninstall":
        return "restored" if after is not None else "removed"
    return "updated" if before is not None else "installed"


def manage_adapter(
    tool: AdapterTool,
    *,
    root: Path,
    action: AdapterAction,
    block: str = "",
    adopt_existing: bool = False,
    dry_run: bool = False,
) -> str:
    """Apply or reconcile one exact-byte adapter transaction.

    Parameters
    ----------
    tool : AdapterTool
        Catalogue entry, including its dedicated or append ownership shape.
    root : pathlib.Path
        Explicit home, project or Kimi configuration root.
    action : {'install', 'uninstall', 'recover'}
        Requested local operation. Recovery only settles already observed bytes.
    block : str, optional
        Complete rendered adapter, including skill frontmatter where applicable.
    adopt_existing : bool, optional
        Permit a dedicated file's initial replacement after preserving its bytes.
    dry_run : bool, optional
        Inspect admission without creating parents, locks, journals or targets.

    Returns
    -------
    str
        Installed, updated, unchanged, removed, restored, not-installed or recovered.

    Raises
    ------
    AdapterCustodyError, OSError
        Ownership, target safety, concurrency or prepared recovery is uncertain.
        Existing data and pending recovery evidence are retained on refusal.
    """
    if action not in {"install", "uninstall", "recover"} or tool.mode not in {
        FILE_MODE,
        APPEND_MODE,
    }:
        raise AdapterCustodyError("Unsupported adapter operation or ownership mode.")
    target = validate_target(root, tool.target, create=False)
    directory = target.parent / f".{target.name}.synapse-custody"
    snapshot = read_text_snapshot(target)
    journal = directory / "custody.sqlite3"
    if dry_run:
        return _preview(tool, target, action=action, block=block, adopt_existing=adopt_existing)
    if not directory.exists() and not directory.is_symlink() and action == "install":
        _plan(tool, snapshot, None, action=action, block=block, adopt_existing=adopt_existing)
    if action != "install" and not directory.exists() and not directory.is_symlink():
        _plan(tool, snapshot, None, action="uninstall", block="", adopt_existing=False)
        return "not-installed"
    validate_target(root, tool.target, create=True)
    lock_path = directory / "lock"
    if lock_path.exists() or lock_path.is_symlink():
        validate_auxiliary(lock_path)
    with locked(lock_path, blocking=False, purpose="adapter custody") as acquired:
        if not acquired:
            raise AdapterCustodyError("Another installer owns this adapter transaction.")
        validate_auxiliary(lock_path)
        with CustodyStore(journal, target) as store:
            return _apply(
                store, tool, directory, action=action, block=block, adopt_existing=adopt_existing
            )
