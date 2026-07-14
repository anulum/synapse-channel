# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one-command claim-aware git onboarding (synapse git-init)
"""One-command claim-aware git onboarding behind ``synapse git-init``.

:func:`init_repo` installs the auto-release hooks (reusing
:func:`synapse_channel.git.githook.install_hooks`) and writes a short scaffold
guide under ``.synapse/`` documenting the branch-naming convention, the
recommended git-worktree-per-claim workflow, and the exact claim-check command.
It also records the repository-local identity and connection metadata used by
the staged gate. Everything is client-side and idempotent; the git executor and
target directories are injectable for isolated tests.
"""

from __future__ import annotations

from pathlib import Path

from synapse_channel.git.claim_check_config import persist_claim_check_config
from synapse_channel.git.gitclaim import GitRunner, _default_git_runner
from synapse_channel.git.githook import install_hooks
from synapse_channel.terminal_text import shell_long_option

SCAFFOLD_DIR = ".synapse"
"""Repository-relative directory the onboarding scaffold is written into."""

SCAFFOLD_FILE = "git-claims.md"
"""Filename of the onboarding guide within :data:`SCAFFOLD_DIR`."""

SCAFFOLD_MARKER = "<!-- synapse-git-init -->"
"""Marker identifying a scaffold this tool wrote, so a user's own file is never clobbered."""


def repo_toplevel(*, runner: GitRunner = _default_git_runner) -> Path:
    """Return the repository's working-tree root via ``git rev-parse``."""
    return Path(runner(["rev-parse", "--show-toplevel"]))


def _scaffold_body(*, name: str, base_branch: str) -> str:
    """Build the onboarding guide written into ``.synapse/git-claims.md``."""
    return (
        f"{SCAFFOLD_MARKER}\n"
        "# Claim-aware git in this repository\n\n"
        "Several agents can edit this repo at once without clobbering each other by\n"
        "declaring a file-scope claim before they start and releasing it on merge.\n"
        "`synapse git-init` set this up; the conventions below keep it frictionless.\n\n"
        "## Branch naming\n\n"
        f"Branch one claim per unit of work off `{base_branch}`:\n\n"
        "```\n"
        "claim/<task-id>      # e.g. claim/auth-refactor\n"
        "```\n\n"
        "so the branch name and the claim's task id line up, and a reviewer can see\n"
        "which branch owns which scope.\n\n"
        "## One worktree per claim (recommended)\n\n"
        "Run parallel claims in separate git worktrees, so each has its own checkout\n"
        "and the file-scope claims never overlap on disk:\n\n"
        "```\n"
        "git worktree add ../<repo>-<task-id> -b claim/<task-id>\n"
        "cd ../<repo>-<task-id>\n"
        "synapse git-init --name <exact-seat-identity>\n"
        "```\n\n"
        "The staged gate stores its identity and hub settings in Git's per-worktree\n"
        "config, so one seat cannot overwrite another. Run `git-init` once in each\n"
        "worktree. The auto-release hooks are repository-wide but read that same\n"
        "per-worktree identity at commit time, so each worktree releases its own\n"
        "claims even when several seats share the checkout; `--auto-release-on manual`\n"
        "stays available whenever you would rather release explicitly.\n\n"
        "## Claiming and releasing\n\n"
        "```\n"
        f"synapse git-claim {shell_long_option('--paths', 'src/area')} "
        f"{shell_long_option('--name', name)} -- <task-id>\n"
        "# ... edit, commit ...\n"
        "# the post-commit / post-merge hook auto-releases the claim (installed here)\n"
        "```\n\n"
        "The hub only ever sees an ordinary claim and release — all the git awareness\n"
        "is client-side. Run `synapse conflicts` to predict cross-branch overlaps, and\n"
        "`synapse git-hook test` to confirm the hooks are healthy.\n"
        "\n## Commit-time claim coverage\n\n"
        "This command verifies the real staged index before a commit:\n\n"
        "```\n"
        "synapse git-claim-check --staged\n"
        "```\n\n"
        "With the pre-commit framework, use an always-run local hook with\n"
        "`pass_filenames: false`; the checker reads Git directly. `git-init` installs\n"
        "only the post-commit and post-merge auto-release hooks.\n"
    )


def init_repo(
    *,
    uri: str,
    name: str,
    base_branch: str = "main",
    token_file: str | None = None,
    synapse_bin: str | None = None,
    runner: GitRunner = _default_git_runner,
    hooks_dir: Path | None = None,
    scaffold_dir: Path | None = None,
) -> list[str]:
    """Install the auto-release hooks and write the onboarding scaffold.

    Idempotent: a re-run overwrites a scaffold this tool wrote (carrying
    :data:`SCAFFOLD_MARKER`) but leaves a user's own ``.synapse/git-claims.md``
    untouched and reported — the same contract as the hooks.

    Parameters
    ----------
    uri, name : str
        Hub URI and agent identity recorded for this worktree and baked into the
        installed hooks as the fallback; the hooks prefer the current worktree's
        recorded identity at run time.
    base_branch : str, optional
        The integration branch the convention branches off. Defaults to ``main``.
    token_file : str or None, optional
        A token file passed through to the hooks for a secured hub.
    synapse_bin : str or None, optional
        Path to the ``synapse`` executable baked into the hooks; resolved from the
        current ``PATH`` when ``None``.
    runner : GitRunner, optional
        The git executor; injectable for testing.
    hooks_dir, scaffold_dir : Path or None, optional
        Override the hooks / scaffold directories; resolved from git when ``None``.

    Returns
    -------
    list[str]
        One human-readable line per hook and the scaffold file (installed,
        updated, or skipped).
    """
    results, canonical_token = persist_claim_check_config(
        uri=uri,
        name=name,
        token_file=token_file,
        runner=runner,
    )
    results.extend(
        install_hooks(
            uri=uri,
            name=name,
            token_file=canonical_token,
            synapse_bin=synapse_bin,
            runner=runner,
            hooks_dir=hooks_dir,
        )
    )
    base = scaffold_dir if scaffold_dir is not None else repo_toplevel(runner=runner) / SCAFFOLD_DIR
    base.mkdir(parents=True, exist_ok=True)
    guide = base / SCAFFOLD_FILE
    if guide.exists() and SCAFFOLD_MARKER not in guide.read_text(encoding="utf-8", errors="ignore"):
        results.append(f"skipped {SCAFFOLD_DIR}/{SCAFFOLD_FILE}: a non-Synapse file already exists")
    else:
        existed = guide.exists()
        guide.write_text(_scaffold_body(name=name, base_branch=base_branch), encoding="utf-8")
        verb = "updated" if existed else "wrote"
        results.append(
            f"{verb} {SCAFFOLD_DIR}/{SCAFFOLD_FILE} (branch convention + worktree guide)"
        )
    return results
