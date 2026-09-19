<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Delivery protocol compatibility decision

Status: **reviewed C06 decision record, followed by local C05 implementation**
(2026-09-19). At the C06 decision checkpoint, the wire was version 2. C05
implements a gated version 3 in Core; F06 Fleet compatibility and a release
remain separate work. Current behavior is defined by [the wire protocol](protocol.md)
and [the coordination invariants](coordination-spec.md). AEF receipts prove the
events they bind; they do not prove a recipient's model executed an instruction.

## Baseline and decision

At the C06 checkpoint, the JSON/WebSocket envelope had `sender`, `target`, `type`, `payload`
and `timestamp`; hub frames also have `hub_id`. The welcome handshake advertises
integer `protocol_version=2`. A missing or malformed version negotiates as
legacy version 1; an older or newer advertised version gives an operator
warning. Version 2 added mailbox `ack` and deferred transport receipts. Chat
remains at least once, keyed claim-family mutations apply once with a durable
journal, and `ack` advances a receiver watermark. Immediate and deferred
receipts show transport reachability and acceptance only. The stable durable
`seq` is a mailbox attempt identity; `msg_id` resets on hub restart.

**Decision at C06:** keep JSON/WebSocket and wire version 2 while C05 is developed.
Add no new delivery mode to a version-2 frame and do not reinterpret its
`delivery_receipt`, `ack`, `delivered` or `deferred` fields. A future delivery
vocabulary requires a new negotiated feature profile and a version bump after
the real two-adapter test matrix passes. The candidate revision is **version 3**;
that number was the design target and is now the locally implemented Core wire
constant. C05 freezes exact field names and public schemas with conformance
fixtures. Internal task labels such as C05/C06/F06 never enter the wire.

The committed `benchmarks/coordination_wire_benchmark.py`
measures six fixed current-wire control and coordination frames with the
production bounded JSON decoder. The local Python 3.12.3 run used 1,000 codec
iterations per frame: 1,168 bytes with the current `json.dumps` spacing and
1,080 bytes minified, a 7.5% size difference. Individual frames were 128–261
wire bytes. The local median codec timings are in the committed result; they
vary with shared-host load. These are synthetic shapes and local codec timings,
not WebSocket latency, throughput, provider traffic or a compression study.
The current 1 MiB default hub frame ceiling remains. This evidence provides
no reason to add CBOR/Protobuf or change JSON framing now. F06 may revisit
encoding only with measured real coordination traces, canonicalization and
mixed-version evidence; byte saving alone cannot justify a second decoder.

The C05 rerun keeps the six baseline examples unchanged at 1,168 wire bytes
and adds three version-three examples: request 441, offer 546, and acknowledged
status 511 bytes. The nine-frame total is 2,666 bytes with default spacing and
2,485 minified. These are local codec samples, not network latency or a maximum
frame-size proof; the per-mode 8,192-byte body cap is enforced separately.

## Candidate normative delta for C05

The following MUST/SHOULD statements were prospective at the C06 checkpoint.
They govern C05's local Core implementation review; the runtime contract and
remaining limitations are stated in [the wire protocol](protocol.md).

1. **Session incarnation.** A recipient session MUST have an opaque incarnation
   issued or verified by its owning hub at authenticated registration. A delivery
   request MUST bind the exact target identity, owning hub and incarnation when
   the mode depends on a live turn boundary. A reconnect or a new process with
   the same display name MUST NOT inherit a pending interrupt or steer. An
   absent incarnation means a legacy transport-only message, never permission
   to guess a current process. The server must derive authority from the
   authenticated connection, not trust a sender-supplied session string.
2. **Origin and correlation.** Every new delivery request MUST carry a
   sender-scoped stable request id and an idempotency key; the hub MUST bind
   origin hub, sender principal, target and task correlation in its durable
   record. A duplicate with identical canonical content returns the original
   disposition. Reusing an id with different content MUST be rejected. A
   forwarded hub MUST preserve original provenance and MUST NOT claim local
   execution. A hub id is provenance, not authentication by itself.
3. **Modes and feature negotiation.** Candidate modes are interrupt, steer,
   follow-up and next-turn intent. Each recipient advertises its native and
   emulated capabilities for its current incarnation. A sender MUST request a
   supported mode and receive an explicit acceptance or refusal. An unsupported
   mode MUST NOT silently become chat. A fallback requires the sender's
   explicit allowed-mode list and a reported selected mode. Unsupported or
   unknown mode values fail closed. Interrupt requires an authorised sender
   and cannot silently abandon a live claim or bypass the recipient's native
   approval prompt. Shell effects remain outside a parser-based safety claim.
4. **Stages and evidence.** Status MUST distinguish request accepted by hub,
   durable queue, recipient boundary delivered, explicit recipient ACK and
   completed or failed task outcome. Neither socket match nor mailbox `ack`
   can be promoted to outcome. Outcome requires recipient-origin evidence bound
   to request, session incarnation and task. A receipt names its stage and
   evidence source; absent evidence remains unknown. At-least-once transport
   plus durable deduplication is the baseline. No exactly-once external effect
   is promised.
