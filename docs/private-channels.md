# Private channels

Private channels route selected coordination messages to a smaller namespace
than the default public channel.

Private channels solve audience control, not cryptographic secrecy. The feature
does not encrypt payloads, does not replace end-to-end encrypted channels, and
does not create cryptographic identity. The hub can still see metadata, route
events, enforce retention, and write durable logs.

## Implemented runtime

The private-channel runtime is implemented for local coordination. The hub keeps a
:class:`~synapse_channel.core.channels.ChannelRegistry`, and:

- `synapse channel create <id>` makes a channel whose creator is its first
  member; `synapse channel join <id>` / `leave <id>` change membership;
  `synapse channel list` shows the channels an agent belongs to; and
  `synapse channel history <id>` returns the retained live history only to a
  current member. The client exposes `channel_create` / `channel_join` /
  `channel_leave` / `request_channels` / `request_channel_history`.
- `synapse send --channel <id>` (and `SynapseAgent.chat(..., channel=<id>)`)
  delivers a chat **only to that channel's online members**. A non-member sender
  is refused; a non-member never receives the body. Channel chat is not
  broadcast and is not retained in the public chat history.
- Channel chat is retained in the channel's bounded member-only channel history,
  journalled as `chat` events with an explicit `channel` id, and mirrored to the
  relay log with that same channel id. `synapse relay --channel <id>` selects one channel,
  `synapse relay --public-only` selects the default public lane, and
  `synapse relay --channel-metadata` hides private-channel bodies while showing
  sender, target, timestamp, type, and channel id.
- `synapse event-query <db> "channel <id> between seq <start> <end>"` filters
  channel chat by channel id and sequence range. Its channel result is
  metadata-only: sequence, timestamp, kind, sender, target, channel id, message
  id, and payload byte length, not the private payload body.

Join is open in this runtime (any agent may join a channel by id) so teams can
route operational chatter cleanly; who-may-join authorization is the future
identity/ACL layer. Membership is in-memory and lives for the hub process.
The live history retention boundary is the hub's `max_history` window per
channel. Durable event-store channel records are available for forensic
filtering, but channel membership itself is not durable across hub restarts.

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

History visibility is currently member-only and bounded recent history: a
current member can request the retained channel window, and a non-member gets a
private refusal without message bodies. The broader policy matrix — no-history,
invite-time snapshots, all retained history, and owner-managed history reads —
remains a design target for the identity/ACL layer.

## Retention and projections

Private channels have these runtime retention and projection rules:

- A **retention boundary** limits live chat history per channel to the hub's
  bounded history window. Progress notes, artifacts, and replay windows remain
  outside this tranche.
- Relay log filtering can export one named channel, the default public channel,
  or channel metadata without private-channel bodies.
- Event-query filtering supports channel id and sequence/time ranges without
  rendering private payload bodies.
- Compaction and archive reports should include channel counts and retention
  decisions without leaking bodies to non-members.

These rules are operational, not cryptographic. If a payload body needs to stay
hidden from the hub, combine private channels with end-to-end encrypted
channels.

## Relationship to encryption

Private channels and end-to-end encrypted channels solve different problems:

- Private channels decide who should receive or review a payload.
- End-to-end encrypted channels hide selected payload bodies from the hub.
- The [differential-privacy blackboard design](differential-privacy-blackboard.md)
  shapes aggregate or redacted shared board views after data exists.

The private-channel runtime works without encryption so teams can route
operational chatter cleanly. An encrypted private channel can combine membership
with encrypted bodies, but routing metadata remains visible.

## Boundaries

Private channels do not encrypt payloads, do not replace end-to-end encrypted
channels, do not create
cryptographic identity, do not sandbox agents, and do not prevent a malicious or
compromised member from copying plaintext.

The trusted local hub remains the coordination authority. It can still see
channel ids, sender names, recipient names, task ids, timestamps, delivery
metadata, and retention metadata. Treat private channels as audience-scoping
inside trusted local coordination until per-agent identity, ACL enforcement, and
the [signed events and mTLS design](signed-events-mtls.md) exist. The
[identity and ACL design](identity-and-acl.md) is the future layer for join,
leave, publish, and history-read authorization. Signed events can later bind
channel ids and membership changes, but they do not encrypt payloads.
