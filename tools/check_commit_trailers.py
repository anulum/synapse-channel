#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — forward-only commit-message policy
"""Enforce seat, authorship, subject-language and hook-use rules on new commits.

With a message-file argument, this module is the local ``commit-msg`` hook.
Without one, it audits every commit after the policy baseline. CI may pass an
event-specific revision range with ``--range``. Published history before the
baseline is immutable and deliberately outside this forward-only gate.

Hook use is enforced as well. Run with ``--stamp``, the ``commit-msg`` hook
writes ``Hooks-Verified: pre-commit sha256:<16 hex>`` above the ``Seat:``
trailer: a digest of the staged ``.pre-commit-config.yaml``. Git runs
``commit-msg`` only after every ``pre-commit`` hook has passed, so the stamp
shows that the hooks ran. The audit requires the stamp, matching that
commit's own hook configuration, on every commit whose configuration already
contains this rule. A commit made with ``--no-verify``, ``git commit-tree`` or
from a worktree without installed hooks has no stamp and is refused before
push and in CI. Whoever types a stamp by hand can defeat this; it stops
accidental bypass, not a determined forger.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

POLICY_BASELINE = "d668a54628d974d62eed44801c65573539e302da"
DEFAULT_AUDIT_RANGE = f"{POLICY_BASELINE}..HEAD"
REQUIRED_AUTHORSHIP_LINE = "Authored by Anulum Fortis & Arcane Sapience (protoscience@anulum.li)"
HISTORY_EXEMPTIONS = {
    "872ac28c56280f959c13f804e1b8d8a08dda9406": (
        "published with literal escaped newlines joining otherwise present trailers"
    ),
    # Dependabot pull requests are landed with GitHub's squash-merge, whose commit
    # is authored by dependabot[bot] and cannot carry the seat/authorship trailer.
    # These three are already published on main and are not amendable post-merge.
    "140653aace9e626edc09476f9612c227979414c7": (
        "dependabot squash-merge (#43 cockpit-minor-patch), authored by dependabot[bot]"
    ),
    "95645ad968e44650d4874a27cdd2e8399ca2b8f2": (
        "dependabot squash-merge (#45 actions/setup-node), authored by dependabot[bot]"
    ),
    "e86aa7bd728db5986df68d50bb359928c134fed1": (
        "dependabot squash-merge (#46 github-app-minor-patch), authored by dependabot[bot]"
    ),
    "ef7c1938799debc2815cd7bd1c324b8f6f3c0250": (
        "dependabot squash-merge (#47 actions/setup-python), authored by dependabot[bot]"
    ),
    "5d3803154a8abe5a335997d5be8e173fb99b22b9": (
        "dependabot squash-merge (#48 fast-uri 3.1.4, CVE-2026-16221), authored by dependabot[bot]"
    ),
    "46928a4fcc0b3bde92b8fa730ba2ba17de722de0": (
        "dependabot squash-merge (#50 cockpit-minor-patch), authored by dependabot[bot]"
    ),
    "c5ff0ec0d5f471335814e6549e92759a820f7b23": (
        "dependabot squash-merge (#52 actions-minor-patch), authored by dependabot[bot]"
    ),
    "cea5ef2a7ca947471ff5300ef0d1a72686833662": (
        "dependabot squash-merge (#53 github-app-minor-patch), authored by dependabot[bot]"
    ),
}
SEAT_PREFIX_RE = re.compile(r"^\s*Seat:")
SEAT_TRAILER_RE = re.compile(r"^Seat:\s+([A-Za-z0-9][A-Za-z0-9_-]{0,63})\s*$")
FORBIDDEN_SEAT_PREFIXES = ("claude-", "codex-")
CONFIG_PATH = ".pre-commit-config.yaml"
STAMP_MARKER = "check_commit_trailers.py --stamp"
STAMP_PREFIX_RE = re.compile(r"^\s*Hooks-Verified:")
STAMP_RE = re.compile(r"^Hooks-Verified: pre-commit sha256:([0-9a-f]{16})$")
FORBIDDEN_SUBJECT_WORDS = (
    "elite",
    "superior",
    "etalon",
    "comprehensive",
    "robust",
    "leveraging",
    "world-class",
    "best-in-class",
)
FORBIDDEN_SUBJECT_RE = re.compile(
    r"\b(" + "|".join(re.escape(word) for word in FORBIDDEN_SUBJECT_WORDS) + r")\b",
    re.IGNORECASE,
)


def _subject(message: str) -> str:
    """Return the first non-empty commit-message line."""
    return next((line.strip() for line in message.splitlines() if line.strip()), "")


def _stamp_shape_violations(lines: list[str]) -> list[str]:
    """Return why a message's hooks stamp, if any, is malformed or repeated."""
    stamps = [line.strip() for line in lines if STAMP_PREFIX_RE.match(line)]
    if len(stamps) > 1:
        return ["expected at most one `Hooks-Verified:` stamp"]
    if stamps and STAMP_RE.fullmatch(stamps[0]) is None:
        return ["malformed `Hooks-Verified:` stamp"]
    return []


