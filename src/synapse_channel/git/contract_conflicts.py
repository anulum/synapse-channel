# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — API contract conflicts without a shared file
"""Find work that breaks an API contract another branch depends on.

Path and symbol claims see a conflict only when two tasks touch the same
file. An interface change and a client generated against the old interface
share no file, yet they break each other once both are merged. This module
finds that case from exact artefact versions, never from a guess.

A repository declares its contracts in ``contracts.json`` at its root::

    {"contracts": [
      {"name": "public-api", "spec": "api/openapi.yaml",
       "dependents": [{"kind": "client", "path": "clients/python/",
                       "record": "clients/python/CONTRACT"}]}
    ]}

A contract's identity is the SHA-256 of its ``spec`` file at a revision. Each
dependent keeps a record file holding exactly ``<name> sha256:<64 hex>``, the
digest it was built from, written by the generator or the deploy step.

Two conflicts are reported, each with its evidence:

``record_mismatch``
    A branch changes a contract but one of its dependents still records
    another digest: the regeneration was forgotten.
``stale_dependent``
    One branch changes a contract while another branch changes a dependent of
    it that records a digest other than the first branch's new one.

Every revision is frozen to its commit once per report, the base must be an
ancestor of every branch, and all bytes come from git objects whose content is
checked against their object id. Only a tree with no entry at a path reads as
absent; a directory, a symbolic link, or an object git cannot read is refused,
never read as absent. A spec missing at the base and a branch that changes
``contracts.json`` are refused rather than read. The check is advisory:
it never claims, releases, blocks or grants anything. Byte identity is
deliberate: a whitespace-only change of a spec is a finding here, a false
block that the benchmark counts.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.errors import SynapseError

MANIFEST = "contracts.json"
"""Repository-relative manifest that declares the contracts."""

KINDS = frozenset({"client", "deployment", "other"})
"""Kinds of dependent a contract may have."""

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_FILE_MODES = frozenset({b"100644", b"100755"})
_RECORD = re.compile(r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,63}) (?P<digest>sha256:[0-9a-f]{64})")
_GIT_TIMEOUT_SECONDS = 30.0


class ContractError(SynapseError, ValueError):
    """The manifest, a record or a revision cannot be used."""

    code = "contract"


@dataclass(frozen=True, slots=True)
class Dependent:
    """Something built against a contract, with the record of which version."""

    kind: str
    path: str
    record: str


@dataclass(frozen=True, slots=True)
class Contract:
    """One API contract: its spec file and what depends on it."""

    name: str
    spec: str
    dependents: tuple[Dependent, ...]


@dataclass(frozen=True, slots=True)
class Conflict:
    """One mechanically evidenced conflict over exact versions."""

    kind: str
    contract: str
    evidence: dict[str, str]

    def to_json(self) -> dict[str, object]:
        """Return the conflict as plain data."""
        return {"kind": self.kind, "contract": self.contract, "evidence": dict(self.evidence)}


def _relative(value: object, label: str, *, directory: bool = False) -> str:
    """Return ``value`` when it is a plain repository-relative path.

    A file path has no empty, ``.`` or ``..`` segment and no trailing slash.
    With ``directory`` one trailing slash may mark a directory; a repeated one
    is still an empty segment and is refused. A path must encode as UTF-8, so
    an unpaired surrogate from an escaped JSON string is refused before git.
    """
    if not isinstance(value, str):
        raise ContractError(f"{label} is a plain repository-relative path")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ContractError(f"{label} is not valid UTF-8 text") from exc
    text = value[:-1] if directory and value.endswith("/") else value
    if (
        not text
        or text.startswith("/")
        or "\\" in text
        or "\0" in text
        or any(part in {"", ".", ".."} for part in text.split("/"))
    ):
        raise ContractError(f"{label} is a plain repository-relative path")
    return value


def _dependent(entry: object) -> Dependent:
    """Return one validated dependent entry."""
    if not isinstance(entry, dict) or set(entry) != {"kind", "path", "record"}:
        raise ContractError("a dependent has exactly kind, path and record")
    kind = entry["kind"]
    if not isinstance(kind, str) or kind not in KINDS:
        raise ContractError(f"a dependent's kind is one of {sorted(KINDS)}")
    return Dependent(
        kind,
        _relative(entry["path"], "a dependent's path", directory=True),
        _relative(entry["record"], "a dependent's record"),
    )


def _unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object, refusing a repeated member name."""
    names = [name for name, _value in pairs]
    if len(set(names)) != len(names):
        raise ContractError(f"{MANIFEST} repeats a member name")
    return dict(pairs)


