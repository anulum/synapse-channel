# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — observed release verification receipts
"""Observed verification evidence for release receipts."""

from __future__ import annotations

import hashlib
import subprocess  # nosec B404
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, cast

from synapse_channel.core.merkle import MerkleRoot, root_to_json, run_root, verify_root
from synapse_channel.core.receipts import ReleaseReceipt, build_release_receipt

DEFAULT_COMMAND_TIMEOUT_SECONDS = 1800.0
"""Default per-command verification timeout; a hung command is recorded as failed."""

GIT_TIMEOUT_SECONDS = 60.0
"""Timeout for each Git query, so a stuck Git invocation cannot hang the run."""

_HASH_CHUNK_BYTES = 1 << 20
"""Streaming hash chunk size, bounding artifact-hash memory to a fixed window."""


class GitState(NamedTuple):
    """Git state captured for a verified release receipt.

    Attributes
    ----------
    head : str
        Current ``HEAD`` commit hash, or an empty string outside a Git checkout.
    tree : str
        Current ``HEAD`` tree hash, or an empty string outside a Git checkout.
    changed_files : list[str]
        Git-observed modified and untracked file paths.
    """

    head: str
    tree: str
    changed_files: list[str]


class CommandEvidence(TypedDict):
    """Observed subprocess result stored in a verified release receipt."""

    argv: list[str]
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str


class ArtifactEvidence(TypedDict):
    """Observed artifact hash stored in a verified release receipt."""

    path: str
    sha256: str
    size_bytes: int


class VerificationDetails(TypedDict, total=False):
    """Machine-readable observed verification details."""

    commands: list[CommandEvidence]
    artifacts: list[ArtifactEvidence]
    changed_files: list[str]
    git_head: str
    git_tree: str
    timestamp: float
    signature: str
    merkle: dict[str, object]


class VerifiedReleaseReceipt(ReleaseReceipt, total=False):
    """Release receipt extended with observed verification metadata."""

    verification: VerificationDetails


def _sha256_bytes(payload: bytes) -> str:
    """Return the SHA-256 digest for ``payload``."""
    return hashlib.sha256(payload).hexdigest()


def _command_failure(argv: list[str], detail: bytes) -> CommandEvidence:
    """Return failure evidence for a command that could not run to completion."""
    return {
        "argv": list(argv),
        "exit_code": -1,
        "stdout_sha256": _sha256_bytes(b""),
        "stderr_sha256": _sha256_bytes(detail),
    }


