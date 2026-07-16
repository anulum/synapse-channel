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
# equivalent for structured argv builders:
synapse git-claim --task-id TASK-1 --paths src/auth.py --base main
```

The branch is resolved locally with `git rev-parse --abbrev-ref HEAD` and sent on
an ordinary claim, carrying the branch, the base it will merge into, and the
declared auto-release policy. The repository root is resolved too (with
`git rev-parse --show-toplevel` followed by strict OS canonicalisation) and set
as the claim's worktree. A git-claim is therefore isolated to its own repository:
two repositories that declare identically named paths never contend, while two
claims in the *same* repository still detect an overlap. `synapse state` then
shows the branch alongside the claim:

```
Active claims (1):
  TASK-1 [claimed] owner=USER paths=src/auth.py checkpoint=- git=feature/x->main
```

### Options

| Option | Meaning |
|---|---|
| `--paths` | File-scope path the claim intends to touch (repeatable). |
| `--module`, `--symbol`, `--api`, `--source`, `--test`, `--generated`, `--migration` | Resolve semantic selectors locally. Symbol and API selectors use a synthetic descendant scope; the other selectors and companion tests/generated outputs remain whole-file paths. |
| `--diff-base`, `--diff-head`, `--diff-path` | Infer conservative symbol scopes from a tracked Git diff. `--diff-head` is optional; without it the base is compared with the working tree. Repeat `--diff-path` to limit the diff. |
| `--semantic-evidence-json` | Write receipt-ready selector and diff evidence JSON under the git root, or to an absolute path. |
| `--base` | The branch the work merges back into (default: `main`). |
| `--auto-release-on` | The release trigger recorded on the claim: `manual`, `commit`, or `merge` (default `merge`). |

Use normal repository-relative display paths such as `src/auth.py` or
`docs/cli.md` for narrow claims. `git-claim` binds each display to a local,
versioned identity before sending it: Git-index component spelling, the strictly
resolved filesystem-relative path, Unicode NFC, the worktree's actual case
policy, and a device/object key when the target already exists. Symlinks,
junctions, hard links, and Windows 8.3 aliases therefore cannot create a second
claim for the same object. The human-readable `paths` remain unchanged for state
views and denial messages.

Device/object keys are local values, so the resolver also sends an opaque hashed
host namespace and the canonical worktree root's object key. Object equality is
trusted only when both values match; coincident inode numbers from different
hosts cannot widen conflicts. Object equality is deliberately conflict-only: it
can deny a competing hard-link claim, but cannot authorize an edit or release a
lease after the filesystem object may have changed.

Identity derivation is fail closed. Absolute or parent-escaping paths, broken or
unreadable aliases, aliases outside the worktree, ambiguous case-insensitive Git
index entries, non-canonical display spelling, an unprovable case policy, and
identity/display mismatches are refused before the claim is
sent. Missing final components remain claimable after their nearest existing
ancestor has resolved inside the worktree. Case is folded only when the detected
filesystem is case-insensitive; Linux/ext4 claims remain case-sensitive.

Auto-release repeats the same local identity derivation. If that derivation
fails, or a snapshot carries a present-invalid or scope-misaligned identity, the
non-blocking hook returns success without releasing anything.

The hub validates and compares identity values but never reads the worktree.
Identity-aware claims compare conservatively with a legacy peer by projecting
that peer's display paths under the known filesystem policy. Two legacy peers
retain their historical literal-path behavior, so upgrade all Git-aware claim
producers to close alias gaps across an entire fleet.

The `--auto-release-on` value is the policy stored with the claim; a client-side
git hook enacts it so a finished branch frees its claim without a manual step.

Use either the positional `TASK-1` form or `--task-id TASK-1`, not both. The named
form exists for scripts and agent adapters that assemble command arguments from
structured fields. `synapse git-release` is reserved for the installed hooks and
auto-detects releasable claims from the git diff; for a manual release, use
`synapse release <task> --name <owner>`.

## Claim a semantic selector

Use the semantic flags when the work is naturally described by a module, symbol,
API object, source path, owning test, generated artefact, or migration:

```bash
synapse git-claim TASK-RECEIPTS \
  --symbol synapse_channel.core.receipts.build_release_receipt \
  --semantic-evidence-json semantic-evidence.json
```

The command resolves the current git root first and runs the same deterministic
resolver as `python tools/semantic_claims.py`. A symbol or API selector encodes
the source and qualified symbol as a synthetic descendant such as
`src/pkg/worker.py/.synapse-symbol/Worker/run`; likely owning tests and generated
outputs remain ordinary whole-file companion paths. Module, source, test,
generated, and migration selectors also remain whole-file paths. All derived
paths are merged with explicit `--paths`. Selector text and evidence stay local
unless you choose `--semantic-evidence-json` and later attach that JSON to a
release receipt.

## Claim a Git diff at symbol scope

Install the local parser bindings, then compare a base revision with the working
tree:

```bash
pip install 'synapse-channel[semantic]'
python tools/semantic_diff_claims.py --base main --claim-args
synapse git-claim TASK-WORKER \
  --diff-base main \
  --diff-path src/pkg/worker.py \
  --semantic-evidence-json semantic-evidence.json
