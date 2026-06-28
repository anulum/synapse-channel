# Private channels design

Private channels are a design target for routing selected coordination messages
to a smaller namespace than the default public channel. They are not implemented
yet. Today, the hub is a trusted local hub: participants can use direct messages
and task scopes, but the hub does not enforce private channel membership.

Private channels solve audience control, not cryptographic secrecy. The feature
does not encrypt payloads, does not replace end-to-end encrypted channels, and
does not create cryptographic identity. The hub can still see metadata, route
events, enforce retention, and write durable logs.

## Namespace model

The first design should keep channel ids simple and inspectable:

- **Default public channel**: the current broadcast lane, visible to connected
  participants in a project.
- **Project channel**: a named channel under one project, such as
  `project:release`.
- **Worktree channel**: a channel tied to one checkout or branch group, such as
  `worktree:feature-a`.
- **Task channel**: a channel bound to one board task or claim id.
- **Direct channel**: a two-party or small recipient-set channel for explicit
  operator messages.

Every private channel needs a stable **channel id**, a display label, a project
or worktree anchor, and a **membership list**. Channel ids should be ordinary
strings in protocol payloads so logs, receipts, and CLI output can explain where
an event went.

## Membership lifecycle

Membership must be explicit and auditable:

- **Join policy** decides whether membership is owner-managed, invite-only, or
  derived from an active task claim.
- **Invitation** events add named participants or groups.
- **Leave policy** records voluntary departure and owner removal.
- **Membership audit** exposes who was allowed to receive a channel event at a
  given sequence.
- **Member removal** affects future delivery and retention views, but it cannot
  erase payloads already delivered to that participant.

Without per-agent identity and ACL enforcement, private channels should remain a
local coordination convenience. They should not be marketed as a hard security
boundary.

## Routing and history

Private-channel routing should preserve the existing hub properties:

- Delivery still uses connected agent names and durable inbox semantics.
- Board updates can reference a channel id without exposing a hidden body in the
  default public channel.
- Release receipts can name channel evidence by event sequence, channel id, and
  task id.
- Postmortems can list private-channel metadata and explain that body visibility
  depends on membership.

History visibility should be defined per channel. A new member may see no
history, bounded recent history, or all retained history, depending on the join
policy. The hub should record which policy applied.

## Retention and projections

Private channels need clear retention rules:

- A **retention boundary** limits chat history, progress notes, artifacts, and
  replay windows per channel.
- Relay log filtering should let operators export only channel metadata, one
  named channel, or the default public channel.
- Event-query filtering should support channel id, task id, and sequence ranges
  without requiring the query engine to read private payload bodies.
- Compaction and archive reports should include channel counts and retention
  decisions without leaking bodies to non-members.

These rules are operational, not cryptographic. If a payload body needs to stay
hidden from the hub, combine private channels with end-to-end encrypted
channels.

## Relationship to encryption

Private channels and end-to-end encrypted channels solve different problems:

- Private channels decide who should receive or review a payload.
- End-to-end encrypted channels hide selected payload bodies from the hub.

The first private-channel implementation should work without encryption so teams
can route operational chatter cleanly. Later, an encrypted private channel can
combine membership with encrypted bodies, but routing metadata remains visible.

## Boundaries

This is a design target, not implemented yet. Private channels do not encrypt
payloads, do not replace end-to-end encrypted channels, do not create
cryptographic identity, do not sandbox agents, and do not prevent a malicious or
compromised member from copying plaintext.

The trusted local hub remains the coordination authority. It can still see
channel ids, sender names, recipient names, task ids, timestamps, delivery
metadata, and retention metadata. Treat private channels as audience-scoping
inside trusted local coordination until per-agent identity, ACL enforcement, and
the [signed events and mTLS design](signed-events-mtls.md) exist. Signed events
can later bind channel ids and membership changes, but they do not encrypt
payloads.