def _run_command(argv: list[str], *, cwd: Path | None, timeout_seconds: float) -> CommandEvidence:
    """Run one command and return digest-only observed evidence.

    A command that is empty, cannot launch, or exceeds ``timeout_seconds`` is
    itself evidence: it is recorded as a failure (``exit_code`` ``-1``) instead of
    aborting the whole run, so one bad command never discards the evidence already
    gathered.
    """
    if not argv:
        return _command_failure(argv, b"empty verification command")
    try:
        result = subprocess.run(  # nosec B603
            argv,
            cwd=str(cwd) if cwd is not None else None,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return _command_failure(argv, f"timed out after {timeout_seconds:.0f}s".encode())
    except OSError as exc:
        return _command_failure(argv, f"could not launch: {exc}".encode())
    return {
        "argv": list(argv),
        "exit_code": int(result.returncode),
        "stdout_sha256": _sha256_bytes(result.stdout),
        "stderr_sha256": _sha256_bytes(result.stderr),
    }


def _hash_artifact(path: Path) -> ArtifactEvidence | None:
    """Return streamed hash evidence for ``path``, or ``None`` when unreadable.

    The file is hashed in fixed-size chunks so a multi-gigabyte artifact never
    needs to be fully resident. A missing path, a directory, or a file that
    disappears between the check and the read all return ``None`` rather than
    crashing the run.
    """
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
                size += len(chunk)
    except OSError:
        return None
    return {"path": str(path), "sha256": digest.hexdigest(), "size_bytes": size}


def _git_stdout(root: Path, args: list[str]) -> str:
    """Return stripped stdout from one Git command, or an empty string on failure."""
    try:
        result = subprocess.run(  # nosec B603,B607
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def collect_git_state(root: str | Path = ".") -> GitState:
    """Collect HEAD, tree, and changed file paths from a Git checkout.

    Parameters
    ----------
    root : str or pathlib.Path, optional
        Directory where Git commands should run. Defaults to the current working
        directory.

    Returns
    -------
    GitState
        Current commit/tree identifiers and sorted changed-file paths. Outside a
        Git checkout the identifiers and changed-file list are empty.
    """
    repo = Path(root)
    head = _git_stdout(repo, ["rev-parse", "HEAD"])
    tree = _git_stdout(repo, ["rev-parse", "HEAD^{tree}"])
    modified = _git_stdout(repo, ["diff", "--name-only", "HEAD"]).splitlines()
    untracked = _git_stdout(repo, ["ls-files", "--others", "--exclude-standard"]).splitlines()
    return GitState(
        head=head,
        tree=tree,
        changed_files=sorted({path for path in [*modified, *untracked] if path}),
    )


def build_verified_release_receipt(
    *,
    task_id: str,
    owner: str,
    commands: list[list[str]],
    artifacts: list[str | Path],
    changed_files: list[str],
    git_head: str,
    git_tree: str,
    timestamp: float | None = None,
    signature: str = "",
    cwd: str | Path | None = None,
    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    merkle: MerkleRoot | None = None,
) -> VerifiedReleaseReceipt:
    """Run declared checks and build a JSON-serialisable release receipt.

    Parameters
    ----------
    task_id, owner : str
        Claim id and releasing identity for the receipt.
    commands : list[list[str]]
        Commands to execute as argv vectors. Each command is run directly without
        shell interpretation.
    artifacts : list[str | pathlib.Path]
        Artifact paths whose content hashes should be recorded.
    changed_files : list[str]
        Git-observed changed files to attach to the release receipt.
    git_head, git_tree : str
        Git commit and tree identifiers captured with the receipt.
    timestamp : float or None, optional
        Receipt timestamp. ``None`` uses :func:`time.time`.
    signature : str, optional
        Optional caller-supplied signature reference.
    cwd : str or pathlib.Path or None, optional
        Working directory for verification commands.
    merkle : MerkleRoot or None, optional
        Coordination-log Merkle commitment captured at release time. When given
        the receipt carries the root as evidence and machine-readable detail, so
        the exact coordination history behind the release is tamper-evident:
        :func:`check_receipt_merkle_commitment` later recomputes the same log
        prefix and any rewrite of it changes the root.

    Returns
    -------
    VerifiedReleaseReceipt
        Release receipt carrying observed command, artifact, and Git evidence.
    """
    command_cwd = Path(cwd) if cwd is not None else None
    command_results = [
        _run_command(command, cwd=command_cwd, timeout_seconds=command_timeout_seconds)
        for command in commands
    ]
    artifact_results: list[ArtifactEvidence] = []
    known_failures: list[str] = []
    for command in command_results:
        if command["exit_code"] != 0:
            known_failures.append(
                f"verification command failed: {' '.join(command['argv'])} "
                f"exit={command['exit_code']}"
            )
    for artifact in artifacts:
        artifact_path = Path(artifact)
        artifact_result = _hash_artifact(artifact_path)
        if artifact_result is None:
            known_failures.append(f"artifact missing: {artifact_path}")
            continue
        artifact_results.append(artifact_result)
    evidence = [
        "command: "
        + " ".join(command["argv"])
        + f" exit={command['exit_code']} stdout_sha256={command['stdout_sha256']} "
        + f"stderr_sha256={command['stderr_sha256']}"
        for command in command_results
    ]
    if changed_files:
        # git_tree is HEAD's tree, but uncommitted changes were present, so the
        # commands ran against content that hash does not represent. Surface the
        # drift as visible (non-failing) evidence rather than letting git_tree
        # read as clean provenance — verify-release commonly runs pre-commit.
        evidence.append(
            f"note: working tree had {len(changed_files)} uncommitted change(s); "
            f"git_tree {git_tree or '(none)'} is HEAD, not the verified working-tree content"
        )
    if merkle is not None:
        evidence.append(
            f"merkle root: {merkle.root} over {merkle.tree_size} coordination-log "
            f"event(s) (seq {merkle.first_seq}..{merkle.last_seq})"
        )
    artifact_lines = [
        f"{artifact['path']} sha256={artifact['sha256']} size={artifact['size_bytes']}"
        for artifact in artifact_results
    ]
    receipt = build_release_receipt(
        task_id=task_id,
        owner=owner,
        evidence=evidence,
        artifacts=artifact_lines,
        known_failures=known_failures,
        changed_files=changed_files,
        confidence="observed",
        freshness_seconds=0.0,
    )
    verification: VerificationDetails = {
        "commands": command_results,
        "artifacts": artifact_results,
        "changed_files": changed_files,
        "git_head": git_head,
        "git_tree": git_tree,
        "timestamp": time.time() if timestamp is None else float(timestamp),
    }
    if signature:
        verification["signature"] = signature
    if merkle is not None:
        verification["merkle"] = root_to_json(merkle)
    verified = cast(VerifiedReleaseReceipt, dict(receipt))
    verified["verification"] = verification
    return verified


class MerkleCommitmentCheck(NamedTuple):
    """The outcome of re-verifying a receipt's coordination-log commitment.

    Attributes
    ----------
    status : str
        ``"pass"`` when the recomputed log prefix matches the recorded root,
        ``"fail"`` when it does not (or the recorded commitment is malformed),
        ``"not_applicable"`` when the receipt carries no commitment.
    reason : str
        One line explaining the outcome.
    recorded_root : str
        The root the receipt recorded; empty when absent.
    recomputed_root : str
        The root recomputed from the log now; empty when not recomputed.
    """

    status: str
    reason: str
    recorded_root: str = ""
    recomputed_root: str = ""


def check_receipt_merkle_commitment(
    receipt: Mapping[str, Any],
    db_path: str | Path,
) -> MerkleCommitmentCheck:
    """Recompute a receipt's coordination-log commitment against the log today.

    The receipt recorded the RFC 6962 root over the log through ``last_seq`` at
    release time. Because the log is append-only, recomputing the same prefix
    later must reproduce the same root: events appended after ``last_seq`` do not
    disturb it, while any rewrite, removal, or renumbering inside the committed
    prefix changes it. The comparison is constant-time.

    Parameters
    ----------
    receipt : Mapping[str, Any]
        A parsed release-receipt JSON document.
    db_path : str or pathlib.Path
        Path to the hub event store the receipt committed to.

    Returns
    -------
    MerkleCommitmentCheck
        Pass/fail with the recorded and recomputed roots.

    Raises
    ------
    ValueError
        If the event store does not exist.
    """
    verification = receipt.get("verification")
    merkle = verification.get("merkle") if isinstance(verification, Mapping) else None
    if not isinstance(merkle, Mapping):
        return MerkleCommitmentCheck(
            status="not_applicable",
            reason="receipt carries no coordination-log commitment",
        )
    recorded_root = str(merkle.get("root") or "")
    last_seq = merkle.get("last_seq")
    tree_size = merkle.get("tree_size")
    if not recorded_root or not isinstance(last_seq, int) or not isinstance(tree_size, int):
        return MerkleCommitmentCheck(
            status="fail",
            reason="recorded commitment is malformed: needs root, last_seq, and tree_size",
            recorded_root=recorded_root,
        )
    recomputed = run_root(db_path, through_seq=last_seq)
    if recomputed.tree_size != tree_size:
        return MerkleCommitmentCheck(
            status="fail",
            reason=(
                f"log prefix through seq {last_seq} now holds {recomputed.tree_size} "
                f"event(s); the receipt committed to {tree_size}"
            ),
            recorded_root=recorded_root,
            recomputed_root=recomputed.root,
        )
    if not verify_root(recomputed.root, recorded_root):
        return MerkleCommitmentCheck(
            status="fail",
            reason="commitment mismatch: the committed log prefix changed since the receipt",
            recorded_root=recorded_root,
            recomputed_root=recomputed.root,
        )
    return MerkleCommitmentCheck(
        status="pass",
        reason=(
            f"coordination log through seq {last_seq} still matches the recorded "
            f"root over {tree_size} event(s)"
        ),
        recorded_root=recorded_root,
        recomputed_root=recomputed.root,
    )
