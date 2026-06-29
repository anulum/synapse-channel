<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Managed GitHub App (design)

This page is a **design**, not a feature. It pins down what a managed GitHub App
for cross-PR file-scope conflict prediction would be, and — more importantly —
where the line falls between the local-first core and the managed layer, so the
boundary is decided before any hosted code is written. It is **not implemented**,
and it is intentionally gated on a local adoption signal: there is no reason to
host conflict prediction before teams are coordinating locally with it.

## The idea

When several open pull requests touch overlapping files, they are on a collision
course that neither author can see until the merge conflict lands. The hub
already answers this question for live agents: an active claim names the files an
agent is working on, and the dashboard surfaces advisory branch-conflict
candidates from overlapping claims. A GitHub App would answer the same question
for pull requests — comparing the file sets of open PRs against one another (and
against live claims) and posting a check or comment when two PRs are predicted to
collide, before either is merged.

## The boundary: core logic vs managed layer

The conflict prediction itself is **core, local-first logic** and already exists:
`synapse_channel.git.gitconflict.find_conflicts` derives conflict candidates from
overlapping path sets, and `dashboard_fleet` already uses it to surface branch
conflicts from active claims. A GitHub App reuses that function unchanged; it adds
no new prediction logic to the core.

Everything that makes it *managed* stays out of the local core package:

| Local core (`synapse_channel.core`, today) | Managed layer (a separate service, not in core) |
| --- | --- |
| Path-set overlap and conflict candidate derivation | GitHub webhook intake and event verification |
| Claim and lease model | GitHub App identity, installation tokens, auth |
| Deterministic, offline, single-dependency | Checks/Comments API calls, rate-limit handling |
| Runs on the developer's machine | Hosting, tenancy, persistence, billing, ops |

This split is the point: the value (predicting the collision) is computed by code
that runs anywhere, and the hosted app is a thin adapter that maps PR file sets
onto that function and renders the result as a GitHub check. The local core never
gains GitHub-specific dependencies, webhooks, or hosted state.

## Sketch of operation

1. The App subscribes to pull-request events on an installed repository.
2. On a PR open or synchronise, it reads the changed file paths for every open PR.
3. It calls the core conflict finder over those path sets (and optionally over
   live hub claims, when a hub is reachable) to find predicted collisions.
4. It posts a neutral check — advisory, never blocking — naming the other PR and
   the overlapping paths, so authors can coordinate before merging.

The check is **advisory**: like the rest of Synapse's governance surface, it
informs and records rather than enforcing at the merge gate.

## Why it is gated

Building and hosting this before teams coordinate locally would invert the
adoption order: the App is only useful once file-scope claims are part of a team's
workflow. The trigger to build it is a local adoption signal, not a date. Until
then, this design exists so that when the signal comes, the boundary is already
settled and no managed concern leaks into the local core.

## Boundaries

- **Not implemented.** No webhook intake, no GitHub App, no hosted state exists.
- **Core stays local-first.** Conflict prediction reuses the existing core finder;
  the managed layer is a separate service and never adds GitHub or hosting
  dependencies to `synapse_channel.core`.
- **Advisory only.** The check informs; it does not block merges or replace review.
- **Gated on adoption.** It is built after local adoption, not before.