def _message_violations(message: str) -> list[str]:
    """Return every policy violation in one commit message."""
    lines = message.splitlines()
    violations: list[str] = []

    authorship_indices = [
        index for index, line in enumerate(lines) if line.strip() == REQUIRED_AUTHORSHIP_LINE
    ]
    if len(authorship_indices) != 1:
        violations.append(f"expected exactly one `{REQUIRED_AUTHORSHIP_LINE}` line")

    seat_indices = [index for index, line in enumerate(lines) if SEAT_PREFIX_RE.match(line)]
    if len(seat_indices) != 1:
        violations.append("expected exactly one `Seat: <seat-suffix>` trailer")

    if len(seat_indices) == 1:
        seat_line = lines[seat_indices[0]].strip()
        match = SEAT_TRAILER_RE.fullmatch(seat_line)
        if match is None:
            violations.append("invalid vendor-neutral `Seat: <seat-suffix>` trailer")
        elif match.group(1).lower().startswith(FORBIDDEN_SEAT_PREFIXES):
            violations.append("vendor-prefixed `Seat:` trailer is forbidden")

    if len(authorship_indices) == 1 and len(seat_indices) == 1:
        seat_index = seat_indices[0]
        authorship_index = authorship_indices[0]
        between = lines[seat_index + 1 : authorship_index]
        if seat_index >= authorship_index or any(line.strip() for line in between):
            violations.append("`Seat:` must immediately precede the authorship line")

    violations += _stamp_shape_violations(lines)

    forbidden = list(
        dict.fromkeys(
            match.group(1).lower() for match in FORBIDDEN_SUBJECT_RE.finditer(_subject(message))
        )
    )
    if forbidden:
        violations.append(f"forbidden subject word(s): {', '.join(forbidden)}")
    return violations


def config_digest(config: bytes) -> str:
    """Return the 16-hex digest a stamp carries for one hook configuration."""
    return hashlib.sha256(config).hexdigest()[:16]


def stamp_message(message: str, digest: str) -> str:
    """Return ``message`` with exactly one hooks stamp directly above ``Seat:``.

    Any earlier stamp, for example from an amended message, is replaced.
    """
    lines = [line for line in message.splitlines() if not STAMP_PREFIX_RE.match(line)]
    seat = next(index for index, line in enumerate(lines) if SEAT_PREFIX_RE.match(line))
    lines.insert(seat, f"Hooks-Verified: pre-commit sha256:{digest}")
    return "\n".join(lines) + "\n"


