# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deny-by-default ACL model and shadow-mode evaluator
"""A deny-by-default ACL model and a non-blocking shadow evaluator.

An ACL rule grants one permission verb on a structured target pattern inside a
project namespace, with a decision reason for receipts and postmortems. The
evaluator denies by default: an access is allowed only when a rule matches the
permission, the target kind and pattern, and the namespace. Target patterns are
structured (kind plus a glob value), never ad hoc substring checks, so a path
claim, channel id, agent id, or A2A endpoint is compared consistently.

This tranche is shadow-only: :func:`evaluate_access` returns a would-allow or
would-deny decision that callers record as evidence; it never blocks a frame.
See :doc:`../../docs/identity-and-acl` for the design.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.identity_namespace import project_of
from synapse_channel.core.protocol import GLOB_CHARACTERS

MESSAGE = "message"
CLAIM = "claim"
RELEASE = "release"
BOARD = "board"
METRICS = "metrics"
DASHBOARD = "dashboard"
A2A = "a2a"
NAMESPACE = "namespace"
SANDBOX = "sandbox"
OBSERVE = "observe"
MAILBOX = "mailbox"
OWNED_WORK = "owned-work"
ROLE_CLAIM = "role-claim"
PIN_RECLAIM = "identity-pin-reclaim"
MAILBOX_HANDOVER = "mailbox-handover"
OPERATION_EVIDENCE = "operation-evidence"
OPERATION_SEAL = "operation-seal"
OPERATION_STOP = "operation-stop"
OPERATION_RESUME = "operation-resume"
OPERATION_LEVEL = "operation-level"
OPERATION_JUDGE = "operation-judge"
IDENTITY_ENROLL = "identity-enroll"
ENTITLEMENT_ADVERTISE = "entitlement-advertise"
EVIDENCE = "evidence"
RECALL = "recall"
DELIVERY_CONTROL = "delivery-control"
ATTACHMENT_READ = "attachment-read"
ATTACHMENT_WRITE = "attachment-write"
ATTACHMENT_ADMIN = "attachment-admin"
FEED_READ = "feed-read"
OPERATOR_RELAY = "operator-relay"

PERMISSIONS = frozenset(
    {
        MESSAGE,
        CLAIM,
        RELEASE,
        BOARD,
        METRICS,
        DASHBOARD,
        A2A,
        NAMESPACE,
        SANDBOX,
        OBSERVE,
        MAILBOX,
        OWNED_WORK,
        ROLE_CLAIM,
        PIN_RECLAIM,
        MAILBOX_HANDOVER,
        OPERATION_EVIDENCE,
        OPERATION_SEAL,
        OPERATION_STOP,
        OPERATION_RESUME,
        OPERATION_LEVEL,
        OPERATION_JUDGE,
        IDENTITY_ENROLL,
        ENTITLEMENT_ADVERTISE,
        EVIDENCE,
        RECALL,
        DELIVERY_CONTROL,
        ATTACHMENT_READ,
        ATTACHMENT_WRITE,
        ATTACHMENT_ADMIN,
        FEED_READ,
        OPERATOR_RELAY,
    }
)
"""The auditable permission vocabulary an ACL rule may grant.

``SANDBOX`` grants a sandboxed tool one capability — a filesystem or network target — so
a tool's grants are evaluated through the same deny-by-default path as any other access,
not a parallel one. See :mod:`synapse_channel.core.sandbox_policy`.

``OBSERVE`` grants an identity the right to *receive* directed messages it is not a party
to — a live monitor or auditor. It is only consulted when directed-message routing is on
(``--private-directed-messages``); with routing off every socket sees all traffic and the
grant is moot. Durable consumers (the relay log, the journal, a feeds-backed dashboard,
the federation follower) read the retained feed and never need it.

``MAILBOX`` grants an identity the right to replay another identity's directed backlog
via a mailbox heartbeat (``mailbox_for``). Target kind is ``agent``; the pattern matches
the identity whose backlog is requested. Self and ``-rx`` sidecar naming still work
without a grant; this verb is the policy-file path for a trusted monitor that is neither.

``OPERATOR_RELAY`` grants a local identity the right to have this hub forward an operator
relay, such as a force release, to the peer hub that owns a namespace. Target kind is
``namespace``; the pattern matches the namespace the relay acts in. The forwarding hub lends
its own federation identity, so it checks this grant always, with or without enforcement,
and a hub without a policy forwards no relay. It is deliberately not ``release``: a broad
local release grant must not become a cross-hub one.

