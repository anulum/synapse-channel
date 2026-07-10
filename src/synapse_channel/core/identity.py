# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — agent identity inventory for shadow-mode ACL evaluation
"""Agent identity records and an inventory for shadow-mode ACL evaluation.

An identity binds a stable agent id to its project namespace, the seat that
launched it, and the credential that proves it may act as that agent. This first
tranche is observe-only: it catalogs declared identities and audits them for the
ambiguities that block a future enforcement rollout (duplicate agent ids, missing
credentials, blank namespaces). It does not issue credentials, verify them, or
change how the hub admits a connection — that is the runtime-enforcement tranche.

See :doc:`../../docs/identity-and-acl` for the full identity and ACL design.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.errors import SynapseError


class IdentityError(SynapseError, ValueError):
    """Raised when an identity inventory file is malformed."""

    code = "identity"


@dataclass(frozen=True)
class Identity:
    """One agent identity record.

    Parameters
    ----------
    agent_id : str
        Stable per-agent identity used as the audit subject on claims, releases,
        messages, and receipts.
    project : str
        Project namespace prefix (for example ``SYNAPSE-CHANNEL``) that scopes the
        identity, claim paths, and channels.
    seat_id : str
        Workstation, tmux session, or service unit currently using the agent id.
    credential_id : str
        Key, certificate, or token handle that would prove the caller may act as
        the agent id. Blank means no credential is declared yet.
    """

    agent_id: str
    project: str
    seat_id: str = ""
    credential_id: str = ""

    @property
    def audit_subject(self) -> str:
        """Return the canonical ``project/agent_id`` audit subject string."""
        return f"{self.project}/{self.agent_id}"

    def as_dict(self) -> dict[str, str]:
        """Return the JSON-serialisable form of the identity."""
        return {
            "agent_id": self.agent_id,
            "project": self.project,
            "seat_id": self.seat_id,
            "credential_id": self.credential_id,
            "audit_subject": self.audit_subject,
        }


@dataclass(frozen=True)
class IdentityFinding:
    """One audit finding about an identity inventory."""

    severity: str
    subject: str
    message: str

    def as_dict(self) -> dict[str, str]:
        """Return the JSON-serialisable form of the finding."""
        return {"severity": self.severity, "subject": self.subject, "message": self.message}


class IdentityInventory:
    """A catalog of declared agent identities, keyed by audit subject."""

    def __init__(self, identities: list[Identity]) -> None:
        self._identities = list(identities)

    @classmethod
    def from_file(cls, path: str | Path) -> IdentityInventory:
        """Load an identity inventory from a JSON list of identity objects.

        Parameters
        ----------
        path : str or pathlib.Path
            JSON file holding a list of ``{agent_id, project, seat_id?,
            credential_id?}`` objects.

        Returns
        -------
        IdentityInventory
            The loaded inventory.

        Raises
        ------
        IdentityError
            When the file is missing, not JSON, not a list, or an entry lacks the
            required ``agent_id``/``project`` fields.
        """
        target = Path(path)
        try:
            raw = target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise IdentityError(f"identity file does not exist: {target}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IdentityError(f"invalid identity JSON: {exc}") from exc
        if not isinstance(data, list):
            raise IdentityError("identity file must contain a JSON list")
        identities = [cls._parse_entry(entry, index) for index, entry in enumerate(data)]
        return cls(identities)

    @staticmethod
    def _parse_entry(entry: object, index: int) -> Identity:
        """Parse one inventory entry into an :class:`Identity`."""
        if not isinstance(entry, dict):
            raise IdentityError(f"identity entry {index} must be an object")
        agent_id = str(entry.get("agent_id", "")).strip()
        project = str(entry.get("project", "")).strip()
        if not agent_id or not project:
            raise IdentityError(f"identity entry {index} needs non-empty agent_id and project")
        return Identity(
            agent_id=agent_id,
            project=project,
            seat_id=str(entry.get("seat_id", "")).strip(),
            credential_id=str(entry.get("credential_id", "")).strip(),
        )

    def identities(self) -> list[Identity]:
        """Return the inventory's identities in declaration order."""
        return list(self._identities)

    def subjects(self) -> list[str]:
        """Return the sorted audit subjects in the inventory."""
        return sorted(identity.audit_subject for identity in self._identities)

    def audit(self) -> list[IdentityFinding]:
        """Return findings that would block an enforcement rollout.

        The audit flags duplicate audit subjects (two records that would resolve
        to the same identity), identities with no declared credential, and seats
        that run more than one agent id (a borrowed-credential risk).
        """
        findings: list[IdentityFinding] = []
        subject_counts = Counter(identity.audit_subject for identity in self._identities)
        for subject, count in sorted(subject_counts.items()):
            if count > 1:
                findings.append(
                    IdentityFinding(
                        "fail", subject, f"duplicate identity: {count} records share this subject"
                    )
                )
        for identity in self._identities:
            if not identity.credential_id:
                findings.append(
                    IdentityFinding(
                        "warn",
                        identity.audit_subject,
                        "no credential declared; enforcement would deny this identity",
                    )
                )
        seat_agents: dict[str, set[str]] = {}
        for identity in self._identities:
            if identity.seat_id:
                seat_agents.setdefault(identity.seat_id, set()).add(identity.audit_subject)
        for seat, agents in sorted(seat_agents.items()):
            if len(agents) > 1:
                findings.append(
                    IdentityFinding(
                        "warn",
                        seat,
                        f"seat runs {len(agents)} agent ids; credentials must not be shared",
                    )
                )
        return findings
