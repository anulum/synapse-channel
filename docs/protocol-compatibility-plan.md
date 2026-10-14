<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Wire and delivery compatibility

SYNAPSE CHANNEL uses JSON over WebSocket. The current source advertises wire
version **7**, independently of the package version. Exact frame shapes are
specified in [the wire protocol](protocol.md); coordination invariants are in
[the coordination specification](coordination-spec.md). A version number
identifies a vocabulary, not provider execution, peer trust or fleet readiness.

## Feature floors

| Minimum wire version | Capability |
| --- | --- |
| 1 | Legacy chat and claim flow |
| 2 | Mailbox `ack` and deferred transport receipts |
| 3 | Session-bound delivery requests, recipient stages and cancellation |
| 4 | Scoped local attachment transfers |
| 5 | Hub-to-hub chat and delivery forwarding |
| 6 | Recipient-granted private cross-hub attachment reads |
| 7 | Record-only `native_message_record` |

These floors come from `core/protocol.py`. Each optional path also has its own
configuration and authorisation requirements. Advertising a floor does not
install a recipient adapter, provision keys, grant access or start a vendor
client. A newer connection retains the version-three delivery frame profile;
its delivery frames do not change their `protocol_version` to seven.

## Negotiation and client boundaries

The hub advertises `protocol_version` in its `welcome` frame. Version skew by
itself is not a connection refusal. The public `read_protocol_version` helper
accepts a non-boolean integer; absent, boolean and non-integer values return
`None`. `negotiate_protocol_version` chooses the lower advertised/local version,
or compatibility version one when the peer version is absent, and returns an
operator warning for skew or an absent advertisement.

Do not assume every client applies that helper identically. The Python agent
records the advertised hub version and gates each optional outbound API by its
feature floor. Its delivery API refuses an absent version or disconnected hub
as `unavailable_hub`, a version below three as `unsupported_protocol`, and an
unknown stable hub identity as `unsupported_profile`. This API does not silently
convert a delivery request into chat. Ordinary chat is a separate operation.

| Connection/profile | Delivery boundary |
| --- | --- |
| Python agent with hub below wire three | Delivery methods refuse before sending the request; ordinary chat remains a separate API. |
| Legacy client on a newer hub | Its negotiated profile limits optional verbs; a mailbox ACK conveys no version-three outcome. |
| Current hub and compatible recipient | An advertised recipient incarnation and supported mode are still required. |
| Recipient without the requested mode | Only sender-approved fallback modes are eligible; refusal remains explicit. |
| Cross-hub forwarding | Configured trust, peer grants and forwarding support are independently required; forwarding does not prove provider execution. |

For other clients, validate the exact client build and feature against a real
hub. Successful registration alone does not qualify optional modes or every
old/new Core–Fleet pairing.

## Receipts, replay and outcomes

A directed socket match, durable mailbox queue and transport `ack` are different
stages. A version-three delivery request binds its stable operation identity,
target hub, target incarnation, task and deadline. A recipient stage report can
record boundary delivery, acknowledgement or task outcome; the evidence remains
recipient-supplied. The hub does not independently inspect the provider's
execution or external effects.

An ACK without a completed or failed outcome remains incomplete. Unsupported
modes must not be inferred from a successful chat or generic wake. A reconnect
must use the current recipient incarnation rather than reuse an old process's
identity. Stable request IDs and retained canonical content support replay and
conflicting-duplicate refusal within their storage profile. They do not promise
exactly-once external effects, unlimited retention or recovery after storage
loss. Cancellation is a request until the executor confirms the disposition.

AEF receipts bind the events and stages they contain. They do not establish
that a recipient model read or executed an instruction, nor certify an entire
workflow that is absent from the receipt.

## Upgrade and rollback

Before changing hub, client or Fleet versions, record exact builds, negotiated
wire profiles, stable hub identities, storage profiles, trust configuration and
pending request IDs. Exercise the needed feature through its real entry point;
include reconnect, expiry, duplicate content and unavailable recipient cases.
The [multi-hub guide](multi-hub-sync.md) describes the separate serving and
forwarding requirements.

For a durable hub downgrade:

1. Stop admission of work that the proposed binary cannot process. Preserve the
   journal, database and required keys using the [deployment recovery guidance](deployment.md).
2. Inspect retained delivery requests. `queued`, `boundary_delivered` and
   `acknowledged` are nonterminal stages; cancellation requests may still require
   executor reconciliation. Record their original IDs and evidence.
3. Verify that the rollback build can read and process the actual storage
   profile. Opening SQLite successfully or reading `PRAGMA user_version` is not
   proof of delivery compatibility.
4. Keep a compatible reader/worker until unresolved work is reconciled, or stop
   the downgrade. Never discard queued work, rewrite receipts or turn an
   unconfirmed cancellation into success to make rollback appear complete.
5. After recovery, preserve the stable `hub_id`, inspect retained dispositions
   and qualify the required APIs before resuming admission. An interval on an
   incompatible binary remains unavailable work, not inferred delivery.

## Encoding measurements

The committed [coordination wire benchmark](benchmarks.md#coordination-wire-baseline)
contains a dated, synthetic version-two/version-three codec sample. It is not a
measurement of all current version-seven frames, network latency, provider
traffic or fleet throughput. Its exact recorded sizes apply to those fixtures
and their source fingerprints. Adding a second encoding requires separate
mixed-version and canonicalisation evidence; a fixture byte saving alone does
not establish an operational benefit.