``FEED_READ`` grants a memory consumer the hub's forward event feed through
``history_request.feed_query`` and the right to acknowledge it with ``feed_ack``.
Target kind is ``feed``; the pattern matches the feed kind (``finding`` or
``chat_marker``) for a page and ``ack`` for an acknowledgement, so ``feed:*``
grants both. Hold it on a role, such as ``REMANENTIA/role/memory-consumer``.

``OWNED_WORK`` grants the connected identity access to full current work
assigned to itself through ``history_request.owned_work_query``. Target kind
is ``agent``. The handler still refuses another connected owner and mixed
selectors. This body-reading capability is separate from mailbox metadata.


``ROLE_CLAIM`` grants an identity the right to bind a role on its heartbeat when
``--require-role-claim`` is on. Target kind is ``role``; the pattern matches
``<project>/<role>``. Complements the role-grant store (``synapse role``): either the
store or an ACL rule may authorise a claim.

``PIN_RECLAIM`` grants a cryptographically bound operator the right to remove
one stale trust-on-first-use identity pin. Target kind is ``agent``. Unlike the
general mutation switch, this grant is always enforced by the reclaim handler,
even when ``--require-acl`` is off, because no open-hub compatibility posture
may silently make identity recovery public.

``IDENTITY_ENROLL`` grants a cryptographically bound operator the right to enrol,
rotate or revoke an identity key for a name on a hub with ``--identity-enrollments``.
Target kind is ``agent``; the pattern matches the name the key will prove. Like
``PIN_RECLAIM`` it is always enforced by its handler, and the requester also needs
the ``<project>/identity-enroller`` role grant and the name's namespace on the
hub's enrolment allow-list.

``ENTITLEMENT_ADVERTISE`` grants a cryptographically bound owner the right to record
a redacted pool advertisement (``synapse entitlements advertise``) in the hub
journal, for fleet planners. Target kind is ``pool-alias``; the pattern matches the
advertised alias. It is always enforced by its handler.

``OPERATION_EVIDENCE`` grants an identity the right to add an outcome report or
a verification record to an operation declared on a task (``operation_record``).
Target kind is ``claim``; the pattern matches the task id. The handler enforces
it even when ``--require-acl`` is off, so an open hub never keeps third-party
evidence. A declaration needs no grant: only the live claim holder may make it.

``OPERATION_SEAL`` grants an identity the right to journal a seal of an open
attempt (``operation_record`` with ``record: seal``): the report that it created
the attempt's ``sealed`` marker at the system of record, so the attempt's effect
can never land. Target kind is ``claim``; the pattern matches the task id. No
identity holds it by default; grant it to a recovery or custody role, not to
every worker. The handler enforces it even when ``--require-acl`` is off.
``operation_state``, which lists an operation's attempts, is answered to holders
of ``OPERATION_EVIDENCE`` or ``OPERATION_SEAL`` on the task, by its handler.

``OPERATION_STOP`` grants an identity the right to stop a scope of operations
(``operation_stop``): no new attempt opens and no approval is admitted in it
until a resume. Target kind is ``stop-scope``; the pattern matches the scope as
written (``installation``, ``project:<namespace>``, ``effect:<kind>`` or
``operation:<id>``). The access gate checks it when ``--require-acl`` is on, as
for every guarded request; on an open hub any authenticated identity may stop,
because stopping is a step down.

``OPERATION_RESUME`` grants an identity the right to lift a stop
(``operation_resume``). Target kind is ``stop-scope``, as for ``OPERATION_STOP``.
Grant it to person principals: until the hub records the kind of each approver,
the grant is what makes a resume a person's act. The access gate checks it when
``--require-acl`` is on, and its handler checks it also when that is off. The
handler refuses an actor of the scope: an identity that declared an operation
the stop listed or one open in the scope. A hub started without
``--acl-policy`` therefore cannot lift a stop until it is restarted with an
access file that grants this permission.

``OPERATION_LEVEL`` grants an identity the right to set a scope's autonomy level
by hand (``operation_level``). Target kind is ``level-scope``, with the scope as
written. The access gate checks it when ``--require-acl`` is on, and its handler
checks it also when that is off, so a hub without an access file sets no level.
It also lets an identity declare the criteria of a raise
(``operation_level_criteria``) and raise a level on a judgement
(``operation_level_raise``); a raise also needs a person.

