<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — owner-local attention queue guide
-->

# Local attention queue

`synapse attention` projects approval requests, failed delivery and recovery
from a hub event store. Optionally, it reads the owner-local [account and quota
ledger](entitlements.md) for stale observations and exhausted windows with a
recorded renewal. It can also project owner-local [review feedback](review-feedback.md)
for missing author bindings, pending independent decisions and route receipts.
The queue is advisory: it never decides an approval, retries
a delivery or spends a quota. A late observer is shown as `missing_observer`;
an empty queue is `quiet` only after a recent successful observation.

Use an existing hub database. A sync reads only the event kinds needed for
attention, folds each reason and subject to its latest source revision, then
reconciles the owner-only SQLite queue. The default queue path is
`$XDG_STATE_HOME/synapse-channel/attention/queue.sqlite3`, or
`~/.local/state/synapse-channel/attention/queue.sqlite3` when that variable is
unset. Its directory and database file must be owner-only.

```bash
synapse attention sync /path/to/hub.db
synapse attention sync /path/to/hub.db --entitlement-store /path/to/ledger.sqlite3
synapse attention sync /path/to/hub.db --review-store /private/reviews.sqlite3 \
  --reviewer-seat REVIEWER/seat
synapse attention list
synapse attention list --json
synapse attention snooze approval:TASK-7 --hours 2
synapse attention resolve approval:TASK-7
```

`--db-key-file` reads an encrypted hub through the same SQLCipher key material
as other local event-store commands. `--approval-hours` sets the review
deadline (24 hours by default); when it passes, the queue marks an undecided
request `expired` and raises its severity. **Elapsed time never grants or
denies approval.** Use `synapse approval decide` to record the actual decision.
An explicit local resolution only clears the alert at its current source
revision. A fresh request or other new evidence reopens it. A deferred
delivery acknowledgement replaces the failed-delivery alert with one
time-bounded recovery item. Snoozes hide the alert until their deadline;
source changes cancel the snooze. An absent C04 observation is stale, while a
fresh observation clears that stale alert. `--stale-hours` sets its threshold.

Run `sync` regularly to keep the observer current. `list` reports the last
successful source observations and defaults to a five-minute freshness limit;
`--observer-seconds` changes that limit. If no sync has run, or one stops,
the CLI and cockpit report a missing observer. A failed source read does not
replace the last successful observation with a false quiet state.

Desktop delivery is **opt-in**. Add `--desktop` to `sync` on a host with
`notify-send`; `--desktop-max` bounds each pass to at most 50 new alert
revisions (default 10). One generic preview says only how many items need
review. It contains no task body, account label, subject or source path.
Repeated syncs suppress the same revision and severity state. A failed
desktop command is not marked delivered, so a later pass can retry.

To show this same local queue in the authenticated cockpit, start the
dashboard with `--attention-store /path/to/queue.sqlite3`. Its bearer-gated
`/attention.json` route serves a bounded, content-minimised view. The cockpit
keeps its live fleet signals and adds these persisted alerts to the same
attention panel. It marks a missing or unavailable observer explicitly. The
cockpit remains a read-side display; use the CLI for snooze and resolution.
The dashboard does not start a sync process or read the private entitlement
ledger on its own. Mobile and chat delivery are separate adapters.