```

Use `--diff-head HEAD` for a committed comparison. The standalone tool accepts
the equivalent `--head` and repeatable `--path` flags. Python/PYI,
JavaScript/JSX, TypeScript/TSX, Rust, and Go are supported by locally installed
upstream grammar wheels. Parser imports are lazy, and neither command downloads
a grammar or contacts a service at runtime.

For an ordinary modified file, zero-context hunks are mapped on both the old and
new source side. Every changed line must fall inside a named declaration. The
smallest enclosing declaration becomes the claim path; renaming a declaration
claims both old and new names. The existing path ancestry rule then provides the
enforcement:

- two different function descendants in one source file can coexist in hub
  state and can be edited safely from isolated worktrees;
- a class scope conflicts with its methods;
- a whole-file or parent-directory claim conflicts with every symbol below it.

Provider guards do not pretend a shared physical file is isolated. A precise
edit tool may start from one semantic claim only when every semantic claim for
that source in the exact worktree and branch belongs to the same editable owner.
A sibling claim held by another owner in the same shared worktree makes the
pre-edit decision ambiguous and is denied. Whole-file writers and patch tools
always require a literal whole-file or parent claim. The staged index check is
the authoritative post-edit proof described below.

Claim the smallest coherent ownership boundary, but do not under-claim a tool's
actual mutation surface: whole-file writers require the file or an ancestor,
while a verified precise edit may use the exact semantic declaration scope.

Incomplete evidence always widens. Additions, deletions, file renames or copies,
mode-only changes, unsupported languages, syntax-error trees, oversized or
non-regular sources, module-level changed lines, and unsafe declaration names
produce a whole-file claim. Owning tests and generated outputs also remain
whole-file companions. This may block more work, but it cannot silently omit a
known scope or miss a real conflict.

The hub receives only canonical path strings; `.synapse-symbol` is a reserved
coordination segment, not a filesystem lookup or a new wire field. Evidence JSON
records each narrowing or widening decision, but tree-sitter output is planning
evidence, not proof that a change is correct or complete.

Filesystem-canonical identity resolves a semantic descendant through its physical
source before rebuilding the synthetic suffix. Hard-linked source aliases use a
private object-derived comparison anchor, so the same declaration and declaration
ancestry still contend across aliases while sibling declarations remain separate.
The anchor is comparison material only and never becomes a repository path.

## Auto-release on commit or merge

Install the git hooks once per repository so finished work frees its claims with
no manual step:

```bash
synapse git-hook install
```

This writes a `post-commit` and a `post-merge` hook that call `synapse
git-release`. After each commit or merge, `git-release` resolves the changed
files locally (`git diff-tree` for a commit, `git diff ORIG_HEAD HEAD` for a
merge), then filters claims by exact owner, canonical worktree, attached branch,
and `--auto-release-on` trigger. Whole-file claims release from the physical
change. Semantic claims release only when the committed `HEAD^..HEAD` (or
`ORIG_HEAD..HEAD` merge) diff proves a matching `.synapse-symbol` path.
Ambiguous, unsupported, unreadable, or parser-unavailable semantic evidence
retains the symbol claim for explicit manual release instead of releasing it
from a mere physical-file match. Add `--name` (and `--token-file` for a secured
hub) to match the identity your agent claims under; a pre-existing hook from
anything else is left untouched.

The hub never parses Git or source: it receives only an ordinary release. The
hook never blocks a commit — an unreachable hub or no matching claim is simply
a no-op.

## Block commits whose staged paths are not claimed

Run the read-only staged gate directly:

```bash
synapse git-init --name project/agent
synapse git-claim-check --staged
```

The checker reads the authoritative index with `git diff --cached --name-status
-z --find-renames --find-copies`; it does not trust filenames supplied by a hook.
Adds, modifications, deletions, type changes, and unmerged paths are checked.
Copies and renames check both the old and new names. Malformed, absolute,
parent-escaping, or truncated records fail closed.

When at least one active claim in the exact worktree and branch uses a
`.synapse-symbol` path for a staged source, the checker also resolves `HEAD`
versus the index with the optional local semantic parser. An ordinary
modification is projected to the exact named declarations touched on both old
and new sides. The projected symbol paths — not the physical source name — are
then matched against claims. A change to an unclaimed sibling symbol is denied.
Any incomplete mapping widens to the physical source, so a symbol-only claim
cannot authorise module-level edits, additions, deletions, renames, unsupported
languages, invalid syntax, or ambiguous hunks. Parser/import/Git failures deny;
they never downgrade a semantic claim check to a permissive file match.

An empty staged index returns success without resolving identity, reading a
token, or connecting to a hub. For a non-empty index, every covering claim must:

- match the canonical repository root and current non-detached branch;
- belong to one exact identity and be in `claimed` or `working` state; and
- cover every projected staged path, either by exact semantic or literal scope,
  by directory ancestry, or through the existing empty-path whole-worktree
  meaning.
- cover every staged path by its display-bound canonical Git identity, directory
  ancestry, or the empty-path whole-worktree meaning. Filesystem aliases and
  object ids remain conflict-denial evidence, never authorization.

A `PROJECT:git` serialization lock cannot satisfy this check: it has no canonical
worktree, branch, and path ownership. The checker never acquires, widens, renews,
or releases a claim. Its bounded denial lists all ordinary uncovered paths so the
operator can acquire the exact claim and retry.

Identity resolution is deliberately strict. Populated sources must agree in this
order: explicit `--name`, worktree-scoped `synapse.identity`, then an agreeing
`SYN_PROJECT` plus `SYN_IDENTITY` pair. A bare ambient identity, placeholder such
as `USER`, disagreement, or detached HEAD is refused. `git-init` enables Git's
official `extensions.worktreeConfig` support and persists `synapse.identity` and
`synapse.uri` in the current worktree's config; `--token-file` persists only its
canonical path as `synapse.tokenFile`, never token content. Legacy repository-local
values remain readable until `git-init` is rerun, then they are removed so another
linked worktree fails closed instead of inheriting the wrong seat.

Run `synapse git-init --name <exact-seat>` once inside every linked worktree. Git
requires `core.worktree` and `core.bare=true` to be moved out of the shared config
before enabling per-worktree config; Synapse detects that uncommon layout and
refuses with the upstream migration instruction instead of guessing. The staged
gate is worktree-safe after this migration. The auto-release hook scripts are
repository-wide — git worktrees share one hooks directory — but they now pass
`git-release --resolve-identity`, so at commit time each reads the
`synapse.identity` / `synapse.uri` / `synapse.tokenFile` recorded for the worktree
that produced the commit and releases that seat's claims. Mixed-identity linked
worktrees therefore auto-release correctly; the identity baked at install time is
only the fallback for a worktree with no recorded identity, and
`--auto-release-on manual` remains available whenever explicit release is preferred.

This repository dogfoods the gate through the pre-commit framework:

```yaml
- id: staged-claim-coverage
  name: every staged path has an owned Synapse claim
  entry: python -m synapse_channel.cli git-claim-check --staged
  language: system
  stages: [pre-commit]
  always_run: true
  pass_filenames: false
