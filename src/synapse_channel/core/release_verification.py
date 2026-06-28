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
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

from synapse_channel.core.receipts import ReleaseReceipt, build_release_receipt


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


class VerifiedReleaseReceipt(ReleaseReceipt, total=False):
    """Release receipt extended with observed verification metadata."""

    verification: VerificationDetails


def _sha256_bytes(payload: bytes) -> str:
    """Return the SHA-256 digest for ``payload``."""
    return hashlib.sha256(payload).hexdigest()


def _run_command(argv: list[str], *, cwd: Path | None) -> CommandEvidence:
    """Run one command and return digest-only observed evidence."""
    result = subprocess.run(  # nosec B603
        argv,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=True,
    )
    return {
        "argv": list(argv),
        "exit_code": int(result.returncode),
        "stdout_sha256": _sha256_bytes(result.stdout),
        "stderr_sha256": _sha256_bytes(result.stderr),
    }


def _hash_artifact(path: Path) -> ArtifactEvidence | None:
    """Return hash evidence for ``path``, or ``None`` when it does not exist."""
    if not path.is_file():
        return None
    payload = path.read_bytes()
    return {
        "path": str(path),
        "sha256": _sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _git_stdout(root: Path, args: list[str]) -> str:
    """Return stripped stdout from one Git command, or an empty string on failure."""
    result = subprocess.run(  # nosec B603,B607
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
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

    Returns
    -------
    VerifiedReleaseReceipt
        Release receipt carrying observed command, artifact, and Git evidence.
    """
    command_cwd = Path(cwd) if cwd is not None else None
    command_results = [_run_command(command, cwd=command_cwd) for command in commands]
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
    verified = cast(VerifiedReleaseReceipt, dict(receipt))
    verified["verification"] = verification
    return verified