def _staged_config() -> bytes | None:
    """Return the staged hook configuration, or ``None`` when it cannot be read."""
    git = _resolve_git()
    if git is None:
        return None
    result = subprocess.run([git, "show", f":{CONFIG_PATH}"], check=False, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def _check_message_file(path: Path, *, stamp: bool = False) -> int:
    """Validate one pending commit-message file and, with ``stamp``, stamp it."""
    try:
        message = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read commit message {path}: {exc}", file=sys.stderr)
        return 2
    violations = _message_violations(message)
    if not violations:
        if not stamp:
            return 0
        config = _staged_config()
        if config is None:
            print(f"cannot read the staged {CONFIG_PATH}; refusing to stamp", file=sys.stderr)
            return 2
        path.write_text(stamp_message(message, config_digest(config)), encoding="utf-8")
        return 0
    print("Commit message rejected:", file=sys.stderr)
    for violation in violations:
        print(f"  - {violation}", file=sys.stderr)
    print(
        "Use one vendor-neutral Synapse seat suffix, then the exact authorship line.",
        file=sys.stderr,
    )
    return 1


def _resolve_git() -> str | None:
    """Return a verified absolute Git executable path."""
    candidate = shutil.which("git")
    if candidate is None:
        return None
    try:
        resolved = Path(candidate).resolve(strict=True)
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return str(resolved)


def _run_git(git: str, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one non-shell Git command in the selected repository."""
    return subprocess.run(
        [git, "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _stamp_violations(git: str, root: Path, commit: str, message: str) -> list[str]:
    """Return why ``commit`` lacks a valid hooks stamp, when its configuration needs one."""
    config = subprocess.run(
        [git, "-C", str(root), "show", f"{commit}:{CONFIG_PATH}"],
        check=False,
        capture_output=True,
    )
    if config.returncode != 0 or STAMP_MARKER.encode() not in config.stdout:
        return []
    stamps = [
        match.group(1)
        for line in message.splitlines()
        if (match := STAMP_RE.fullmatch(line.strip())) is not None
    ]
    if not stamps:
        return ["no `Hooks-Verified:` stamp: the commit was made without the pre-commit hooks"]
    if stamps[0] != config_digest(config.stdout):
        return ["`Hooks-Verified:` stamp does not match this commit's hook configuration"]
    return []


def _audit_range(range_spec: str, *, repo: Path | None = None) -> int:
    """Audit commit messages in ``range_spec`` and return a CLI status code."""
    git = _resolve_git()
    if git is None:
        print("git executable unavailable", file=sys.stderr)
        return 2
    root = Path.cwd() if repo is None else repo
    revisions = _run_git(git, root, "rev-list", "--reverse", range_spec)
    if revisions.returncode != 0:
        print(f"git rev-list failed: {revisions.stderr.strip()}", file=sys.stderr)
        return 2

    failures: list[str] = []
    exemptions: list[str] = []
    commits = [line for line in revisions.stdout.splitlines() if line]
    for commit in commits:
        if reason := HISTORY_EXEMPTIONS.get(commit):
            exemptions.append(f"{commit[:12]}: {reason}")
            continue
        result = _run_git(git, root, "show", "-s", "--format=%B", commit)
        if result.returncode != 0:
            print(f"git show failed for {commit}: {result.stderr.strip()}", file=sys.stderr)
            return 2
        violations = _message_violations(result.stdout)
        violations += _stamp_violations(git, root, commit, result.stdout)
        if violations:
            failures.append(f"{commit[:12]}: {'; '.join(violations)}")

    print(f"Audited {len(commits)} commit(s) in {range_spec}")
    print(f"Explicit history exemptions: {len(exemptions)}")
    for exemption in exemptions:
        print(f"  - {exemption}")
    print(f"Violations: {len(failures)}")
    for failure in failures:
        print(f"  - {failure}")
    return 1 if failures else 0


def _parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message_file", nargs="?", type=Path)
    parser.add_argument("--range", dest="range_spec")
    parser.add_argument(
        "--stamp", action="store_true", help="commit-msg hook: stamp a valid message"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the commit-message hook or history audit."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.message_file is not None and args.range_spec is not None:
        parser.error("message_file and --range are mutually exclusive")
    if args.stamp and args.message_file is None:
        parser.error("--stamp needs a message_file")
    if args.message_file is not None:
        return _check_message_file(args.message_file, stamp=args.stamp)
    return _audit_range(args.range_spec or DEFAULT_AUDIT_RANGE)


if __name__ == "__main__":
    raise SystemExit(main())
