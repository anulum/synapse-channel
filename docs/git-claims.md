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
| `--module`, `--symbol`, `--api`, `--source`, `--test`, `--generated`, `--migration` | Resolve semantic selectors locally. Symbol and API selectors use a synthetic descendant scope; the other selectors and companion tests/generated outputs remain whole-file paths. |
| `--diff-base`, `--diff-head`, `--diff-path` | Infer conservative symbol scopes from a tracked Git diff. `--diff-head` is optional; without it the base is compared with the working tree. Repeat `--diff-path` to limit the diff. |
| `--semantic-evidence-json` | Write receipt-ready selector and diff evidence JSON under the git root, or to an absolute path. |
| `--base` | The branch the work merges back into (default: `main`). |
| `--auto-release-on` | The release trigger recorded on the claim: `manual`, `commit`, or `merge` (default `merge`). |

Claim paths are coordination scopes, not filesystem reads. Use normal
repository-relative paths such as `src/auth.py` or `docs/cli.md` for narrow
claims. Absolute paths and any path containing `..` are treated as traversal-like
declarations and widen to the whole worktree. That conservative fallback may
over-claim, but it does not under-claim and miss a real conflict.

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

- two different function descendants in one source file can coexist;
- a class scope conflicts with its methods;
- a whole-file or parent-directory claim conflicts with every symbol below it.

Incomplete evidence always widens. Additions, deletions, file renames or copies,
mode-only changes, unsupported languages, syntax-error trees, oversized or
non-regular sources, module-level changed lines, and unsafe declaration names
produce a whole-file claim. Owning tests and generated outputs also remain
whole-file companions. This may block more work, but it cannot silently omit a
known conflict.

The hub receives only canonical path strings; `.synapse-symbol` is a reserved
coordination segment, not a filesystem lookup or a new wire field. Evidence JSON
records each narrowing or widening decision, but tree-sitter output is planning
evidence, not proof that a change is correct or complete.

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

A git-scoped claim is an ordinary claim with one extra field. The hub deserialises
that field for storage and display but runs no git and reads no filesystem. Even
symbol claims are ordinary canonical paths interpreted by the existing ancestry
algebra; parsing and diff resolution stay entirely client-side. Resist any
temptation to move git execution into the hub: the git-agnostic hub is the whole
local-first guarantee.
