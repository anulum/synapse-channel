<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — per-message authentication runtime
-->

# Per-message authentication runtime

Per-message authentication is implemented for selected mutating hub frames as
an opt-in HMAC-SHA256 runtime. It runs after WebSocket connect authentication:
`synapse hub --token` still gates admission, and `--require-message-auth` then
requires signed frames for claims, releases, task updates, handoffs,
checkpoints, and resource offers.

The default remains off for compatibility and for loopback-only single-operator
use. Operators opt in explicitly:

```bash
synapse hub \
  --token "$SYNAPSE_TOKEN" \
  --message-auth-key main:"$SYNAPSE_MESSAGE_AUTH_SECRET":project/agent \
  --require-message-auth
```

Clients can sign mutating frames by constructing `SynapseAgent` with
`per_message_auth_key_id` and `per_message_auth_secret`. Unsigned chat and
heartbeat frames remain ordinary envelopes; the first runtime tranche protects
state-changing coordination frames.

This runtime does not encrypt payloads, does not replace TLS, does not replace
signed events, does not create per-agent identity, and does not enforce ACLs.
**Signature support remains a design target** for the
[signed events and mTLS](signed-events-mtls.md) trust model rather than a
partially implemented frame mode.

## Frame authentication profile

An authenticated frame remains an ordinary protocol envelope with an `auth`
object:

```json
{
  "sender": "project/agent",
  "target": "System",
  "type": "claim",
  "payload": "",
  "timestamp": 1782648000.0,
  "task_id": "TASK-1",
  "auth": {
    "alg": "hmac-sha256",
    "kid": "main",
    "nonce": "base64-url-nonce",
    "sequence": 1,
    "timestamp": 1782648000.5,
    "value": "hex-hmac"
  }
}
```

The **authenticated frame** bytes come from a **canonical frame**: JSON with
sorted keys and compact separators, with `auth.value` excluded and all other
`auth` fields included. The message authentication code binds the frame type,
sender, target, payload, key id, nonce, sequence, and authentication timestamp.

Each configured **key id** maps to one HMAC secret and at least one allowed
sender. The CLI requires
`--message-auth-key KEY_ID:SECRET:SENDER[,SENDER...]`; embedded callers must
configure `MessageAuthKey(..., senders=...)`. Empty sender bindings fail
closed.

## Replay controls

Per-message authentication includes replay protection:

- A **nonce** makes each authenticated frame unique for a key id and sender.
- The **signed sequence metadata** remains authenticated for diagnostics, but
  nonce uniqueness is the replay identity. This avoids false replay failures
  after a client reconnect resets an in-memory sequence counter.
- A **timestamp window** rejects signed frames outside
  `--message-auth-window-seconds` seconds in the past plus a small future clock
  skew allowance. The default past window is `10.0`.
- A bounded in-memory **replay cache** records recent nonces. The default
  `--message-auth-replay-capacity` is `4096` entries. After expired entries are
  evicted, a full live cache rejects new signed frames rather than evicting
  in-window nonces and reopening replay.
- The existing idempotency key remains part of mutating retry semantics; it is
  not a replay-protection system by itself. The reusable signed client emits a
  fresh idempotency key on signed mutating frames that did not already provide
  one.

The replay cache is intentionally in-memory only. A hub restart clears accepted
nonce history, so a captured signed frame inside the timestamp window can still
reach verification after restart. The tighter default window and signed-client
idempotency keys bound the residual retry window, but operators should treat a
restart as clearing per-message-auth replay memory. Durable idempotency replay
remains separate: when a journal-backed hub restarts, an accepted mutating
command with the same idempotency key can replay its original response, but
per-message-auth replay state itself is not journal-backed.

Verification produces stable **verification result** strings: `ok`, `missing`,
`expired`, `unknown_key`, `revoked_key`, `bad_authentication`,
`sender_mismatch`, `sequence_mismatch`, and `replayed`. Hub refusals return an
`error` frame with `verification_result` set to the refusal reason.

## Key lifecycle

The runtime keeps keys local and explicit:

- Add one or more `--message-auth-key KEY_ID:SECRET:SENDER[,SENDER...]` values
  to the hub.
- Rotate by adding a new key id for new clients while keeping the older key id
  available until its replay and operational retry windows have passed.
- Embedded callers can mark a `MessageAuthKey` as revoked; frames naming that
  key id fail with `revoked_key`.
- Do not put secrets in shell history, service files, logs, receipts, or
  diagnostics. Prefer environment files or a local secret manager when running
  a long-lived service.

There is no managed key store, no key-file lifecycle command, and no automatic
rotation workflow yet. Lost-key recovery is an operator action, not a hub
action.

## Relationship to signed events

Per-message authentication and signed events solve different problems:

- Per-message authentication rejects a bad incoming frame before it mutates hub
  state.
- Signed events make selected durable event-log records tamper-evident after
  admission, storage, replay, relay export, or postmortem reconstruction.

An exposed deployment may need both. This runtime authenticates selected
incoming frames; it does not sign durable event-log records or certify
federation.

## Boundaries

The implemented runtime proves that a selected mutating frame was signed by a
configured HMAC key holder inside the accepted timestamp window. It does not
encrypt payloads, does not replace TLS, does not replace signed events, does
not replace per-agent identity, does not replace ACL enforcement, does not
sandbox connected agents, and does not make a shared-token hub safe on an
untrusted network by itself.

The first runtime tranche gates only claims, releases, task updates, handoffs,
checkpoints, and resource offers. It runs after WebSocket admission and hub
sender resolution; it does not authenticate initial name binding, presence,
heartbeat traffic, or takeover of a live identity.

The [identity and ACL design](identity-and-acl.md) remains the future layer
that maps a key holder to an audit subject and decides whether the requested
verb and target are allowed.

The local-first tradeoff is key-management complexity. Loopback-only single
operator use can stay unsigned. Exposed deployments need explicit keys, sender
binding, replay cache bounds, key rotation, revocation procedures, diagnostics,
and operator review before per-message authentication is treated as one part of
a broader hardening profile.