``OPERATION_JUDGE`` grants an identity the right to judge a raise's criteria
(``operation_level_judgement``). Target kind is ``level-scope``. The access gate
checks it when ``--require-acl`` is on; on an open hub any identity may judge.
In either case the handler refuses an actor of the count.

``EVIDENCE`` grants an authenticated recorder the right to append a bounded
enforcement record. The shipped guard-denial target is
``evidence:guard-denial``; its handler separately requires connect-token
provenance and a durable journal.

``RECALL`` grants an identity the right to pull the hub's global chat history
and cursor-based resume backlog (``history_request`` / ``resume_request``). Target
kind is ``history``; the shipped target is ``history:global``. Consulted only when
``--require-acl`` is on: without enforcement the recall verbs stay open (a loopback
or open hub is unchanged), matching the proportionate-to-exposure posture. It is
the read counterpart to the channel-history membership gate, so a secured
deny-by-default hub no longer leaks its full history to every authenticated agent.

``DELIVERY_CONTROL`` grants a cryptographically admitted sender the right to
interrupt or steer a claimed recipient task. The version-three delivery handler
enforces this permission even when ordinary ACL enforcement is disabled.
"""

WOULD_ALLOW = "would_allow"
WOULD_DENY = "would_deny"


class AclError(SynapseError, ValueError):
    """Raised when an ACL policy file is malformed."""

    code = "acl"


@dataclass(frozen=True)
class Target:
    """A structured access target: a kind and a concrete value.

    Parameters
    ----------
    kind : str
        Target category, such as ``path``, ``channel``, ``agent``, ``endpoint``,
        ``board``, ``metrics``, ``dashboard``, or ``namespace``.
    value : str
        Concrete target value compared against a rule's glob pattern.
    glob : bool, optional
        Whether ``value`` is itself an address glob, as a message target part such
        as ``PROJ/*`` is. A rule grants a glob value only when every address the
        value can match is one the rule grants (see :meth:`AclRule.matches`).
        ``False`` by default, so a value is compared literally.
    """

    kind: str
    value: str
    glob: bool = False


@dataclass(frozen=True)
class AclRule:
    """One deny-by-default ACL grant.

    Parameters
    ----------
    permission : str
        Permission verb this rule grants (see :data:`PERMISSIONS`).
    target_kind : str
        Target kind the rule applies to.
    target_pattern : str
        Glob pattern matched case-sensitively against a target value.
    namespace : str
        Project namespace the rule is scoped to; blank matches any namespace.
    reason : str
        Human-readable decision reason recorded on a matching decision.
    """

    permission: str
    target_kind: str
    target_pattern: str
    namespace: str = ""
    reason: str = ""

    def matches(self, *, project: str, permission: str, target: Target) -> bool:
        """Return whether this rule grants ``permission`` on ``target``.

        A literal value is granted when it matches the rule's pattern. A glob value
        is granted only by containment: when it equals the pattern, or when the
        pattern is ``L*`` with a literal ``L`` that the value's literal prefix (the
        text before its first glob character) starts with. Matching a glob's text
        against the pattern would be unsound, because ``PROJ/?`` matches the text
        ``PROJ/*``, which reaches ``PROJ/longname``.
        """
        return (
            self.permission == permission
            and self.target_kind == target.kind
            and (not self.namespace or self.namespace == project)
            and _pattern_grants(self.target_pattern, target)
        )


def _pattern_grants(pattern: str, target: Target) -> bool:
    """Return whether ``pattern`` grants ``target``'s value, by containment for a glob."""
    if not target.glob:
        return fnmatchcase(target.value, pattern)
    if target.value == pattern:
        return True
    literal = pattern[:-1]
    if not pattern.endswith("*") or GLOB_CHARACTERS.intersection(literal):
        return False
    first_glob = min(
        (index for index, char in enumerate(target.value) if char in GLOB_CHARACTERS),
        default=len(target.value),
    )
    return target.value[:first_glob].startswith(literal)


@dataclass(frozen=True)
class AclPolicy:
    """An ordered, deny-by-default set of ACL rules."""

    rules: list[AclRule] = field(default_factory=list)


@dataclass(frozen=True)
class AclDecision:
    """A shadow-mode access decision: what enforcement would have done."""

    decision: str
    subject: str
    permission: str
    target: Target
    reason: str
    matched_rule: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form of the decision."""
        return {
            "decision": self.decision,
            "subject": self.subject,
            "permission": self.permission,
            "target": {"kind": self.target.kind, "value": self.target.value},
            "reason": self.reason,
            "matched_rule": self.matched_rule,
        }


def evaluate_access(
    *, subject: str, project: str, permission: str, target: Target, policy: AclPolicy
) -> AclDecision:
    """Return the deny-by-default shadow decision for one access request.

    The first rule that matches the permission, target kind and pattern, and
    namespace allows the access; if none match, the access would be denied.

    Returns
    -------
    AclDecision
        ``would_allow`` with the matching rule index, or ``would_deny`` when no
        rule grants the access (including an unknown permission verb).
    """
    if permission not in PERMISSIONS:
        return AclDecision(
            WOULD_DENY, subject, permission, target, f"unknown permission '{permission}'"
        )
    for index, rule in enumerate(policy.rules):
        if rule.matches(project=project, permission=permission, target=target):
            return AclDecision(
                WOULD_ALLOW,
                subject,
                permission,
                target,
                rule.reason or f"granted by rule {index}",
                matched_rule=index,
            )
    return AclDecision(
        WOULD_DENY, subject, permission, target, "no rule grants this access (deny by default)"
    )


def task_grant(policy: AclPolicy | None, subject: str, task_id: str, permission: str) -> bool:
    """Return whether ``policy`` grants ``subject`` ``permission`` on the claim of ``task_id``.

    The subject's project is read from its name. No policy grants nothing: a
    handler that enforces a permission itself, as the operation records and
    ``operation_state`` do, keeps an open hub from accepting third-party
    evidence or a seal and from answering an operation's attempts.
    """
    if policy is None:
        return False
    decision = evaluate_access(
        subject=subject,
        project=project_of(subject),
        permission=permission,
        target=Target("claim", task_id),
        policy=policy,
    )
    return decision.decision == WOULD_ALLOW


def scope_grant(
    policy: AclPolicy | None,
    subject: str,
    scope: str,
    permission: str,
    *,
    target_kind: str = "stop-scope",
) -> bool:
    """Return whether ``policy`` grants ``subject`` ``permission`` on the scope ``scope``.

    The target is kind ``target_kind`` (``stop-scope`` or ``level-scope``) with
    the scope as written. No policy grants nothing, so a handler that enforces a
    permission itself keeps an open hub from lifting a stop or setting a level.
    """
    if policy is None:
        return False
    decision = evaluate_access(
        subject=subject,
        project=project_of(subject),
        permission=permission,
        target=Target(target_kind, scope),
        policy=policy,
    )
    return decision.decision == WOULD_ALLOW


def load_acl_policy(path: str | Path) -> AclPolicy:
    """Load and validate an ACL policy from a JSON rules file.

    Parameters
    ----------
    path : str or pathlib.Path
        JSON file holding ``{"rules": [{permission, target_kind, target_pattern,
        namespace?, reason?}, ...]}``.

    Returns
    -------
    AclPolicy
        The validated policy.

    Raises
    ------
    AclError
        When the file is missing, not JSON, or a rule is malformed or names an
        unknown permission.
    """
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AclError(f"ACL policy file does not exist: {target}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AclError(f"invalid ACL JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise AclError("ACL policy must be an object with a 'rules' list")
    return AclPolicy(rules=[_parse_rule(entry, index) for index, entry in enumerate(data["rules"])])


def _parse_rule(entry: object, index: int) -> AclRule:
    """Parse one ACL rule object, validating its permission and target."""
    if not isinstance(entry, dict):
        raise AclError(f"ACL rule {index} must be an object")
    permission = str(entry.get("permission", "")).strip()
    if permission not in PERMISSIONS:
        raise AclError(f"ACL rule {index} has unknown permission '{permission}'")
    target_kind = str(entry.get("target_kind", "")).strip()
    target_pattern = str(entry.get("target_pattern", "")).strip()
    if not target_kind or not target_pattern:
        raise AclError(f"ACL rule {index} needs non-empty target_kind and target_pattern")
    return AclRule(
        permission=permission,
        target_kind=target_kind,
        target_pattern=target_pattern,
        namespace=str(entry.get("namespace", "")).strip(),
        reason=str(entry.get("reason", "")).strip(),
    )