def _contract(entry: object) -> Contract:
    """Return one validated contract entry."""
    if not isinstance(entry, dict) or not {"name", "spec"} <= set(entry) <= {
        "name",
        "spec",
        "dependents",
    }:
        raise ContractError("a contract has name, spec and optional dependents")
    name = entry["name"]
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise ContractError("a contract name is 1 to 64 of [A-Za-z0-9._-]")
    dependents = entry.get("dependents", [])
    if not isinstance(dependents, list):
        raise ContractError("a contract's dependents are a list")
    return Contract(
        name,
        _relative(entry["spec"], "a contract's spec"),
        tuple(_dependent(item) for item in dependents),
    )


def parse_manifest(text: str) -> tuple[Contract, ...]:
    """Return the contracts a ``contracts.json`` text declares.

    Raises
    ------
    ContractError
        If the text is not JSON, repeats a member, or declares a malformed or
        repeated contract.
    """
    try:
        data = json.loads(text, object_pairs_hook=_unique_members)
    except json.JSONDecodeError as exc:
        raise ContractError(f"{MANIFEST} is not JSON") from exc
    if not isinstance(data, dict) or set(data) != {"contracts"}:
        raise ContractError(f'{MANIFEST} is an object with only a "contracts" list')
    entries = data["contracts"]
    if not isinstance(entries, list):
        raise ContractError(f'{MANIFEST} is an object with only a "contracts" list')
    contracts = [_contract(entry) for entry in entries]
    if len({contract.name for contract in contracts}) != len(contracts):
        raise ContractError("contract names are unique")
    return tuple(contracts)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run one read-only git command in ``repo`` without a shell."""
    return subprocess.run(  # noqa: S603 - fixed git argv, never a shell
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def resolve(repo: Path, revision: str) -> str:
    """Return the commit object id ``revision`` names now, frozen for one report.

    Raises
    ------
    ContractError
        If ``revision`` is not a commit of ``repo``.
    """
    done = _git(
        repo, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"
    )
    oid = done.stdout.decode("ascii", "replace").strip()
    if done.returncode != 0 or _OID.fullmatch(oid) is None:
        raise ContractError(f"{revision} is not a commit of {repo}")
    return oid


def _object_id(data: bytes, width: int) -> str:
    """Return the git blob object id of ``data`` for a ``width``-digit repository."""
    framed = b"blob %d\0" % len(data) + data
    if width == 40:
        return hashlib.sha1(framed, usedforsecurity=False).hexdigest()
    return hashlib.sha256(framed).hexdigest()


def read_at(repo: Path, oid: str, path: str) -> bytes | None:
    """Return the regular file at ``path`` in commit ``oid``, or ``None`` when absent.

    Only git objects are read, never the working tree. ``None`` means only
    that the commit's tree has no entry at ``path``.

    Raises
    ------
    ContractError
        If the tree cannot be listed, the entry is not a regular file (a
        directory, a symbolic link or a submodule), or the blob cannot be read
        or does not match its object id.
    """
    listed = _git(repo, "--literal-pathspecs", "ls-tree", "-z", "--full-tree", oid, "--", path)
    if listed.returncode != 0:
        raise ContractError(f"cannot list {path} at {oid}")
    entries = [
        meta
        for meta, _tab, name in (item.partition(b"\t") for item in listed.stdout.split(b"\0"))
        if name == path.encode()
    ]
    if not entries:
        return None
    mode, kind, blob = entries[0].split(b" ")
    if kind != b"blob" or mode not in _FILE_MODES:
        raise ContractError(f"{path} at {oid} is not a regular file")
    shown = _git(repo, "cat-file", "blob", blob.decode("ascii"))
    if shown.returncode != 0 or _object_id(shown.stdout, len(blob)) != blob.decode("ascii"):
        raise ContractError(f"cannot read {path} at {oid}: the object is missing or damaged")
    return shown.stdout


def digest(data: bytes | None) -> str:
    """Return ``sha256:<hex>`` of ``data``, or ``absent`` for a missing file."""
    return "absent" if data is None else "sha256:" + hashlib.sha256(data).hexdigest()


def recorded(repo: Path, oid: str, contract: Contract, dependent: Dependent) -> str:
    """Return the digest ``dependent`` records for ``contract`` in commit ``oid``.

    Returns
    -------
    str
        The recorded ``sha256:`` digest, ``absent`` when there is no record
        file, or ``unreadable`` when the record does not name this contract
        in the exact form. A matching record is the generator's or deploy
        step's statement; it does not prove the dependent behaves correctly.
    """
    raw = read_at(repo, oid, dependent.record)
    if raw is None:
        return "absent"
    match = _RECORD.fullmatch(raw.decode("utf-8", "replace").strip())
    if match is None or match["name"] != contract.name:
        return "unreadable"
    return match["digest"]


def changed_paths(repo: Path, base_oid: str, oid: str) -> tuple[str, ...]:
    """Return the paths commit ``oid`` changed against the exact commit ``base_oid``.

    ``base_oid`` must be an ancestor of ``oid``; the comparison is between those
    two trees, without rename detection, so a moved or deleted record shows as
    a change of its old path.

    Raises
    ------
    ContractError
        If ``base_oid`` is not an ancestor of ``oid``, or git cannot compare them.
    """
    if _git(repo, "merge-base", "--is-ancestor", base_oid, oid).returncode != 0:
        raise ContractError(f"{base_oid} is not an ancestor of {oid}")
    done = _git(repo, "diff", "--name-only", "-z", "--no-renames", base_oid, oid, "--")
    if done.returncode != 0:
        raise ContractError(f"cannot compare {base_oid} with {oid}")
    return tuple(item for item in done.stdout.decode("utf-8").split("\0") if item)


def _touches(paths: Sequence[str], dependent: Dependent) -> bool:
    """Return whether one of ``paths`` is ``dependent``'s path or a path inside it."""
    prefix = dependent.path.rstrip("/")
    return any(path == prefix or path.startswith(prefix + "/") for path in paths)


