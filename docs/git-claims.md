<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Git-native claims

A work claim can be scoped to the git branch the work happens on. The hub stays
**git-agnostic** — it never runs git and never reads a filesystem — so all git
resolution happens client-side and the branch travels as **opaque metadata** on
an ordinary claim. The hub stores it, replays it from the durable log on restart,
and shows it in the state view, but it never acts on it. That keeps the
local-first, single-dependency guarantee intact while making claims branch-aware.

## Claim on the current branch

```bash
synapse git-claim TASK-1 --paths src/auth.py --base main
```

The branch is resolved locally with `git rev-parse --abbrev-ref HEAD` and sent on
an ordinary claim, carrying the branch, the base it will merge into, and the
declared auto-release policy. The repository root is resolved too (with
`git rev-parse --show-toplevel`) and set as the claim's worktree, so a git-claim is
isolated to its own repository: two repositories that declare identically-named
paths never contend, while two claims in the *same* repository still detect an
overlap. `synapse state` then shows the branch alongside the claim:

```
Active claims (1):
  TASK-1 [claimed] owner=USER paths=src/auth.py checkpoint=- git=feature/x->main
```

### Options

| Option | Meaning |
|---|---|
| `--paths` | File-scope path the claim intends to touch (repeatable). |
| `--base` | The branch the work merges back into (default: `main`). |
| `--auto-release-on` | The release trigger recorded on the claim: `manual`, `commit`, or `merge` (default `merge`). |

The `--auto-release-on` value is the policy stored with the claim; a client-side
git hook enacts it so a finished branch frees its claim without a manual step.

## Auto-release on commit or merge

Install the git hooks once per repository so finished work frees its claims with
no manual step:

```bash
synapse git-hook install
```

This writes a `post-commit` and a `post-merge` hook that call `synapse
git-release`. After each commit or merge, `git-release` resolves the changed
files locally (`git diff-tree` for a commit, `git diff ORIG_HEAD HEAD` for a
merge) and releases any claim you hold whose `--auto-release-on` matches the
trigger and whose declared paths were touched. Add `--name` (and `--token-file`
for a secured hub) to match the identity your agent claims under; a pre-existing
hook from anything else is left untouched.

The hub is never involved: it only ever receives an ordinary release, and a hook
never blocks a commit — an unreachable hub or no matching claim is simply a no-op.

### Verify the hooks (recommended for production)

Because a missing hook — or one whose baked-in `synapse` path has since moved —
fails silently at commit time, confirm the setup with:

```bash
synapse git-hook test
```

It reports whether each `post-commit`/`post-merge` hook is installed and whether
the executable it invokes still resolves, exiting non-zero on any gap. Gate your
deployment on it: this project's own CI installs the hooks in a scratch repo and
runs `git-hook test` on every push, so a regression in the install-or-resolve path
is caught before release. (`synapse git-init` installs the hooks and writes the
conventions guide in one step.)

## Predict merge conflicts

See a collision before it happens:

```bash
synapse conflicts
synapse conflicts --check-diff
```

`synapse conflicts` reads the hub's live claims and flags every pair held on
*different* branches whose declared paths overlap — two agents about to edit the
same files on branches that merge into the same base. `--check-diff` refines the
prediction against each branch's actual `git diff base...branch`, so only files a
branch has really changed are reported (a branch that is not checked out locally
is kept as a conservative warning rather than dropped).

```
Predicted conflicts (1):
  A@feature/x vs B@feature/y (both -> base): src/auth.py
```

`synapse conflicts` exits `0` when nothing is predicted, `2` when a conflict is,
and `1` if the hub is unreachable — so a gate like `synapse conflicts && git
merge feature/x` proceeds only on a clean, successfully checked result.

The prediction is computed entirely on the client from the ordinary state
snapshot; the hub runs no git.

## What stays out of the hub

A git-scoped claim is an ordinary claim with one extra field. The hub deserialises
that field for storage and display but runs no git and reads no filesystem — the
branch is resolved and acted on entirely on the client. Resist any temptation to
move git execution into the hub: the git-agnostic hub is the whole local-first
guarantee.
