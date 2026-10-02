<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — receiving-hub recovery and recorded federation validation
-->

# Receiving-hub ownership and delivery recovery

A forwarded delivery has two hub identities. `origin_hub` identifies the
authenticated requester’s hub and remains part of the immutable request digest.
The receiving hub stores the offer, owns its deadline and resolves work for a
replaced recipient session. These identities can differ.

Delivery storage profile **4** records the server-selected `receiving_hub` in
the accepted event and its indexed aggregate. Hub decisions and cold replay
check this receiver. Opening the journal under another stable hub identity
fails before a listener starts. Request profile and agent protocol remain
version **3**; operation keys, request digests and notification semantics remain
unchanged. An agent cannot choose the receiving hub through a request field.

## Legacy journals

Storage profile 3 did not record a receiver. A local request proves its
receiver through its origin. A forwarded request records only its remote
origin and a local recipient name, which cannot establish journal ownership.
Startup refuses an unbound forwarded journal with `receiving_hub_required`.
Changing `hub_id` or rewriting `origin_hub` is not a recovery procedure.

For an existing forwarded journal, stop the owning hub and every process that
can write that database. Make a consistent backup of the database, its WAL and
the configured encryption keys using your normal backup procedure. Work first
on a restored copy with the same key configuration. Establish the receiver from
the original deployment configuration and custody records; a peer name or
recipient address alone is insufficient.

The explicit offline Python API is:

```python
from synapse_channel.core.persistence import EventStore

with EventStore("restored-hub.db") as store:
    bound = store.delivery.bind_legacy_receiving_hub(
        "verified-receiving-hub",
        recovery_ref="operator-records/verified-deployment-and-backup",
    )
    store.delivery.verify_origin_hub("verified-receiving-hub")
    store.delivery.verify_replay()
```

For an encrypted journal, pass the original `key_file` to `EventStore`. Keep key
contents out of commands, recovery references and logs. The API requires the
operator to establish custody; it cannot verify external deployment records or
stop another process. Do not invoke it while a hub is running.

Row authentication uses a separate key from SQLCipher encryption. For a journal
with authenticated rows, load its **original existing** row key using
`load_or_create_row_mac_key` from `synapse_channel.core.event_row_mac`, supplying
`current_max_seq=store.max_seq()` and `log_has_macs=store.has_row_macs()`.
Call `store.enable_row_mac(key)` before binding and hold recovery if it returns
quarantined rows. Restore the original key alongside the database; never create
a substitute to exempt existing rows. Binding refuses with
`row_authentication_required` if the authenticated journal's writer has no row
key, or `journal_recovery_required` if row authentication fails. It rechecks
authentication inside the recovery transaction and signs new rows through the
owning store's normal writer.

Binding appends `delivery_receiving_hub_bound` events and atomically updates the
receiver index. It preserves accepted requests, operation keys, digests, task
stages, offers and other history. A failed write rolls back the entire binding.
Rebinding to the same receiver adds no events; a conflicting known receiver is
refused. The recovery reference must be printable and contain no credentials.

Quarantine is retained by default. After reviewing the specific historical
`unauthorised_requester` deadline refusal, an operator may pass
`retry_authority_refusals=True` **on the initial binding**. That releases only
that reason and records it in the binding event. Other quarantine reasons stay
held. Binding does not reset deadlines or report task completion; a receiving
hub can expire overdue work after restart. If binding was already performed
with quarantine retained, a later binding call does not release it.

Reopen the recovered copy, verify replay and the original receiving identity,
then follow the deployment’s controlled adoption procedure. Retain the backup
and recovery record. Delivery-aware older releases, including 0.99.36, refuse
new profile-4 events or the changed aggregate of a bound legacy journal. Do not
downgrade an adopted database in place; use a compatible roll-forward runtime
or a pre-adoption backup under the documented delivery rollback procedure.

## Recorded validation and declaration limits

On 2026-10-02, a physical workstation and a Linux laptop exchanged directed
messages in both directions through authenticated native TLS hub federation
over Tailscale. The candidate pair used published Core 0.99.36 with a Fleet
0.8.1 CI artifact from source revision `1ac018b93902a7fdad861d384ec5754221bfde6d`.
A delayed delivery
then remained `queued` after its deadline: the receiver was refused because
the ledger compared its identity with the request’s origin. The original
physical services were restored after that failed acceptance case.

Two real TLS hubs running the published Core 0.99.36 package independently
reproduced the deadline failure and showed that a forwarded-only journal could
start under a different receiving identity. The repository retains the real
legacy journal as `tests/fixtures/delivery_legacy_core_09936.sql`, with hashes
and producer provenance in the adjacent JSON file. Regression tests exercise
forwarded expiry, cold replay, wrong-identity startup, explicit legacy recovery
and storage rollback through actual runtime and database paths.

This evidence establishes message transport for the recorded pair. The original
physical run failed expiry acceptance. It does not establish direct LAN
federation, Windows or ML350 coverage, 100-terminal capacity, provider execution,
an SLA or exactly-once external effects. A new physical run with the published
correction is required before declaring the whole federation acceptance passed.
Dashboard consumers must keep reachability, queue state, boundary delivery,
recipient acknowledgement and task completion distinct.
