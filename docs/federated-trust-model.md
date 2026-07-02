<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Federated trust model design

Synapse is local-first and operator-managed: one hub, one operator, loopback by
default, with optional shared tokens, file permissions, per-agent identity,
signed events, mutual TLS, and release receipts layered on top. This document
designs how those single-domain primitives could compose into a **federated**
trust model — multiple independently operated Synapse domains that let scoped
agents and evidence cross an organisational boundary without surrendering
local-first control.

It is a design, not an implementation. The runtime primitives it builds on exist
and are tested; the federation layer that joins domains does not exist yet. The
goal is to pin the trust boundaries before any cross-organisation code is
written, so federation never silently widens what a single domain already
enforces.

## Runtime status

These single-domain primitives are implemented and are the building blocks a
federation layer would compose — it would add no new trust root of its own:

- **Identity and ACLs** — per-agent identity inventory and deny-by-default ACL
  evaluation (`synapse identity`, `synapse acl`), today in shadow/observe mode.
  See [identity and ACL](identity-and-acl.md).
- **Signed events** — `EventSignatureKey` / `EventSignatureTrustBundle` verify
  Ed25519-signed coordination frames with sender, project, expiry, replay, and
  revocation checks. See [signed events and mTLS](signed-events-mtls.md).
- **Mutual TLS peers** — `MTLSPeerTrustBundle` verifies trusted-peer certificate
  pins, project scope, signing-key scope, and revocation for multi-host hubs.
- **Release receipts** — bounded, evidence-bearing receipts with epistemic
  status, carried on `release_granted` and recorded on the board.
- **Signed capability cards** (design) — a planned card-signing profile, see
  [signed capability cards](signed-capability-cards.md).

The deny-by-default **policy bundle** has shipped in `core/federation.py`: a
`FederationPeer` records, per remote domain, the local namespaces it may address, the
accepted certificate pins and event-signing key ids, the bounded local scope
(`ScopeGrant` verb/namespace pairs) its subjects map to, and an expiry plus revocation;
`FederationBundle.authorise` returns a deny-by-default decision (unknown domain, revoked,
expired, namespace not granted, key not accepted, pin not accepted, in that order), and
`compose_cross_domain` joins it with the external mutual TLS, signature, and ACL results
so a frame any layer rejects is rejected. It owns no crypto and adds no trust root.

That policy is now **composed into the live runtime**, on both surfaces, deny-closed and
opt-in:

- **Hub-to-hub** — the multi-hub event-log pull and cross-hub claim forwarding gate one
  hub serving or forwarding to another against the peering and the live certificate pin
  (`MultiHubServingPolicy`, `authorise_multihub_pull`).
- **Agent frames** — a hub started with `synapse hub --federation-store FILE` composes the
  imported peerings into its per-frame authorisation: a frame whose verified Ed25519
  signing key and live certificate pin resolve to one peered domain (`resolve_domain`) is
  authorised against that peering's bounded scope (`scope_authorises`), composed with
  mutual TLS, the event signature, and the mapped scope, instead of the local ACL. A frame
  resolving to no peer stays local only when its signing key is unpeered; a peered key that
  cannot be bound to a single peering is denied. Federation only binds authority on a hub
  that also runs
  `--require-message-auth`; without it a cross-domain frame is refused, since its signing
  key is not verified. The hub therefore refuses to *start* when the store's peerings grant
  cross-domain scope but `--require-message-auth` is not set — the configuration claims an
  authorisation it can never perform. Two configurations remain valid without it: a store
  whose peerings grant no enforceable scope (revoked, expired, credential-less, or
  scope-less — observe-only by construction, started with a warning), and a scope-granting
  store started with `--federation-observe-only`, the operator's declared intent to load
  the peerings for diagnostics and deny-closed refusal only. Declaring
  `--federation-observe-only` alongside `--require-message-auth` is a contradiction and is
  likewise refused. With no store the live path is unchanged.

A frame that carries a signing key over a pinned connection yet resolves to no peering is
disposed of by whether the *key* is peered. A key no peering enrols is an ordinary local
frame and is handled locally; because a peering whose signing-key enrolment is stale fails
the same way, the hub logs a warning when the certificate pin alone is enrolled
(`diagnose_unresolved_domain`) so a wrong key id does not go silent, and stays quiet when
neither credential is enrolled. A **peered** key that fails to resolve is denied outright
(`peer_domain_unresolved`), exactly as a peered key on an unpinnable connection is
(`peer_certificate_unavailable`): the frame claims cross-domain authority that binds to the
verified key-and-pin pair, so a stale or foreign certificate, credentials split across
peerings, or an ambiguous pair two peerings both claim must never downgrade it to local
processing. The operator log names the diagnosis; the wire reply carries only the reason.

Bundle *transport* over the network has shipped: a hub serves its own operator-authored
bundle material (`synapse hub --federation-offer`), and a peer operator pulls it
(`synapse federation fetch`) instead of moving a file by hand. What stays out-of-band —
deliberately and permanently — is the *trust decision*: both operators compare the bundle
fingerprint over an independent channel (`synapse federation offer` prints the same block
the fetch displays) and only then import explicitly (`synapse federation import
--confirmed-by`). The hub does not auto-discover or negotiate trust, and there is no
trust-on-first-use. This document is the boundary specification for that ceremony.

## Trust domains

A **trust domain** is one operator's Synapse deployment: a hub (or a set of
mutually-trusting mTLS hubs), the project namespaces it owns, the agent
identities it issues, and the signing keys and certificate pins it manages. A
domain is the unit of federation and the unit of revocation. Every federated
statement is scoped to a named domain so that a claim, signature, or receipt is
always attributable to exactly one issuing operator.

