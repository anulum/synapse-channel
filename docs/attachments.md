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
federated event log. An optional private recipient-granted peer API serves individual objects. A digest is an identifier, never a credential.

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
digest, upload ID, or object. Local requests remain project-bound; cross-hub reads
use the separate source-owned permissions below.

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


## Recipient-granted cross-hub reads (wire version 6)

Enable `--attachment-recipient-policy FILE` alongside the complete local attachment
posture and `--multihub-serving-policy FILE`. The source hub refuses to start if either
feature is absent. The recipient policy is a UTF-8 JSON file owned by the operator,
mode `0600`, without a symlink or hard-link alias, at most 65,536 bytes:

```json
{
  "version": 1,
  "grants": [
    {
      "recipient_hub": "hub-b",
      "scope": "PROJECT",
      "digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "expires_at": 1790870400
    }
  ]
}
```

Use the actual object digest and a future Unix expiry. Each of at most 256 grants
names exactly one recipient hub, project and lowercase SHA-256 digest. Wildcards,
unknown or duplicate fields, duplicate recipient/object tuples and non-finite
expiries are refused. An empty list grants nothing.

The source checks the requesting socket's pinned client certificate or verified
identity-key registration through its peer serving policy, including namespace,
peering expiry and revocation. The peering must explicitly grant the `read` verb
for that namespace. A namespace grant alone never permits content.
It then reloads the recipient file and checks its exact object grant and expiry
before any digest lookup. Both metadata and chunks require these checks. Source
content expiry also denies metadata, even when a reference retains the bytes.

Atomically replace the file with an owner-only replacement to revoke or revise
permissions without restarting the hub. The next request, including a retry on
an already-open socket, observes the replacement. A missing, invalid or newly
public file denies all reads. One accepted request uses one policy snapshot;
revocation cannot retract a chunk already authorised or delivered. Reads never
add or remove source references and never change source garbage-collection rules.

A peer registers with `protocol_version: 6` and sends `attachment_peer_request`
with `action` (`info` or `read`), `scope`, `digest`, and, for `read`, an integer
`offset`. The private `attachment_peer_result` returns `ok: true` plus `metadata`,
or `scope`, `digest`, `offset`, base64 `body` and boolean `eof`. Every handler refusal
is exactly `ok: false, error: "attachment unavailable"`, without metadata or bytes.
Earlier connection authentication failures retain their ordinary private refusal.
The read-only peer frames use the verified connection's identity; local attachment
frames retain their durable per-message authentication, ACL and role requirements.
No peer upload, reference mutation, listing or GC operation is exposed.

The Python API is `synapse_channel.core.attachment_transport.request_attachment`.
It checks the source hub id on every response, negotiates version six before
requesting content, bounds frames and chunks, validates response fields and returns
chunk bodies as bytes. Across hosts, supply a verifying `ssl_context` and `wss://`
URI; an identity-key registration can prove the recipient through a TLS proxy.
The source id check supplements TLS server authentication. The caller validates the
assembled length and complete SHA-256 digest before committing to its local Core
store; interrupted local Core uploads still require a new session-bound token.
Fleet transfer staging and resumability are separate consumer responsibilities.
Older peers and clients keep their earlier local attachment and forwarding APIs.
