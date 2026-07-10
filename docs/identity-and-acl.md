<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — identity and ACL design
-->

# Identity and ACL design

Per-agent identity and ACL enforcement grew out of a design target for
deployments that need more than shared-token mode and the caller-name model.
The core of it is now implemented (see the next section): deny-by-default ACL
enforcement is opt-in, the production waiter opts into a name-ownership lease,
and installations with `cryptography` use a zero-config trust-on-first-use
machine key. Core-only installations retain the unsigned compatibility path.
What remains a design target is listed at the end of that section. A secured
hub still admits a connection with a shared token; the layers below decide what
the admitted connection may claim to *be* and to *do*.

The goal is to bind every durable actor to an identity-bound credential, then
evaluate whether that identity may perform a requested action before the hub
mutates state or exposes scoped data. This does not replace per-message
authentication, does not replace signed events, and does not sandbox agents.

## Implemented (TOFU, shadow tools, and opt-in enforcement)

The ACL model and its evaluation are implemented in
:mod:`synapse_channel.core.identity`, :mod:`synapse_channel.core.acl`, and
:mod:`synapse_channel.core.acl_enforcement`:

- `synapse identity audit --identities <file>` loads a declared identity
  inventory and reports the ambiguities that would block an enforcement rollout:
  duplicate audit subjects, missing credentials, and seats that run more than one
  agent id.
- `synapse acl shadow --policy <file> --requests <file>` evaluates candidate
  accesses against a deny-by-default ACL policy and records the would-allow /
  would-deny decision each would receive — with the matching rule and reason —
  without ever blocking a frame. Target patterns are structured (a kind plus a
  glob value), scoped to a project namespace, across the permission vocabulary
  below.
- `synapse hub --acl-policy <file> --require-acl` turns the same deny-by-default
  evaluation into runtime enforcement: a mutating frame (chat, claim, release,
  task update, handoff, checkpoint, board, finding) is mapped to the structured
  accesses it needs and refused with an error before routing if the authenticated
  sender's identity is not allowed. Authentication stays the per-message
  authentication layer; this is the authorisation layer. Enforcement is opt-in
  and off by default, ungated verbs and read surfaces still pass, and a missing
  policy or shared-token local hub is unchanged.
- **Hub-authoritative name-ownership lease** (`core/name_ownership.py`): a name
  has exactly one owner across reconnects, not merely per socket. A registration
  that declares `lease: true` on a free name is granted an opaque `owner_lease`
  token in a directed `lease_granted` frame (the hub stores only a SHA-256
  digest); while the lease is live — its holder connected, or offline for less
  than `--lease-offline-ttl` seconds (default 3600) — any claim on the name
  must present the token or it is refused with close code
  `4016` (`name owned`), takeover flag or not. A claim presenting the token
  passes and still crosses the takeover damping (cooldown, oscillation
  quarantine). The waiter (`synapse wait` / `synapse arm`) opts in end to end:
  it persists the token per connect name under `~/synapse/owner-lease/` and
  presents it on every re-arm, so a re-arm re-takes its own `-rx` identity and
  a stranger cannot squat it in the gap. Clients that never opt in keep classic
  first-come semantics, and a pre-lease hub ignores the fields — a mixed fleet
  keeps working. A lapsed or lost token self-heals: past the offline window the
  name returns to first-come-first-owned.
- **Zero-config trust-on-first-use identity** (`machine_identity.py` +
  `core/identity_pins.py` + the trust-on-first-use posture in
  `core/hub_identity_gate.py`): with no operator input at all, the first
  connect provisions a per-machine Ed25519 keypair under
  `$XDG_DATA_HOME/synapse/identity/`, the registration is signed with it and
  carries `identity_public_key`, and the hub pins the name to the first key
  that proves it — persisted in `--identity-pins` (default
  `~/synapse/identity-pins.json`) so the pin survives hub restarts. A pinned
  name refuses a missing signature or any other key with close code `4013`
  (`identity pin mismatch`) and names the observed key id plus the governed
  recovery command; unsigned names keep classic semantics;
  `--require-identity-binding` takes precedence and keeps its fail-closed
  operator-bundle behaviour. Together with the ownership lease this closes the
  name-squatting class: the lease covers reconnect gaps with a bearer token,
  the pin covers restarts with a proof of key possession.
