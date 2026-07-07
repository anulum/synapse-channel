# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deny-by-default store of which subjects may claim which roles
"""A subject-aware, deny-by-default store of which identities may claim which roles.

A role is a ``<project>/<role>`` address an agent asks to answer to on its
registration heartbeat (see :meth:`~synapse_channel.core.hub.SynapseHub.set_agent_roles`);
a directed message to that role reaches every holder. Left ungoverned, the hub
binds *any* role a heartbeat declares, so a hostile socket can bind a privileged
role it does not own — role squatting. This store is the authorisation the hub
consults to close that hole: a subject may claim a role only when an operator has
granted it, deny-by-default.

The existing ACL model (:mod:`synapse_channel.core.acl`) is namespace-scoped and
*subject-agnostic* — its rules carry no subject — so it cannot express "only
identity X may claim role Y". Role-claim needs that per-subject grain, so it has
its own store rather than overloading the ACL policy. Subject matching mirrors the
ACL's case-sensitive :func:`fnmatch.fnmatchcase`, so a grant names an exact
identity (``SYNAPSE-CHANNEL/claude-2759``) or a namespace glob
(``SYNAPSE-CHANNEL/*``); the operator chooses the breadth.

Enforcement is opt-in at the hub (``--require-role-claim``): with it off — the
default open/loopback posture — every declared role binds exactly as before, so a
single-user dev hub is unchanged. The :mod:`synapse_channel.cli_role` operator
command manages the store file this loads.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

DEFAULT_STORE_PATH = "~/.synapse/role-grants.json"
"""Conventional store location, mirroring ``synapse federation``'s ``~/.synapse`` home."""

STORE_FILE_MODE = 0o600
"""Owner-only permissions for the store: it is tamper-sensitive authorisation state."""


class RoleGrantError(ValueError):
    """Raised when a role-grant store file is malformed or a grant is invalid."""


def _require_token(value: str, label: str) -> str:
    """Return a stripped non-empty token or raise :class:`RoleGrantError`."""
    token = str(value).strip()
    if not token:
        raise RoleGrantError(f"{label} must be a non-empty string")
    return token


def _require_role(role: str) -> str:
    """Return a validated ``<project>/<role>`` address or raise :class:`RoleGrantError`.

    A role address names a project and a role separated by ``/`` with a non-empty
    half on each side, matching the roster addresses a heartbeat declares. Rejecting
    a malformed address at grant time turns an operator typo into an error instead of
    a silently unmatchable grant.
    """
    token = _require_token(role, "role")
    project, sep, name = token.partition("/")
    if not sep or not project.strip() or not name.strip():
        raise RoleGrantError(f"role must be '<project>/<role>', got {role!r}")
    return token