A domain is identified by a stable domain id and the set of project namespaces it
is authoritative for. A domain is authoritative only for its own namespaces: a
remote domain may *assert* facts about its agents and tasks, but it is never
authoritative for another domain's namespaces, and a verifier always resolves
authority by the issuing domain, never by the asserted content.

## Cross-domain peer federation

Federation extends the existing single-host `MTLSPeerTrustBundle` and
`EventSignatureTrustBundle` from "trusted peer hosts" to "trusted peer domains".
A federation bundle would record, per remote domain:

- the remote domain id and the project namespaces it is allowed to address
  locally (deny-by-default; a remote domain addresses nothing until granted);
- accepted certificate pins for the remote hub(s);
- accepted Ed25519 event-signing key ids for the remote domain;
- a local-side scope mapping that translates remote subjects into locally
  meaningful, bounded permissions;
- expiry and revocation state for the whole peering.

Verification composes the primitives that already exist: a cross-domain frame
must satisfy mutual TLS peer verification (pin + peer scope) **and** event
signature verification (key id + sender + project + replay + expiry +
revocation) **and** the local ACL for the mapped scope. Federation never
weakens any single check; it only refuses to widen one. A frame that any layer
rejects is rejected.

## Trust-bundle exchange and provenance

Federation needs verification keys and certificate pins to move between domains,
but Synapse is not a certificate authority and must not become one. The trust
decision is therefore **out-of-band and operator-confirmed**: operators confirm
domain bundles through their existing trusted channel (a call, a ticket, a
key-signing exchange) and import them explicitly. `synapse federation import`
(shipped) records the bundle with its provenance — who provided it, when, and which
operator confirmed it (a required `--confirmed-by`) — so every federated trust
relationship is auditable back to a human decision, not auto-discovered from the
network; `synapse federation list` shows the imported peerings and `synapse federation
revoke` retires one while keeping its audit record. There is no automatic
trust-on-first-use and no network-driven trust root.

The bundle *bytes* may move over the wire (shipped): a hub started with
`--federation-offer FILE` answers a peer operator's `synapse federation fetch` with its
own published bundle material, and both sides print an identical fingerprint block —
the domain id, signing key ids, certificate pins, and a whole-bundle SHA-256 — computed
by the same code (`synapse federation offer` on the offering side). The fetched file is
untrusted until the operators compare the bundle fingerprint over an independent
channel, the SSH-known-hosts ceremony; the fetch never imports, and the import command
records the fetch URI as the peering's provenance source. A fingerprint over the whole
canonical bundle means an in-path alteration of *any* policy content — an added
namespace or scope grant as much as a swapped key — changes the value the operators
read to each other.

## Scoped cross-domain authorisation

A remote domain's agents never inherit local permissions. The federation bundle
maps a remote subject to a **bounded local scope**: specific verbs (for example,
read board, post chat to a shared channel, submit a receipt) over specific
project namespaces, deny-by-default. Cross-domain authorisation reuses the local
ACL engine — the mapped scope is evaluated exactly like a local subject's — so
there is one authorisation path, not a parallel federated one. Private-channel
membership and at-rest/payload encryption boundaries are unchanged: a remote
subject is a member of nothing until a local membership grant says so.

## Evidence and receipt portability

Release receipts are the natural portable evidence across domains because they
already carry bounded, self-describing fields and an epistemic status. A
federated verifier can read a remote receipt and check what is *verifiable*
(signature, key id, declared evidence, freshness) versus what is merely
*asserted* (the producer's confidence). Crucially, a receipt remains advisory
evidence across a domain boundary exactly as it is locally: it documents claimed
checks, it does not certify sufficiency, and a remote receipt never auto-approves
a local merge or release. Portability widens who can read the evidence, not what
the evidence proves.

## Revocation and incident propagation

Revocation is domain-scoped and explicit. A domain revokes its own keys, pins,
or peerings; federated peers learn of revocation through the same out-of-band
exchange that established the peering, and a future runtime would surface a
revoked peering the way the local trust bundles already surface revoked keys and
peers. Because every federated statement is attributable to one issuing domain,
incident response can scope blast radius to a single domain and its explicit
peerings rather than an implicit transitive web.

## Relationship to other designs

This model is the composition layer above the shipped and designed security
profiles. It depends on [identity and ACL](identity-and-acl.md) for the local
authorisation path, [signed events and mTLS](signed-events-mtls.md) for
cross-domain authentication and integrity, [signed capability
cards](signed-capability-cards.md) for portable capability provenance, and the
[agent trust graph](agent-trust-graph.md) for evidence aggregation. It does not
replace any of them, and it adds no trust root they do not already define.

## Boundaries

The deny-by-default policy and its composition into the live hub-to-hub and agent-frame
paths have shipped (see Runtime status), and so has the bundle-exchange transport
(offer/fetch); the trust decision itself is **out-of-band by design** and stays so.
These boundaries hold regardless and are never relaxed by what ships:

- It is **not a certificate authority or PKI**: it distributes no keys, issues no
  certificates, and performs no automatic trust discovery. Trust roots are
  operator-confirmed and out-of-band.
- It does **not** authorise untrusted organisations. Federation is deny-by-default
  between explicitly peered, operator-managed domains only.
- It does **not** replace per-agent identity, signed events, mutual TLS, ACLs, or
  receipts — it composes them, and it never weakens a check any of them perform.
- It does **not** make a release receipt anything more than advisory evidence
  across a domain boundary, and a remote receipt never auto-approves a local
  action.
- It does **not** change the local-first default: a domain that imports no
  federation bundle behaves exactly as a single local deployment, and federation
  adds no network-reachable trust surface unless an operator explicitly enables
  and scopes it.