- **Every client verb signs uniformly**: `SynapseAgent` presents the machine
  identity by default, so any verb that connects — send, listen, arm, queries,
  the bridges — proves the same key. Before this default only `arm` and `wait`
  signed, and arming a name locked its holder out of every *other* verb under
  that name (refused `signature missing` — the 2026-07-10 incident class). An
  explicit `identity_key_path` wins over the default, `machine_identity=False`
  opts a deliberately unsigned agent out, and a core-only installation
  degrades to the unsigned path with the module's one-time warning.
- **Governed stale-pin reclaim**: `synapse identity reclaim <agent>
  --operator <identity> --expected-key-id <key> --reason <text>` asks the live
  hub to remove one exact TOFU pin. The handler always enforces an
  `identity-pin-reclaim` ACL grant on target kind `agent`, even when general
  `--require-acl` enforcement is off; the requester must itself be pinned or
  operator-bundle-bound, and a hub without `--db` refuses because it cannot
  write the mandatory audit event. The safe path accepts only an offline name
  whose ownership lease has lapsed under `--lease-offline-ttl`. An operator may
  add `--break-glass` to evict a live or still-leased holder, but that override,
  the previous key id, the operator, and the reason are recorded in a
  write-ahead `identity_pin_reclaim` audit trail and broadcast as a system
  notice. Removal is compare-and-swap on `--expected-key-id`; it never rotates
  or installs a replacement key. The next valid proof establishes a fresh
  first-use pin.

The identity namespace is taken from the resolved sender (`project/agent`). The
first credential format is now the zero-config machine key above (operator
bundles remain the multi-tenant graduation). General credential rotation and
revocation tooling, owner recovery beyond the governed stale-pin path, and
read-surface ACLs (metrics, dashboard, event-query) remain design targets.

## Identity model

An identity record should separate the stable actor from the terminal, process,
or provider session currently using it:

- **Agent id:** the stable per-agent identity used as the audit subject on
  claims, releases, messages, progress notes, capability cards, and receipt
  evidence.
- **Seat id:** the local workstation, tmux session, service unit, or operator
  seat that launched the agent. A seat can run one or more agent ids, but it
  should not silently borrow another agent's credential.
- **Project namespace:** the project or organisation prefix, such as
  `SYNAPSE-CHANNEL`, that scopes identity names, claim paths, private channels,
  metrics views, and future federation policy.
- **Credential id:** the key, certificate, or token handle that proves the
  caller is allowed to act as the agent id.
- **Audit subject:** the canonical identity string recorded by the hub after
  verification, not a self-reported display name.

The shipped credential formats are the local machine Ed25519 key and the
operator-managed Ed25519 trust bundle. A future managed credential lifecycle may
add other key or certificate handles, but any addition must support key-id
lookup, rotation, revocation, owner recovery, and diagnostics that explain which
credential failed without leaking secret material.

## ACL model

ACLs should deny by default. An admitted identity may only perform the allowed
verb on a matching target pattern inside its project namespace. The initial
permission vocabulary should stay small and auditable:

| Permission | Scope |
| --- | --- |
| `message` | Send chat, direct messages, handoffs, and checkpoints to allowed targets. |
| `claim` | Create or update claims for allowed path target patterns. |
| `release` | Release claims and publish release receipts; this is the release permission. |
| `board` | Create and update blackboard tasks or findings. |
| `metrics` | Read live or historical operational metrics; this is the metrics permission. |
| `dashboard` | Read dashboard snapshots; this is the dashboard permission. |
| `a2a` | Serve or consume Agent-to-Agent bridge data; this is the A2A permission. |
| `namespace` | Administer project namespace membership and trust material; this is the namespace permission. |
| `observe` | Receive directed messages the identity is not a party to (when private directed routing is on). Target kind `agent`. |
| `mailbox` | Replay another identity's directed backlog via a mailbox heartbeat (`mailbox_for`). Target kind `agent`. Self and `-rx` sidecars do not need a grant. |
| `role-claim` | Bind a role on the heartbeat when `--require-role-claim` is on. Target kind `role` (`<project>/<role>`). Complements the role-grant store. |
| `identity-pin-reclaim` | Remove one exact stale TOFU pin after the liveness, expected-key, requester-binding, and durable-audit gates pass. Target kind `agent`. Always enforced for this verb. |

