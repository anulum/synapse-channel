<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — delivery, retry, clock and durability of every request type
-->

# Delivery matrix

This page answers, for every request type a client can send to a hub, five
questions: how often it takes effect, what makes a retry safe, which clock it
depends on, where the rule is enforced, and what survives a crash. The
normative rules live in [the coordination spec](coordination-spec.md); this
page puts them side by side. `tests/test_delivery_matrix.py` fails when a
request type is missing here, listed twice, or classified differently from
the hub's verb registry.

## Legend

**Delivery.**
- **at-least-once**: a retry can take effect again unless the retry key below
  suppresses it.
- **apply-once with `idem_key`**: with an `idem_key`, the committed effect and
  its response are bound together, and a retry returns the first response
  (INV-DG-2, INV-CR-1). Without one, the request is at-least-once.
- **read**: changes nothing.

**Durable record with `--db`.** What the hub has committed before it answers:
- **FULL**: committed at SQLite `synchronous=FULL`. It survives a process kill
  and an operating-system crash.
- **NORMAL**: committed at `synchronous=NORMAL` in WAL mode. It survives a
  process kill such as `kill -9`; an operating-system crash or power loss can
  drop the last writes (INV-JA-4).
- **memory**: kept only in the running hub and lost when it stops.

Without `--db`, everything except a family's own store is memory. Every keyed
apply-once request commits at FULL whatever its unkeyed record would use.

**Enforced at.** The hub enforces every rule here when it grants or refuses a
request. A claim is coordination at grant time, not a filesystem lock; only
provider hooks and the staged Git gate stop an edit itself.

## Matrix

| Request types | Delivery | Retry key | Clock | Durable record with `--db` |
| --- | --- | --- | --- | --- |
| `chat` | at-least-once | `client_msg_id`: a same-content retry that already reached a recipient is answered `duplicate: true`, in memory for at most 24 h and 4096 pairs (INV-DG-1) | hub stamps `timestamp` (INV-CK-1) | NORMAL; a directed chat nobody received is dead-lettered and replayed to the recipient's mailbox (INV-DG-3); delivery receipts and dead-letter escalations are FULL |
| `ack` | at-least-once | the journal `seq`: an unknown or settled `seq` changes nothing | none | NORMAL mailbox watermark; the deferred receipt it settles is FULL |
| `mailbox_handover` | at-least-once | `(from, through_seq)`: a repeat of an applied handover or close is answered `already_applied`; each forwarded chat cites the original `seq` in `handover_of`, so the successor can drop a repeat after a crash | none | FULL `mailbox_handover` record before the old watermark advances; forwards are ordinary chats |
| `operation_attempt` | at-least-once | `idem_key`, required, applied once by the handler itself rather than by the hub's generic replay, so that a repeat can carry the attempt's present state: a repeat returns the stored answer unchanged with that state beside it; the same key with a changed request is `idempotency_conflict` | hub wall clock, read once per request, for the claim's lease and the intent's deadline | FULL `operation_attempt_answered` row and the stored answer in one transaction before the answer, refusals included |
| `operation_record` | at-least-once | `operation_id`: a second declaration of an id is refused `duplicate_operation`; outcome and verification rows are append-only, so a repeated publish adds a second row (capped at 32 per operation) and a later row never replaces an earlier one | none | FULL `operation_declared`, `operation_outcome` or `operation_verified` record before the answer |
| `heartbeat` | at-least-once | none needed: it only refreshes presence; a repeated registration with the same roles writes no second `role_binding` | hub clock for liveness | memory; a first-use identity pin goes to the `--identity-pins` file; a changed role set is a NORMAL `role_binding` event |
| `claim`, `release`, `task_update`, `handoff`, `checkpoint`, `guard_denial` | apply-once with `idem_key` | `idem_key`; the lease epoch fences an earlier holder (INV-EF-2) | only the owning hub's clock starts and ends a lease (INV-CK-3) | FULL |
| `ledger_task`, `ledger_task_update`, `finding` | apply-once with `idem_key` | `idem_key` | hub clock | FULL |
| `ledger_progress`, `recall_log`, `resource`, `offer_resource`, `resource_offer` | apply-once with `idem_key` | `idem_key` | hub clock | FULL with `idem_key`; NORMAL without |
| `native_message_record` | apply-once with `idem_key` | `idem_key`, which it requires | hub clock | FULL |
| `delivery_request`, `delivery_ack`, `delivery_boundary`, `delivery_outcome`, `delivery_cancel`, `delivery_status_request` | at-least-once | `request_id`, `idempotency_key` and `mutation_id` bind each step to one delivery record ([delivery recovery](delivery-journal-recovery.md)) | hub clock for deadlines | FULL in the hub's delivery ledger |
| `wait_request`, `advertise` | at-least-once | none | hub clock | memory |
| `channel_create`, `channel_invite`, `channel_join`, `channel_leave` | at-least-once | none | none | memory; channel chat itself follows `chat` |
| `identity_enroll`, `identity_revoke`, `identity_pin_reclaim` | at-least-once | a rotation and a pin reclaim name `expected_key_id`, which refuses a change made against a stale key | hub clock for the enrolment rate window | FULL audit record; the key and pin files are replaced atomically |
| `entitlement_advert`, `operator_relay_request` | at-least-once | none | hub clock | FULL |
| `spend_request` | at-least-once | an idempotency key scoped to caller, pool, window, seat, task and operation; a changed request under a used key is refused | hub clock for the spend window | FULL in the spend ledger; `query` and `status` only read |
| `attachment_begin`, `attachment_chunk`, `attachment_commit`, `attachment_abort`, `attachment_ref`, `attachment_gc`, `attachment_peer_request` | at-least-once | the content digest, the `upload_id` and each chunk's `offset` | signed-frame skew budget (INV-CK-2) | fsynced in the attachment store |
| `multihub_claim_request`, `multihub_message_forward`, `dead_letter_forwarding` | at-least-once | the owning hub applies its own `claim` or `chat` rule; a forward carries its forward id | each hub's own clock (INV-MH-1) | the origin's forward outbox is FULL; dead-letter forwarding records are FULL |
| `who_request`, `state_request`, `board_request`, `history_request`, `resume_request`, `manifest_request`, `channel_list_request`, `channel_history_request`, `multihub_log_request`, `federation_offer_request`, `attachment_info`, `attachment_read` | read | none needed | none | nothing written |

Signing does not change these rows. With `--require-message-auth` a signed
frame must also fall inside the INV-CK-2 skew window. With
`--require-signed-frames` it must carry the connection's next sequence, which
needs no clock ([signed frames](protocol.md#signed-frames)).

## What a kill -9 is proven to keep

`tests/test_hub_sigkill_durability.py` starts a real `synapse hub` process with
`--db`. The hub acknowledges three things: a keyed claim, a directed chat with
a delivery receipt to a recipient who is offline, and a progress note. The
test then kills the process with SIGKILL, with no shutdown of any kind, and
starts a second process on the same database. That process must still hold:

- the claim, with its owner and epoch; a retry with the same `idem_key`
  returns the first grant;
- the chat, which it replays to the recipient's mailbox on connect;
- the pending receipt, which the recipient's `ack` settles for the sender as
  `delivered: true, deferred: true`.

The same test against a hub without `--db` fails, so it measures the journal
and not the test. It runs on POSIX (SIGKILL) in every Linux CI shard and the
macOS cross-OS lane. It does not cover an operating-system crash or power
loss, which only the FULL rows withstand.