@dataclass(frozen=True)
class RoleGrants:
    """A deny-by-default map of role address to the subject patterns permitted to claim it.

    Parameters
    ----------
    grants : Mapping[str, frozenset[str]]
        Each ``<project>/<role>`` address mapped to the set of subject glob
        patterns allowed to claim it. An absent role, or one mapped to an empty
        set, permits no subject — claiming is deny-by-default.
    """

    grants: Mapping[str, frozenset[str]]

    def may_claim(self, subject: str, role: str) -> bool:
        """Return whether ``subject`` is permitted to claim ``role``.

        The role's grant patterns are matched case-sensitively with
        :func:`fnmatch.fnmatchcase`, so an exact identity or a namespace glob both
        work. A role with no grant, or a blank subject, is denied.
        """
        if not subject:
            return False
        patterns = self.grants.get(role)
        if not patterns:
            return False
        return any(fnmatchcase(subject, pattern) for pattern in patterns)

    def authorised_roles(self, subject: str, roles: Iterable[str]) -> tuple[str, ...]:
        """Return the subset of ``roles`` ``subject`` is permitted to claim, order preserved.

        The hub uses this to filter a heartbeat's declared roles under enforcement:
        an unauthorised role is dropped rather than dropping the socket, so a partial
        or squatted role list degrades to the authorised roles instead of a
        disconnect — the same forgiving posture the heartbeat handler already applies
        to malformed role fields.
        """
        return tuple(role for role in roles if self.may_claim(subject, role))

    def roles(self) -> tuple[str, ...]:
        """Return the granted role addresses, sorted."""
        return tuple(sorted(self.grants))

    def subjects_for(self, role: str) -> tuple[str, ...]:
        """Return the subject patterns granted for ``role``, sorted (empty if none)."""
        return tuple(sorted(self.grants.get(role, frozenset())))

    def with_grant(self, role: str, subject: str) -> RoleGrants:
        """Return a copy that also permits ``subject`` to claim ``role`` (idempotent).

        Both the role address and the subject are validated; granting a subject that
        is already permitted returns an equal store, so a repeated grant is a no-op
        rather than an error.
        """
        valid_role = _require_role(role)
        valid_subject = _require_token(subject, "subject")
        updated = dict(self.grants)
        updated[valid_role] = frozenset(updated.get(valid_role, frozenset()) | {valid_subject})
        return RoleGrants(updated)

    def without_grant(self, role: str, subject: str) -> RoleGrants:
        """Return a copy that no longer permits ``subject`` to claim ``role``.

        The role address and subject are validated the same way :meth:`with_grant`
        validates them, so a malformed argument is an error on revoke too rather than
        a silent no-op. A role left with no subjects is removed entirely, so revoking
        the last grant collapses the role back to deny-by-default. Revoking a
        well-formed grant that is not present returns an equal store (idempotent); the
        caller detects that no-op by comparing membership beforehand.
        """
        valid_role = _require_role(role)
        valid_subject = _require_token(subject, "subject")
        current = self.grants.get(valid_role)
        if not current or valid_subject not in current:
            return self
        remaining = current - {valid_subject}
        updated = dict(self.grants)
        if remaining:
            updated[valid_role] = remaining
        else:
            del updated[valid_role]
        return RoleGrants(updated)

    def to_json_obj(self) -> dict[str, Any]:
        """Return the JSON-serialisable form: ``{"grants": {role: [subjects]}}``, sorted."""
        return {
            "grants": {role: sorted(self.grants[role]) for role in sorted(self.grants)},
        }


def load_role_grants(path: str | Path) -> RoleGrants:
    """Load a role-grant store from ``path``; an absent file is an empty deny-all store.

    Parameters
    ----------
    path : str or pathlib.Path
        JSON file holding ``{"grants": {"<project>/<role>": ["<subject>", ...], ...}}``.
        ``~`` is expanded. A missing file loads an empty store (nothing is granted),
        so enforcement with no store denies every claim rather than failing to start.

    Returns
    -------
    RoleGrants
        The validated store.

    Raises
    ------
    RoleGrantError
        When the file is not JSON, is not the expected shape, or a role or subject
        entry is malformed.
    """
    file = Path(path).expanduser()
    if not file.is_file():
        return RoleGrants({})
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoleGrantError(f"role-grant store is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping) or not isinstance(data.get("grants"), Mapping):
        raise RoleGrantError("role-grant store must be a mapping with a 'grants' mapping")
    grants: dict[str, frozenset[str]] = {}
    for role, subjects in data["grants"].items():
        valid_role = _require_role(str(role))
        if not isinstance(subjects, list):
            raise RoleGrantError(f"grants for role {valid_role!r} must be a list of subjects")
        members = frozenset(_require_token(str(subject), "subject") for subject in subjects)
        # An empty subject list grants nobody, which is indistinguishable from an
        # absent role; drop it so a stored role always has at least one subject —
        # the invariant the mutators (:meth:`RoleGrants.with_grant` /
        # :meth:`~RoleGrants.without_grant`) already keep.
        if members:
            grants[valid_role] = members
    return RoleGrants(grants)


def save_role_grants(path: str | Path, grants: RoleGrants) -> None:
    """Write ``grants`` to ``path`` atomically with owner-only permissions.

    The store is authorisation state, so it is written the same tamper-resistant way
    as the mailbox cursor: a sibling temporary file is created by
    :func:`tempfile.mkstemp` (owner-only ``0o600`` by construction), flushed and
    ``fsync``-ed, then :func:`os.replace`-d over the target so a reader never sees a
    torn or world-readable file. ``~`` is expanded and parent directories are created.

    Raises
    ------
    RoleGrantError
        When the store directory cannot be created or the file cannot be written.
    """
    file = Path(path).expanduser()
    payload = json.dumps(grants.to_json_obj(), indent=2, sort_keys=True) + "\n"
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=file.parent, prefix=f"{file.name}.", suffix=".tmp")
    except OSError as exc:
        raise RoleGrantError(f"cannot write role-grant store {file}: {exc}") from exc
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, file)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
