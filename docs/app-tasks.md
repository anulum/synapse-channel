<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Human app tasks

`synapse app-task` records a task that a person completes in an app. Synapse
prepares the prompt and input bundle, tracks the handoff, and checks the
returned result. It does not sign in to an app, drive its GUI, or consume an
app subscription automatically.

An offer needs a current window in the private [entitlement ledger](entitlements.md).
The queue snapshots that window's source, age, unit and balance evidence when
the task is offered. This is advisory evidence; a task does not reserve or
debit the ledger. The owner-local queue is under
`$XDG_STATE_HOME/synapse-channel/app-tasks/queue.sqlite3` by default. Its
directory and SQLite file are owner-only.

Create an owner-only JSON file such as `offer.json`:

```json
{
  "task_id": "review-17",
  "prompt": "Review the supplied question in the app and return JSON.",
  "input": {"question": "Which source supports this result?"},
  "window_id": "my-current-window",
  "expires_at": "2026-09-24T12:00:00Z",
  "verifier": {"field": "reviewed", "equals": true}
}
```

`expires_at` must be a future UTC-offset timestamp. The verifier is fixed at
offer time and checks one field of the returned `payload`. Choose a predicate
that actually proves the result required for this task. A generated prompt or
an attached result alone never completes it.

```bash
chmod 600 offer.json
synapse app-task offer --file offer.json
synapse app-task accept review-17
synapse app-task start review-17
```

The person performs the app work, then saves an owner-only JSON result file:

```json
{
  "task_id": "review-17",
  "payload": {"reviewed": true, "answer": "Source and reasoning supplied by the app"},
  "provenance": "operator:manual-app-upload",
  "usage": {"amount": "1", "measurement": "manual"}
}
```

```bash
chmod 600 result.json
synapse app-task attach review-17 --file result.json
synapse app-task verify review-17
synapse app-task show review-17
synapse app-task history review-17
```

The result envelope must name the offered task. Repeating the same upload is
idempotent; a changed upload or an upload for another task is refused. Results
remain untrusted data. The queue stores the returned text and provenance in
the private database and records an event for each transition. Decline an
offer with `decline`; cancel an offered, accepted, running or attached task
with `cancel`. Offers and accepted tasks expire at their deadline. A verified
task's manually entered usage can be corrected with
`synapse app-task correct-usage review-17 --amount 2 --reason "receipt correction"`;
the original result remains attached to the task, and the correction is a
separate durable event. The history includes the result digest, self-reported
provenance and the CLI or MCP bridge identity that submitted it. It does not
include returned content.

The local stdio MCP server exposes `synapse_app_task_offer`,
`synapse_app_task_status`, `synapse_app_task_advance`,
`synapse_app_task_attach`, `synapse_app_task_verify`, and
`synapse_app_task_correct_usage`. MCP replies contain task state and redacted
allowance evidence; they omit prompt text, returned content and private account
source. The CLI is the owner view. Remote MCP is a separate transport with its
own admission requirements.
