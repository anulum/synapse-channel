<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Bounded streaming responses

A single chat reply is right for a short answer. A long generation or a
long-running task wants to deliver its output incrementally, and Synapse supports
that as an explicit, **bounded** stream: one `open` frame, ordered `chunk`
frames, and a terminal `done` or `abort` frame, all tagged with one stream id.

## Transport: WebSocket frames, not SSE

Streams are WebSocket frames carried over the hub's existing chat path. The hub
is already a WebSocket bus that routes typed envelopes, so a stream is a sequence
of chat-carried frames rather than a new transport. Server-Sent Events would
require a separate one-way HTTP endpoint, and MCP/A2A streaming is specific to
those bridges; the core stream rides the connection an agent already holds. Each
frame is an ordinary chat envelope with stream metadata (`kind: "stream"`, a
`stream_id`, a `stream_seq`, a `frame_type`, and the chunk `text`), so a non-stream
client simply sees chat frames it can ignore.

`stream_seq` is the zero-based frame counter. The sender also emits legacy
`seq` for old readers on journal-less paths. A journal-backed hub assigns
`seq` its durable row cursor; the stream decoder therefore prefers
`stream_seq`. It accepts legacy `seq` only when the dedicated field is absent.
A malformed dedicated field is refused without falling back. Old frames whose
counter was overwritten by a journal cannot be reconstructed by guessing an
offset. Journal cursors and stream order have separate meanings.

## Bounds are the point

A stream declares a `StreamBounds` ceiling and may not exceed it:

- `max_chunks` — how many `chunk` frames before the producer must finish;
- `max_chunk_bytes` — the largest single chunk body (UTF-8 bytes);
- `max_total_bytes` — the cumulative body size across all chunks;
- `ttl_seconds` — a declared lifetime, currently not enforced by producer or consumer.

The producer refuses a frame that exceeds its count or byte ceiling. The
consumer validates parsed frames against its own configured count/byte limits
and expected sequence; bounds are not carried in the frames or negotiated.
Neither class enforces a time limit. Applications must add deadline, stalled
stream and upstream cancellation handling. A refused frame does not stop a
model generation or spend, and `stream_reply` does not automatically emit an
abort frame when an iterator, send or bound check fails.

## Sending and receiving

A worker or agent streams a reply with one call:

```python
stream_id = await agent.stream_reply(
    ["partial ", "answer ", "text"],
    target="planner",
)
```

A receiver reassembles the frames it sees:

```python
from synapse_channel.core.streaming import StreamConsumer, parse_stream_frame

consumer = StreamConsumer(stream_id)
for message in received_messages:  # the agent's inbound chat frames
    frame = parse_stream_frame(message)
    if frame is not None and frame.stream_id == stream_id:
        consumer.accept(frame)  # raises on a bound/order violation
if consumer.closed and not consumer.aborted:
    use(consumer.text)  # the reassembled body
```

## Retention is shallow and explicit

A stream is **transient** coordination, not durable task state. The frames use
ordinary chat transport. Intermediate chunks can be stored in the chat journal
and relay log under their
configured retention. Replaying task state does not automatically reassemble
a stream body. The application must separately persist its **final reply and
release receipt**; `stream_reply` does not create them or prove receipt by a
consumer. A fully non-journalled stream message
type — ephemeral delivery that never touches the relay log — is a deliberate
later tranche, called out here so the retention boundary is explicit today.

The lite relay file is an observation surface; reading it does not send frames
to another hub. An explicit application bridge can decode its full envelopes
and re-submit stream fields through SDK chat, preserving `stream_seq` while the
destination hub assigns a new journal `seq`. The human-readable `synapse relay`
output is not a complete stream-frame export. Multi-hub log observation also
does not automatically deliver a stream to a recipient or reassemble its body.

## Boundaries

- The library checks configured count and byte bounds; this is not a hub-wide
  stream admission limit or a guarantee that every caller uses those helpers.
- Streams are **transient, not durable task state**, but chunks can persist as
  chat records. Record final results and release receipts separately.
- The transport is **the existing WebSocket chat path**, not a new SSE endpoint
  or an always-on streaming service; it adds no new local-core dependency.
- Ordering is validated by a configured consumer; transport delivery is not
  guaranteed. A missing terminal frame leaves the consumer open without an
  automatic deadline, recovery or cancellation mechanism.
