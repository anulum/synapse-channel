# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hand over verifiable work state, never authority
"""A hand-off packet that a new agent can check, and that grants nothing.

When work moves from one agent to another, a conversation is not enough. The
``synapse.work-state.v1`` packet names what the next agent needs:

* ``inputs``: files with the SHA-256 they had when the packet was built;
* ``artefacts``: repositories with the exact commit they were at;

  every subject is named by its absolute path, so the packet means the same
  files wherever it is verified, and a path is either an input or an
  artefact, never both;
* ``completed_steps``, ``unknown_effects`` (operation ids whose outcome is
  unknown) and ``remaining_conditions``;
* ``claims``: what the previous agent asserts. Each claim has an ``origin``:
  ``agent_claim`` (the agent says so), ``tool_result`` (a tool reported it) or
  ``verified_artefact`` (it rests on one of the packet's inputs or artefacts,
  which the receiver checks again). The receiver checks only that subject's
  bytes or commit; the claim's text itself is never checked.

The packet never carries authority. A field that would, such as a lease,
fencing epoch, token or grant, is refused. :func:`verify_packet` checks every
input and artefact against the current world and states for each claim what
it rests on. The receiver acquires its own authority from the current hub
state, for example by claiming the task.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess  # nosec B404 # fixed git argv only, never a shell
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from synapse_channel.core.errors import SynapseError

SCHEMA = "synapse.work-state.v1"
"""Version of the hand-off packet."""

ORIGINS = ("agent_claim", "tool_result", "verified_artefact")
"""Where a claim comes from; only the last can be checked by the receiver."""

AUTHORITY_FIELDS = frozenset(
    {"lease", "lease_id", "epoch", "fencing_epoch", "token", "grant", "authority", "credential"}
)
"""Fields a hand-off must never carry: authority is re-acquired, not inherited."""

_FIELDS = frozenset(
    {
        "schema",
        "task_id",
        "inputs",
        "artefacts",
        "completed_steps",
        "unknown_effects",
        "remaining_conditions",
        "claims",
    }
)
_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,500}")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_MAX_ITEMS = 256


class WorkStateError(SynapseError, ValueError):
    """A work-state packet is malformed or tries to carry authority."""

    code = "work_state"


def _text(value: object, label: str) -> str:
    """Return ``value`` when it is printable text of 1 to 500 characters."""
    if not isinstance(value, str) or _TEXT.fullmatch(value) is None:
        raise WorkStateError(f"{label} is printable text of 1 to 500 characters")
    return value


def _texts(value: object, label: str) -> list[str]:
    """Return ``value`` when it is a bounded list of printable texts."""
    if not isinstance(value, list) or len(value) > _MAX_ITEMS:
        raise WorkStateError(f"{label} is a list of at most {_MAX_ITEMS} texts")
    return [_text(item, label) for item in value]


def file_digest(path: Path) -> str:
    """Return ``sha256:<hex>`` of a file's content."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def repository_commit(repo: Path) -> str:
    """Return the full commit a repository's ``HEAD`` names.

    Raises
    ------
    WorkStateError
        If ``repo`` is not a git repository with a commit.
    """
    done = subprocess.run(  # noqa: S603  # nosec B603 B607 # fixed git argv, no shell
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD^{commit}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    commit = done.stdout.strip()
    if done.returncode != 0 or not _HEX40.fullmatch(commit):
        raise WorkStateError(f"{repo} is not a git repository with a commit")
    return commit


def build_packet(
    task_id: str,
    *,
    inputs: Sequence[Path] = (),
    artefacts: Sequence[Path] = (),
    completed_steps: Sequence[str] = (),
    unknown_effects: Sequence[str] = (),
    remaining_conditions: Sequence[str] = (),
    claims: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """Build a packet from the current state of the named inputs and artefacts.

    Parameters
    ----------
    task_id : str
        The task being handed over.
    inputs : sequence of pathlib.Path, optional
        Files whose current SHA-256 is recorded.
    artefacts : sequence of pathlib.Path, optional
        Repositories whose current commit is recorded.
    completed_steps, unknown_effects, remaining_conditions : sequence of str, optional
        What was done, which operation outcomes are unknown, and what remains.
    claims : sequence of mapping, optional
        Each ``{"text": ..., "origin": ...}``; a ``verified_artefact`` claim
        also names the ``subject`` (an input or artefact path) it rests on.

    Returns
    -------
    dict[str, Any]
        A packet that :func:`validate_packet` accepts.
    """
    packet = {
        "schema": SCHEMA,
        "task_id": task_id,
        "inputs": {_absolute(path): file_digest(path) for path in inputs},
        "artefacts": {_absolute(path): repository_commit(path) for path in artefacts},
        "completed_steps": list(completed_steps),
        "unknown_effects": list(unknown_effects),
        "remaining_conditions": list(remaining_conditions),
        "claims": [_bound_claim(claim) for claim in claims],
    }
    return validate_packet(packet)


def _absolute(path: Path | str) -> str:
    """Return the absolute spelling of ``path`` as seen from the builder's directory."""
    return str(Path(path).absolute())


def _bound_claim(claim: Mapping[str, str]) -> dict[str, str]:
    """Return ``claim`` with its subject, if any, named by its absolute path."""
    bound = dict(claim)
    if isinstance(bound.get("subject"), str):
        bound["subject"] = _absolute(bound["subject"])
    return bound


def _claims(value: object, subjects: set[str]) -> list[dict[str, str]]:
    """Return the claims after checking each one's fields, types and subject."""
    if not isinstance(value, list) or len(value) > _MAX_ITEMS:
        raise WorkStateError(f"claims is a list of at most {_MAX_ITEMS} claims")
    result = []
    for claim in value:
        if not isinstance(claim, dict) or not {"text", "origin"} <= set(claim) <= {
            "text",
            "origin",
            "subject",
        }:
            raise WorkStateError("a claim has text, origin and, if verified, its subject")
        _text(claim["text"], "claim text")
        if not isinstance(claim["origin"], str) or claim["origin"] not in ORIGINS:
            raise WorkStateError(f"a claim's origin is one of {list(ORIGINS)}")
        if (claim["origin"] == "verified_artefact") != ("subject" in claim):
            raise WorkStateError("exactly the verified_artefact claims name a subject")
        if "subject" in claim and (
            not isinstance(claim["subject"], str) or claim["subject"] not in subjects
        ):
            raise WorkStateError(
                "a verified claim's subject is one of the packet's inputs or artefacts"
            )
        result.append({key: str(item) for key, item in claim.items()})
    return result


def _subjects(inputs: object, artefacts: object) -> tuple[dict[str, str], dict[str, str]]:
    """Return the inputs and artefacts after checking their paths and recorded values.

    Every path is absolute, so it names one file wherever the packet is
    verified, and no path is both an input and an artefact.
    """
    if not isinstance(inputs, dict) or not all(
        isinstance(value, str) and _DIGEST.fullmatch(value) for value in inputs.values()
    ):
        raise WorkStateError("inputs map paths to sha256:<64 hex> digests")
    if not isinstance(artefacts, dict) or not all(
        isinstance(value, str) and _HEX40.fullmatch(value) for value in artefacts.values()
    ):
        raise WorkStateError("artefacts map repository paths to 40-hex commits")
    for path in (*inputs, *artefacts):
        _text(path, "path")
        if not Path(path).is_absolute():
            raise WorkStateError(f"a subject path is absolute, so it means one file: {path}")
    overlap = sorted(set(inputs) & set(artefacts))
    if overlap:
        raise WorkStateError("a path is either an input or an artefact: " + ", ".join(overlap))
    return inputs, artefacts


def validate_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Return the packet after checking its shape; refuse any authority.

    Raises
    ------
    WorkStateError
        On an authority-bearing, missing, unknown or malformed field.
    """
    if not isinstance(packet, Mapping):
        raise WorkStateError("a work-state packet is a JSON object")
    carried = sorted(AUTHORITY_FIELDS & set(packet))
    if carried:
        raise WorkStateError(
            "a hand-off never carries authority; re-acquire it from the hub: " + ", ".join(carried)
        )
    if set(packet) != _FIELDS or packet["schema"] != SCHEMA:
        raise WorkStateError(f"a {SCHEMA} packet has exactly the fields {sorted(_FIELDS)}")
    _text(packet["task_id"], "task_id")
    inputs, artefacts = _subjects(packet["inputs"], packet["artefacts"])
    result = dict(packet)
    for field in ("completed_steps", "unknown_effects", "remaining_conditions"):
        result[field] = _texts(packet[field], field)
    result["claims"] = _claims(packet["claims"], set(inputs) | set(artefacts))
    return result


def _check(kind: str, path: str, recorded: str) -> tuple[str, str]:
    """Return ``(status, observed)`` of one input or artefact now."""
    try:
        observed = file_digest(Path(path)) if kind == "input" else repository_commit(Path(path))
    except (OSError, WorkStateError):
        return "missing", ""
    return ("unchanged" if observed == recorded else "changed"), observed


def verify_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Check a packet against the current world and say what each claim rests on.

    Parameters
    ----------
    packet : Mapping[str, Any]
        A packet as written by :func:`build_packet`.

    Returns
    -------
    dict[str, Any]
        ``subjects`` with their status now; ``claims`` with their standing;
        ``consistent`` when every input and artefact is unchanged; and the
        fixed statement that authority must be re-acquired.

    Raises
    ------
    WorkStateError
        If the packet is malformed or carries authority.
    """
    body = validate_packet(packet)
    subjects: dict[str, dict[str, str]] = {}
    for kind, field in (("input", "inputs"), ("artefact", "artefacts")):
        for path, recorded in body[field].items():
            status, observed = _check(kind, path, recorded)
            subjects[path] = {
                "kind": kind,
                "recorded": recorded,
                "observed": observed,
                "status": status,
            }
    standing = {
        "agent_claim": "unverified: the previous agent's word",
        "tool_result": "unverified: a tool's report, not checked by the receiver",
    }
    claims = []
    for claim in body["claims"]:
        if claim["origin"] == "verified_artefact":
            status = subjects[claim["subject"]]["status"]
            text = (
                "subject unchanged since the packet; the claim's text is not checked"
                if status == "unchanged"
                else f"no longer supported: subject {status}"
            )
        else:
            text = standing[claim["origin"]]
        claims.append({**claim, "standing": text})
    return {
        "schema": SCHEMA,
        "task_id": body["task_id"],
        "consistent": all(item["status"] == "unchanged" for item in subjects.values()),
        "subjects": subjects,
        "claims": claims,
        "unknown_effects": body["unknown_effects"],
        "remaining_conditions": body["remaining_conditions"],
        "authority": "none carried; claim the task from the current hub state",
    }


def load_packet(path: Path) -> dict[str, Any]:
    """Read a packet file as UTF-8 JSON without validating it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise WorkStateError(f"{path.name} is not UTF-8 text") from exc
    except json.JSONDecodeError as exc:
        raise WorkStateError(f"{path.name} is not JSON") from exc
    if not isinstance(data, dict):
        raise WorkStateError("a work-state packet is a JSON object")
    return data