```

`git-init` does **not** splice or overwrite a pre-commit hook. It installs only
the non-blocking `post-commit` and `post-merge` auto-release hooks and writes the
local guide/config. Repositories using pre-commit can add the stanza above;
repositories with another hook manager must compose the standalone command
themselves.

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
*different* branches with the same merge base whose declared paths overlap — two
agents about to edit the same files on branches that will merge into one target.
Claims with different bases are ignored because their branch-integration risk is
not the same merge point. `--check-diff` refines the prediction against each
branch's actual `git diff base...branch`, so only files both branches have really
changed are reported. A directory-scoped claim such as `--paths src` matches
changed files below that directory, and a whole-worktree claim is refined to the
common changed files when both branch diffs are available. A branch that is not
checked out locally is kept as a conservative warning rather than dropped.

```
Predicted conflicts (1):
  A@feature/x vs B@feature/y (both -> main): src/auth.py
```

`synapse conflicts` exits `0` when nothing is predicted, `2` when a conflict is,
and `1` if the hub is unreachable — so a gate like `synapse conflicts && git
merge feature/x` proceeds only on a clean, successfully checked result.

The prediction is computed entirely on the client from the ordinary state
snapshot; the hub runs no git.

For semantic merge-risk beyond direct path overlap, run the import graph
merge-risk radar against changed files or a branch diff:

```bash
python tools/import_merge_risk.py --changed src/auth.py --claimed src/session.py --check
python tools/import_merge_risk.py --base main --head HEAD --claims-json claims.json --json
```

The radar combines package-local Python import edges, CODEOWNERS, and mapped test
owners with the changed and claimed paths. It is advisory and client-side only;
use it to decide whether to coordinate, expand tests, or include more evidence in
the release receipt.

## What stays out of the hub

A git-scoped claim is an ordinary claim with opaque `git` metadata plus an
optional additive `path_identity`. The hub validates, persists, replays, and
compares that identity but runs no git and reads no filesystem. Even symbol
claims are ordinary canonical paths interpreted by the existing ancestry
algebra; parsing, diff resolution, and filesystem identity derivation stay
entirely client-side. Resist any temptation to move git execution into the hub:
the git-agnostic hub is the whole local-first guarantee.
