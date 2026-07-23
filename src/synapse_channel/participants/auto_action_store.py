# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable store for the armed auto-action policy
"""Persist which auto-actions are armed so the terminal and the loop share one source of truth.

The auto-action reactor (:mod:`~synapse_channel.participants.auto_action`) is armed by an
:class:`~synapse_channel.participants.auto_action.AutoActionPolicy`. Until now that policy lived
only in the process that built the
:class:`~synapse_channel.participants.auto_action.AutoActionDispatch`, so a terminal could preview
the model but never change what a live orchestration loop would arm. This module closes that gap
with a small durable store: the armed set is written to a JSON file in the coordination home, the
``synapse auto-action`` CLI reads and writes it, and an orchestration harness loads the same file to
build its dispatch — so the CLI and the loop agree on one persisted posture.

The store holds only the *policy* (which actions are armed), never the handlers: what "compact" or
"log" actually does stays operator code, injected into the dispatch the same way the loop takes its
result sink. Loading is fail-closed — a missing file arms nothing (the safe default), but a file
that exists yet does not hold a valid policy raises rather than silently arm the wrong set.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.participants.auto_action import AutoAction, AutoActionPolicy

POLICY_FILENAME = "auto_action_policy.json"
"""Default basename of the policy file within the coordination home."""

STORE_VERSION = 1
"""Schema version stamped into the file so a future format change is detected, not misread."""

_VERSION_FIELD = "version"
_ARMED_FIELD = "armed"


class AutoActionStoreError(SynapseError):
    """A policy file exists but does not hold a store this version can read.

    Raised only for a present-but-invalid file (unreadable JSON, wrong shape, unknown schema
    version, or a tag that is not an :class:`AutoAction`). A missing file is not an error — it means
    nothing is armed.
    """

    code = "auto_action_store"


def load_policy(path: Path) -> AutoActionPolicy:
    """Return the armed policy persisted at ``path``, or the empty policy when no file exists.

    Parameters
    ----------
    path : Path
        The policy file to read.

    Returns
    -------
    AutoActionPolicy
        The persisted armed set, or :class:`AutoActionPolicy` with nothing armed when the file is
        absent.

    Raises
    ------
    AutoActionStoreError
        When the file exists but is not valid JSON, is not a policy object, carries an unknown
        schema version, or names an action tag that is not an :class:`AutoAction`. Fail-closed: a
        corrupt file must be fixed, not papered over by arming the wrong set.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return AutoActionPolicy()
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AutoActionStoreError(
            f"auto-action policy file {path} is not valid JSON: {exc}"
        ) from exc
    return _policy_from_document(document, path)


def save_policy(path: Path, policy: AutoActionPolicy) -> None:
    """Write ``policy`` to ``path`` atomically with an owner-only, fsynced replace.

    The parent directory is created if absent. The armed tags are written sorted so an unchanged
    policy produces a byte-identical file (stable diffs, idempotent writes). The write goes to a
    sibling temporary file that is fsynced and then atomically renamed, so a crash mid-write leaves
    either the old file or the new one, never a truncated policy.

    Parameters
    ----------
    path : Path
        Where to write the policy file.
    policy : AutoActionPolicy
        The armed set to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        _VERSION_FIELD: STORE_VERSION,
        _ARMED_FIELD: sorted(action.value for action in policy.armed),
    }
    from synapse_channel.core.secure_path import apply_owner_only_file

    payload = json.dumps(document, indent=2, sort_keys=True).encode("utf-8")
    tmp = path.with_name(f"{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    apply_owner_only_file(path)


def _policy_from_document(document: Any, path: Path) -> AutoActionPolicy:
    """Validate a decoded JSON document into an :class:`AutoActionPolicy`, or raise."""
    if not isinstance(document, dict):
        raise AutoActionStoreError(
            f"auto-action policy file {path} must hold an object, not {type(document).__name__}"
        )
    version = document.get(_VERSION_FIELD)
    if version != STORE_VERSION:
        raise AutoActionStoreError(
            f"auto-action policy file {path} has unsupported version {version!r}; "
            f"this build reads version {STORE_VERSION}"
        )
    armed_raw = document.get(_ARMED_FIELD, [])
    if not isinstance(armed_raw, list):
        raise AutoActionStoreError(
            f"auto-action policy file {path} field '{_ARMED_FIELD}' must be a list, "
            f"not {type(armed_raw).__name__}"
        )
    by_tag = {action.value: action for action in AutoAction}
    armed: set[AutoAction] = set()
    for tag in armed_raw:
        if not isinstance(tag, str):
            raise AutoActionStoreError(
                f"auto-action policy file {path} lists a non-string action: {tag!r}"
            )
        action = by_tag.get(tag)
        if action is None:
            choices = ", ".join(sorted(by_tag))
            raise AutoActionStoreError(
                f"auto-action policy file {path} names unknown action {tag!r}; "
                f"known actions: {choices}"
            )
        armed.add(action)
    return AutoActionPolicy(armed=frozenset(armed))