def _evidence(**items: str) -> dict[str, str]:
    """Return evidence fields in a stable order."""
    return dict(sorted(items.items()))


@dataclass(frozen=True, slots=True)
class Report:
    """The frozen inputs of one check and what it found."""

    configured: bool
    base: str
    branches: dict[str, str]
    manifest: str
    conflicts: tuple[Conflict, ...]

    def to_json(self) -> dict[str, object]:
        """Return the report as plain data, conflicts in a stable order."""
        return {
            "configured": self.configured,
            "base_oid": self.base,
            "branch_oids": dict(self.branches),
            "manifest_digest": self.manifest,
            "conflicts": [conflict.to_json() for conflict in self.conflicts],
        }


def check(repo: Path, base: str, branches: Sequence[str]) -> Report:
    """Check ``branches`` against the exact commit ``base`` names, all frozen once.

    The contracts are those ``contracts.json`` declares at the base. A branch
    that changes the manifest is refused rather than read: a dropped
    declaration must not look like the absence of a conflict.

    Raises
    ------
    ContractError
        If a revision is unknown, the base is not an ancestor of a branch, a
        branch changes the manifest, the manifest is malformed, a declared spec
        is missing at the base, or a git object cannot be read.
    """
    base_oid = resolve(repo, base)
    oids = {branch: resolve(repo, branch) for branch in branches}
    manifest = read_at(repo, base_oid, MANIFEST)
    changed = {branch: changed_paths(repo, base_oid, oid) for branch, oid in oids.items()}
    if manifest is None:
        return Report(False, base_oid, oids, "absent", ())
    for branch, oid in oids.items():
        if read_at(repo, oid, MANIFEST) != manifest:
            raise ContractError(f"{branch} changes {MANIFEST}; check it once it is on the base")
    conflicts: list[Conflict] = []
    for contract in parse_manifest(manifest.decode("utf-8")):
        base_spec = read_at(repo, base_oid, contract.spec)
        if base_spec is None:
            raise ContractError(f"{contract.spec} of {contract.name} is not in the base")
        base_digest = digest(base_spec)
        heads = {branch: digest(read_at(repo, oid, contract.spec)) for branch, oid in oids.items()}
        for branch in [name for name in oids if heads[name] != base_digest]:
            mover = (branch, oids[branch], heads[branch])
            conflicts.extend(_forgotten(repo, contract, mover))
            for other, oid in oids.items():
                if other != branch:
                    conflicts.extend(_stale(repo, contract, mover, (other, oid), changed[other]))
    return Report(True, base_oid, oids, digest(manifest), tuple(conflicts))


def _forgotten(repo: Path, contract: Contract, mover: tuple[str, str, str]) -> list[Conflict]:
    """Return the dependents a spec-changing branch left recording another digest."""
    branch, oid, new = mover
    found = []
    for dependent in contract.dependents:
        have = recorded(repo, oid, contract, dependent)
        if have != new:
            found.append(
                Conflict(
                    "record_mismatch",
                    contract.name,
                    _evidence(
                        branch=branch,
                        branch_oid=oid,
                        spec_digest=new,
                        dependent=dependent.path,
                        record=dependent.record,
                        recorded=have,
                    ),
                )
            )
    return found


def _stale(
    repo: Path,
    contract: Contract,
    mover: tuple[str, str, str],
    other: tuple[str, str],
    other_changed: Sequence[str],
) -> list[Conflict]:
    """Return dependents another branch changed while recording another digest."""
    branch, branch_oid, new = mover
    name, oid = other
    found = []
    for dependent in contract.dependents:
        if not _touches(other_changed, dependent):
            continue
        have = recorded(repo, oid, contract, dependent)
        if have != new:
            found.append(
                Conflict(
                    "stale_dependent",
                    contract.name,
                    _evidence(
                        changes_contract=branch,
                        changes_contract_oid=branch_oid,
                        spec_digest=new,
                        changes_dependent=name,
                        changes_dependent_oid=oid,
                        dependent=dependent.path,
                        record=dependent.record,
                        recorded=have,
                    ),
                )
            )
    return found
