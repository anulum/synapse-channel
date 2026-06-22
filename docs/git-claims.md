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
declared auto-release policy. `synapse state` then shows the branch alongside the
claim:

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

## What stays out of the hub

A git-scoped claim is an ordinary claim with one extra field. The hub deserialises
that field for storage and display but runs no git and reads no filesystem — the
branch is resolved and acted on entirely on the client. Resist any temptation to
move git execution into the hub: the git-agnostic hub is the whole local-first
guarantee.
