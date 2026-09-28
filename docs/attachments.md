<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Scoped local attachments

Attachments are an optional, local WebSocket API for small evidence objects. The
Hub stores bytes in an owner-only directory and keeps their SHA-256 digest,
length, media type, provenance, expiry, and project scope in a separate SQLite
ledger. It never publishes bytes through the dashboard, HTTP API, manifest, or
federation. A digest is an identifier, never a credential.

Enable the API with `synapse hub --attachment-root /private/path` alongside a
durable `--db`, connect `--token`, `--identity-trust` and
`--require-identity-binding`, `--message-auth-key`,
`--require-message-auth` and a durable replay database, plus `--acl-policy`,
`--require-acl` and `--role-grants`. The Hub refuses to start the attachment
store unless all gates are present. The root must already have an owner-only
parent; the Hub creates its root and `staging` and `objects` directories at
mode `0700`. Back up the ledger and object directory together. The bytes are
not encrypted by the attachment store; use owner-controlled encrypted storage
when disk confidentiality is required.

For identity `proj/alice`, the requested `scope` must be exactly `proj`. The
owner grants both a role address (`proj/attachment-read`,
`proj/attachment-write`, or `proj/attachment-admin`) and an ACL rule with the
same permission on target kind `attachment`, target `proj`, namespace `proj`.
Read, write, and admin grants are separate. Admin controls garbage collection.
The Hub checks the signed, bound sender and both grants before touching a
digest, upload ID, or object. Cross-project access and federation are absent.

The wire version is `4`. Every request has the normal envelope plus `scope`;
every request is signed with per-message authentication. Admitted requests
receive a private `attachment_result` with `operation`, `ok`, and either result
fields or `error`; the Hub's earlier signature and ACL gates return their
ordinary private `error` frame on refusal.

| Request | Fields | Result |
| --- | --- | --- |
| `attachment_begin` | `digest` (lowercase SHA-256), `length`, `media_type`, `provenance`, `expires_at` (Unix seconds) | `upload_id` |
| `attachment_chunk` | `upload_id`, sequential `offset`, base64 `body` | `received` |
| `attachment_commit` | `upload_id` | verified `metadata` |
| `attachment_abort` | `upload_id` | completion |
| `attachment_info` | `digest` | private `metadata` |
| `attachment_read` | `digest`, `offset`, optional `preview: true` | base64 `body`, `eof`, optional escaped `preview_html` |
| `attachment_ref` | `digest`, `ref`, optional `remove: true` | completion |
| `attachment_gc` | optional `dry_run: false` | eligible `digests`; dry-run is default |

The Python agent offers `send_attachment(type, **fields)` and dispatches
results through its ordinary callback. The TypeScript client offers
`attachment(type, fields)` with explicit registration and attachment signer
callbacks. Both require a version-four welcome before emitting a request.

An object is at most 8 MiB, a chunk at most 32 KiB, a scope at most 256 MiB,
and the Hub admits at most four active uploads. Staging is discarded on socket
disconnect or restart;
an incomplete upload is never readable. Commit verifies length and SHA-256
before atomic publication. Reads reject expired content and verify the full
stored digest before yielding one bounded chunk. References keep expired objects
until explicitly removed; new references cannot be added after expiry;
admin GC deletes only expired objects with no references. The preview is
available only for `text/plain`, decodes with replacement, escapes HTML, and is
limited to the first 512 bytes of the first read chunk. Never render raw
attachment bytes as HTML.

The Hub offers no anonymous public metadata listing. Publish a separate
owner-reviewed reproducibility manifest with digest, length, provenance,
license, and a stable owner-controlled download URL when distribution is
intended; the content remains outside the public repository and public Hub
surfaces. Large datasets, checkpoints, and model weights belong on
owner-controlled storage, not in this bounded API or a public release.
