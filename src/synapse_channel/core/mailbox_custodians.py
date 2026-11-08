# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — who may hand over or close a mailbox that nobody collects
"""The custodian map: who answers for a mailbox whose owner has gone.

A mailbox belongs to a name, and a session name rarely comes back, so mail can
wait at a name nobody will use again. A custodian may hand that mail on to a
successor or close it with a reason. The operator names custodians in a JSON
object whose keys are a name (``P/seat``), a project (``P``) or ``*``, and whose
values are the custodian names or roles (a connection holding the role acts as the
custodian)::

    {"*": "OWNER/owner", "P": "P/manager", "P/seat": "P/lead"}

One order decides: the entry for the name, else the entry for its project, else
the ``*`` entry. A role's mailbox ``role:P/R`` is looked up under that key first
and then under the role's project ``P``. With none of the three there is no
custodian, and a custodian action is refused; nothing is guessed. The usual
configuration maps ``*`` to the owner and each project to its manager, but that
is the operator's choice, not a default.

The file follows the role-grant store's rules: a missing file is an empty map, so
every custodian action is refused, and a file that is not valid JSON or has the
wrong shape is an error, which refuses hub startup.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.identity_namespace import project_of
from synapse_channel.core.role_mailbox import mailbox_role

ANY_NAME = "*"
_GLOB_CHARACTERS = frozenset("*?[]")


class MailboxCustodianError(SynapseError, ValueError):
    """Raised when a custodian map is not valid JSON or has the wrong shape."""

    code = "mailbox_custodian"


@dataclass(frozen=True)
class MailboxCustodians:
    """A validated custodian map; an empty map has no custodian for any name."""

    entries: Mapping[str, str] = field(default_factory=dict)

    def custodian_for(self, name: str) -> str | None:
        """Return the custodian of ``name`` (name, then project, then ``*``), or ``None``.

        The project of a role mailbox key ``role:P/R`` is the role's project ``P``.
        """
        subject = str(name or "").strip()
        if not subject:
            return None
        if subject in self.entries:
            return self.entries[subject]
        project = project_of(mailbox_role(subject) or subject)
        if project and project in self.entries:
            return self.entries[project]
        return self.entries.get(ANY_NAME)


def _require_name(value: object, label: str) -> str:
    """Return a stripped local name, or raise :class:`MailboxCustodianError`."""
    if not isinstance(value, str) or not value.strip():
        raise MailboxCustodianError(f"{label} must be a non-empty string")
    name = value.strip()
    if "@" in name:
        raise MailboxCustodianError(f"{label} {name!r} names another hub; only local names")
    if _GLOB_CHARACTERS.intersection(name):
        raise MailboxCustodianError(f"{label} {name!r} is a pattern; name one mailbox")
    if "/" in name:
        project, _, rest = name.partition("/")
        if not project.strip() or not rest.strip():
            raise MailboxCustodianError(f"{label} {name!r} must be '<project>/<name>'")
    return name


def _require_key(value: str) -> str:
    """Return a validated map key: ``*``, a project or a name."""
    if value.strip() == ANY_NAME:
        return ANY_NAME
    return _require_name(value, "custodian key")


def parse_mailbox_custodians(data: object) -> MailboxCustodians:
    """Validate a decoded custodian map.

    Raises
    ------
    MailboxCustodianError
        When ``data`` is not an object of names, or two keys collide once stripped.
    """
    if not isinstance(data, Mapping):
        raise MailboxCustodianError("custodian map must be a JSON object of names")
    entries: dict[str, str] = {}
    for raw_key, raw_value in data.items():
        key = _require_key(str(raw_key))
        if key in entries:
            raise MailboxCustodianError(f"custodian key {key!r} appears twice")
        entries[key] = _require_name(raw_value, f"custodian for {key!r}")
    return MailboxCustodians(entries)


def load_mailbox_custodians(path: str | Path) -> MailboxCustodians:
    """Load the custodian map from ``path``; a missing file is an empty map.

    Raises
    ------
    MailboxCustodianError
        When the file cannot be read, is not JSON, or has the wrong shape.
    """
    file = Path(path).expanduser()
    if not file.is_file():
        return MailboxCustodians({})
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MailboxCustodianError(f"custodian map is not valid JSON: {exc}") from exc
    return parse_mailbox_custodians(data)