5. **Ordering, deadline and cancellation.** Order is guaranteed only inside
   one recipient incarnation and one durable queue, using journal sequence;
   cross-hub wall clocks cannot order tasks. The sender MUST supply a bounded
   deadline for a time-sensitive mode. Expired queued work MUST NOT be delivered
   later as an active interrupt. A cancellation is a request until the
   executor confirms it; a completion racing with cancellation retains both
   facts and its original evidence. Rejection, expiry, cancellation and
   supersession are distinct terminal dispositions. A lease/claim remains
   governed by its own release and epoch rules.
6. **Atomic replay boundary.** The hub MUST commit a queue/stage transition
   and its stable notification identity before publishing it. After a crash
   between delivery and ACK, it may redeliver the same request identity;
   recipient deduplication prevents reapplying its local intent. A receiver
   advances a durable cursor only after accepting the matching queued record.
   Journal replay MUST reject gaps, incompatible profiles and altered duplicate
   content before projecting new state.
7. **Limits and optional fields.** New delivery request and status frames MUST
   remain below the configured hub `max_msg_bytes` ceiling (default 1 MiB)
   including the full JSON envelope. C05 MUST select and test a smaller
   per-mode body limit before release; this decision does not invent an
   unmeasured production cap. Names, ids, mode lists and error text need
   explicit byte and cardinality bounds in the frozen schema. An absent
   optional field receives only the documented safe default. `null`, unknown
   enum values, duplicate keys, non-finite numbers, over-depth JSON and
   unsupported field versions MUST NOT broaden authority. An older receiver
   may ignore harmless unknown display metadata, but a new sender MUST NOT
   depend on that behavior for a safety or delivery guarantee.
8. **Errors and reporting.** New failures MUST have stable machine-readable
   reason codes for unsupported protocol/profile/mode, stale incarnation,
   unauthorised requester, id conflict, size/shape refusal, deadline expiry and
   unavailable recipient. Human text is advisory. A refusal MUST identify the
   request correlation without echoing secrets or arbitrary body text, and
   MUST NOT create a false positive delivery receipt. Existing v2 `error`
   frames keep their current meaning.

## Mixed-version matrix and migration

| Pair | Negotiated behavior | Refusal / evidence |
|---|---|---|
| v1 client ↔ v2 hub | Existing chat/claim flow; no client mailbox ACK. | Immediate receipt only where previously supported; no new mode or outcome claim. |
| v2 client ↔ v1 or absent-version hub | Effective v1, warning, no ACK emission. | No deferred receipt assumption; no new mode. |
| v2 client ↔ v2 hub | Current version-2 mailbox and receipt behavior. | `ack` is transport-only; explicit outcome absent. |
| v3 client ↔ v2 hub | Version-three delivery methods refuse locally; ordinary v2 chat remains available only through the separate chat API. | `unsupported_protocol`; no v3 frame sent. |
| v2 client ↔ v3 hub | Hub retains v2 behavior for that connection. | No v3 stage or outcome inferred from legacy `ack`. |
| v3 client ↔ v3 hub, recipient adapter lacks mode | Hub accepts only a supported mode from the sender's explicit fallback list. | `unsupported_mode` or `stale_incarnation` with request correlation. |
| Fleet forwarding between different hub profiles | Lowest common admitted profile, origin retained; no capability invented at relay. | F06 refuses unsupported semantics and reports exact profile gap. |

Rollout order: introduce an additive capability advertisement and explicit
refusals first; retain v2 chat/ACK behavior; test v3 behind a negotiated gate;
upgrade hubs before clients/adapters; then admit Fleet version pairs and
document downgrade. An old client or hub never sees a v3-only verb during
normal operation. Rollback disables new-mode admission while keeping already
accepted durable records readable and reconcilable. It MUST NOT discard queued
work, rewrite receipts or turn a pending cancellation into a success. If a
new-mode queue cannot be drained by the rollback binary, keep the compatible
reader/worker or stop the rollback and report the blocker.

## Acceptance matrix delegated to implementation

C05 must verify with a real hub and two distinct actual participant adapters:
native and emulated modes, unavailable/unknown mode, sender-approved fallback,
old v1/v2 peer, fresh and stale incarnation, reconnect, identical and altered
duplicate ids, target session switch, deadline expiry, queued cancellation,
cancel/completion race, crash after committed queue before delivery, and crash
after delivery before ACK. Each case checks journal replay, displayed stage and
secret-free reason code. It must separately prove that an ACK without recipient
outcome remains incomplete and that an unsupported shell effect remains denied.

F06 must test old/new Core and Fleet pairings through real mTLS forwarding:
native vs unsupported mode, original sender/hub preservation, recipient
incarnation, duplicate receipt, delayed expiry, outage/restart, journal
recovery, and explicit rollback with unresolved work. Fleet's observed state
cannot become authoritative merely by forwarding a v3 record. AEF receipts
must continue to bind the original event and exact stage, without re-signing a
different outcome. Both tasks must measure the selected new frames against the
current JSON baseline and re-run their official version compatibility gates.

## Review record

This was a same-seat technical self-audit under the owner-accepted local
verification path while cross-vendor peer runtimes are unavailable. It
reviewed `core/protocol.py`, current message and mailbox behavior, version
negotiation, journal replay, the existing coordination invariants, AEF receipt
boundary and Fleet F06 requirements. The current baseline protocol/mailbox/
receipt/spec test selection passed 90 tests. The decision is internally
reviewed for implementation planning; it is not independent security review,
a frozen v3 schema or proof that C05/F06 works. Security/enforcement release
gates and exact-object review remain separate.
