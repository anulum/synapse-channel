<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# A2A bridge validation receipts

The local A2A bridge is a local-first HTTP+JSON edge for Synapse. Whether it is
"production-grade" in the broad sense is not a single conformance pass/fail — it is a
**set of receipts that survive across the bridge boundary**. This page turns the
community A2A validation track into that set: the structured artifacts an independent
tester records, so anyone can reason about what was actually exercised rather than
trusting a claim.

This framing was contributed by **[Armorer Labs](https://github.com/armorer-labs)** in
the [A2A validation discussion](https://github.com/anulum/synapse-channel/discussions/20);
it is adopted here as the project's validation template. Two rules run through it:

- **Separate protocol compatibility from operational safety.** A bridge can be
  A2A-shaped and still be unsafe to expose behind a reverse proxy without explicit
  auth, logging, and egress boundaries. A receipt records both, never one as the other.
- **The interesting case is not the happy-path SDK call.** It is **restart + bounded
  replay + a real webhook receiver + a client that assumes durable event history.** A
  crisp trace of that case is what lets people decide whether the bridge is an adapter,
  a local task runtime, or something that needs a stronger event log behind it.

## The receipts

Run `synapse a2a-conformance` before recording a receipt. It prints the current
local matrix against the A2A 1.0.0 operation model; receipt work should update
the matching row instead of replacing the matrix with prose.

Each receipt is a small, self-contained record. Capture it as JSON, a gist, or a
discussion reply — the shape matters more than the medium.

### 1. Discovery receipt

- Agent Card returned, the capability-manifest version it was projected from, the
  endpoint URL, the auth mode, and the client/SDK name and version.

### 2. Task-lifecycle receipt

- The submitted payload, the assigned task id, every state transition observed,
  cancel and timeout behaviour, and the final observed result.

### 3. Webhook receipt

- The destination class (public, private, local-network), the bridge's validation
  decision, how auth headers were handled, the delivery attempts, the retry/backoff
  observed, and the operator-visible failure mode.

### 4. Proxy / TLS receipt

- The public origin, the forwarded headers, where auth was terminated, and whether
  the protected and public routes stayed separated across the boundary.

### 5. Replay / subscription receipt

- Which event window is replayable, what is lost on restart, and how a client detects
  the gap. This is the receipt the durable-history-assuming client depends on.

### 6. Threat-model receipt

- The SSRF / local-network checks exercised, token handling, log redaction, and what a
  compromised client can still ask the bridge to do.

## How to contribute one

Run the bridge (`synapse a2a-card`, `synapse a2a-serve` — see the README's A2A
section), exercise a dimension above against your own client/SDK and, where relevant, a
real webhook receiver and a reverse-proxy/TLS boundary, and post the receipt to the
[validation discussion](https://github.com/anulum/synapse-channel/discussions/20). The
restart-plus-replay-plus-real-webhook case is the most valuable one to trace.

Validated dimensions land back in the [deployment guide](deployment.md) and the A2A
section of the README; gaps a receipt exposes become tracked work. Synapse claims no
external A2A conformance from screenshots — only from receipts that others can reproduce.
