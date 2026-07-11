<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Managed GitHub App

This page pins down what a managed GitHub App for cross-PR file-scope conflict
prediction does and — more importantly — where the line falls between the
local-first core and the managed layer. The adoption gate is lifted and stage 2
has an owner-approved, hosting-neutral build contract. Its detailed component,
data-flow, dependency, security, and verification design lives in
[`integrations/github-app/ARCHITECTURE.md`](https://github.com/anulum/synapse-channel/blob/main/integrations/github-app/ARCHITECTURE.md).
No App is registered or deployed, and the hosting decision remains owner-gated.

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
| Deterministic, offline, single-dependency | Checks API calls and rate-limit handling |
| Runs on the developer's machine | Hosting, tenancy, persistence, billing, ops |

This split is the point: the value (predicting the collision) is computed by code
that runs anywhere, and the hosted app is a thin adapter that maps PR file sets
onto that function and renders the result as a GitHub check. The local core never
gains GitHub-specific dependencies, webhooks, or hosted state.

## Sketch of operation

1. The App subscribes to pull-request events on an installed repository.
2. On a PR open or synchronise, it reads the changed file paths for every open PR.
3. It calls the core conflict finder over those path sets to find predicted
   collisions. A future host may add live-claim evidence without changing the
   PR-only contract.
4. It posts a neutral check — advisory, never blocking — naming the other PR and
   the overlapping paths, so authors can coordinate before merging.

The check is **advisory**: like the rest of Synapse's governance surface, it
informs and records rather than enforcing at the merge gate.

## Build order

The adoption-signal gate is lifted; the build proceeds smallest-hosting-first:

1. **Badge on the existing action — shipped.** The
   [SYNAPSE-protected badge](policy-engine.md#the-synapse-protected-badge)
   rides the composite `policy-check` action that already exists, needs no
   hosting, and is an honest self-declaration with a documented
   verification path.
2. **App manifest and Checks API skeleton — shipped.** The independently
   installable package under `integrations/github-app/` renders the App identity,
   verifies signed webhooks, exchanges short-lived installation credentials,
   reads bounded PR/file inventories, calls the existing conflict finder, and
   creates a neutral advisory check. It has no hosted state.
3. **Hosting decision** — where the managed layer runs; an owner decision
   tied to the enterprise packaging boundary.

## Stage 2 implementation

The skeleton is deliberately usable without pretending to be a deployment:

- `synapse-github-app-manifest` renders a private-by-default manifest with only
  `pull_requests:read`, `checks:write`, metadata read, and the `pull_request`
  event;
- raw webhook bytes are size-bounded, HMAC-SHA256 verified in constant time,
  strict-UTF-8 decoded, and depth-bounded before typed field extraction;
- App JWTs use RS256 with a nine-minute expiry and clock-skew backdating;
  installation tokens are treated as opaque and scoped back down to the two App
  permissions;
- the REST client fixes one API origin, refuses redirects, bounds every response,
  and uses real GitHub App Check Run request shapes;
- the service evaluates at most 100 open PRs and 3,000 files per PR. Incomplete
  evidence never produces a clean verdict;
- dedicated real-socket tests exercise token exchange, PR/file pagination, the
  shipped core finder, and Check Run creation. The package gate requires strict
  typing, 100% line/branch coverage, Ruff, Bandit, dependency audit, and wheel
  boundary inspection.

The full component and data-flow contract is in the linked architecture. Source
installation and the host-adapter seam are in
[`integrations/github-app/README.md`](https://github.com/anulum/synapse-channel/blob/main/integrations/github-app/README.md).

## Boundaries

- **The skeleton is implemented; the App is not registered or deployed.** No
  public installation, manifest-code conversion, hosted endpoint, tenant state,
  secret store, retry queue, billing, or operator service exists. The badge
  remains a self-declaration, not an attestation; only a deployed App observing
  check runs can change that status.
- **Core stays local-first.** Conflict prediction reuses the existing core finder;
  the managed layer is a separate service and never adds GitHub or hosting
  dependencies to `synapse_channel.core`.
- **Advisory only.** The check informs; it does not block merges or replace review.