Each rule should include an allowed verb, a target pattern, an optional channel
or project namespace constraint, and a decision reason suitable for receipts and
postmortems. Target patterns must be structured data, not ad hoc substring
checks, so a future policy engine can compare path claims, channel ids, agent
ids, and A2A endpoints consistently.

## Enforcement path

The hub should evaluate identity and ACLs at the point where a frame would change
state or reveal scoped state:

1. Resolve the connection credential to an audit subject.
2. Canonicalise the requested verb and target pattern.
3. Evaluate the identity's ACL rules with deny by default semantics.
4. Record the verification result, rule id, and reason in the event log.
5. Reject unauthorized frames before state mutation or scoped data disclosure.

Read paths need the same treatment as write paths. Metrics permission, dashboard
permission, private-channel history, A2A permission, and event-query access all
need explicit rules because they can expose sensitive coordination metadata even
when no payload is changed.

## Migration from shared-token mode

Migration should be gradual because local solo use must stay simple:

1. Inventory current sender names, `syn` identities, worker-session identities,
   service units, and shell-hook waiters.
2. Issue identity-bound credentials for known agent ids and seat ids.
3. Run a shadow mode that records which ACL rule would have matched while still
   accepting shared-token mode traffic.
4. Warn on ambiguous names, missing credentials, unscoped target patterns, and
   overbroad namespace permissions.
5. Enable enforcement per project namespace only after the operator has recovery
   credentials and rollback instructions.

Rollback should restore shared-token mode without deleting audit history. The
event log must keep both the asserted sender and the resolved audit subject so a
postmortem can explain what changed during migration.

## Relationship to other designs

- [Per-message authentication](per-message-authentication.md) authenticates
  selected frames after WebSocket connect authentication. Identity and ACLs
  decide whether the authenticated caller may perform the requested verb.
- [Signed events and mTLS](signed-events-mtls.md) make selected durable records
  tamper-evident and authenticate configured peers. Identity and ACLs decide who
  is authorized before those records are created or relayed.
- [Signed capability cards](signed-capability-cards.md) make route-relevant
  advertisements tamper-evident. Identity and ACLs decide who may advertise,
  update, project, or revoke those cards.
- [Private channels](private-channels.md) scope the intended audience. Identity
  and ACLs decide who may join, leave, read history, or publish to that channel.
- [Differential-privacy blackboard](differential-privacy-blackboard.md)
  projections can redact or perturb shared board reports. Identity and ACLs
  decide who may create board data or request those projections.
- [Agent trust graph](agent-trust-graph.md) evidence can explain routing and
  policy inputs. Identity and ACLs still decide who may act on that evidence.
- [Policy engine](policy-engine.md) can later consume identity decisions,
  release receipts, and event-log evidence, but it should not be the first layer
  that authenticates a caller.

## Boundaries

The identity-binding, trust-on-first-use, role-claim, mailbox, private-directed,
and mutating-frame ACL controls described above are implemented. They do not
encrypt payloads, replace per-message authentication, replace signed events,
replace TLS, sandbox agents, or make arbitrary provider code safe to run.

The local-first tradeoff is administrative complexity. A single-owner loopback
hub should still work with shared-token mode. Exposed deployments need explicit
credentials, broader credential rotation and revocation, deny by default ACLs,
diagnostics, and operator procedures before this opt-in runtime can be treated
as a complete multi-tenant IAM system. The reclaim verb is a recovery primitive,
not a complete credential lifecycle.
