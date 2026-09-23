<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — review feedback custody and author routing
-->

# Review feedback to the author session

The local review workflow ties one signed GitHub review or inline diff comment
to an exact repository, commit diff, task, author seat and native session. It
keeps the original signed webhook bytes in an owner-only SQLite store. A GitHub
login is source attribution, not a Synapse identity or an independent approval.
Review text is untrusted data. Only a latest decision by the named, different
Synapse reviewer on the exact digest-bound subject counts as a disposition.

The independently installable `synapse-github-app` package supplies signed
webhook decoding. Core supplies `synapse review-feedback`, the existing
`synapse approval` workflow, the owner-local attention queue and directed hub
delivery. The App manifest can subscribe to `pull_request_review` and
`pull_request_review_comment` with its existing pull-request read permission.
No hosted callback, App registration or automatic review listener is supplied
by this local workflow. An operator provides a captured raw body and its three
GitHub headers in owner-only files; the signature is checked against an
owner-only secret file before any finding enters the store.

## Local journey

Bind a completed single-parent commit before ingesting its feedback. Its
`Seat:` trailer must match the exact author seat; the task and provider-native
session token are declared by that author and held only in the private store.

```sh
synapse review-feedback bind --repo-path /path/to/checkout \
  --repository owner/repository --commit FULL_COMMIT_SHA --task-id TASK-ID \
  --author-seat PROJECT/author-seat --author-session NATIVE_SESSION_ID

synapse review-feedback ingest --webhook-file /private/raw-webhook.json \
  --headers-file /private/headers.json --secret-file /private/webhook-secret \
  --severity high --evidence 'source path and observed failure' \
  --verify 'exact command or result that would resolve the finding'
```

`headers.json` contains exactly `X-Hub-Signature-256`, `X-GitHub-Event`, and
`X-GitHub-Delivery`. The raw body, headers and secret files must be owner-only.
`ingest` prints a stable review key and an approval subject only when the
reviewed commit has a binding. Duplicate deliveries preserve the first bytes;
the same review or delivery id with different evidence is refused. Summary
reviews and inline comments have distinct keys. Inline comments retain their
repository-relative path and source line when GitHub supplies one.

Route the printed subject through the existing hub approval workflow. The
reviewer seat must differ from the bound author seat. The GitHub review state
(`approved`, `changes_requested` or `commented`) never substitutes for this
decision, and the author cannot make their own decision count.

```sh
synapse approval request --name PROJECT/review-router --subject REVIEW_SUBJECT
synapse approval decide --name REVIEWER/seat --subject REVIEW_SUBJECT --approve
synapse review-feedback status REVIEW_KEY --hub-db /path/to/hub.db \
  --reviewer-seat REVIEWER/seat --repo-path /path/to/checkout \
  --current-commit CURRENT_FULL_SHA
synapse attention sync /path/to/hub.db \
  --review-store /private/reviews.sqlite3 --reviewer-seat REVIEWER/seat
synapse review-feedback route REVIEW_KEY --hub-db /path/to/hub.db \
  --reviewer-seat REVIEWER/seat --repo-path /path/to/checkout \
  --current-commit CURRENT_FULL_SHA --name PROJECT/review-router
```

For an encrypted hub journal, pass `--db-key-file` to `status`, `route`, and
`attention sync`. For a token-protected hub, pass an owner-only `--token-file`
to `route`; it also accepts the existing `SYNAPSE_TOKEN` environment variable.

The default private store is
`$XDG_STATE_HOME/synapse-channel/review-feedback/reviews.sqlite3`, or the
matching `~/.local/state/` path. All commands accept `--store` when another
owner-only path is needed. `status` shows the severity, evidence, expected
verification, independent decision, task/session binding and diff
applicability. It prints only the session hash by default; the native token
appears with `--show-author-session`. The original body appears only with
`--show-untrusted-body`. Keep both optional outputs out of shared logs.
`exact_commit` means the exact bytes match. A stable Git patch id on a
different commit yields `same_patch_rebased_requires_recheck`; changed work
yields `stale_diff`. Missing author, missing decision, or stale diff blocks
delivery. A rebase classification is a prompt to recheck context, not a new
approval.

`route` sends a content-minimised directed notice to the exact author seat.
It includes a hash of the native session token, not the token or reviewer
body. The author inspects the owner-local record and resumes that exact native
session under their normal host workflow. The durable hub mailbox carries an
offline notice across a seat restart; retries reuse a stable message id for
the same review decision. A later approved or rejected decision has a new
revision and can be routed separately. The hub delivery receipt proves
transport delivery only. It does not prove that the model read the finding,
ran verification, accepted the review or merged any code.
