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
`stream_id`, a `seq`, a `frame_type`, and the chunk `text`), so a non-stream
client simply sees chat frames it can ignore.

## Bounds are the point

A stream declares a `StreamBounds` ceiling and may not exceed it:

- `max_chunks` — how many `chunk` frames before the producer must finish;
- `max_chunk_bytes` — the largest single chunk body (UTF-8 bytes);
- `max_total_bytes` — the cumulative body size across all chunks;
- `ttl_seconds` — the wall-clock budget for the whole stream.

The producer refuses a frame that would breach any ceiling — a runaway
generation is stopped at the source, not on the bus — and the consumer enforces
the same ceiling and strict in-order delivery on the receiving side, so a
malformed or oversized stream is rejected rather than reassembled.

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
for message in received_messages:        # the agent's inbound chat frames
    frame = parse_stream_frame(message)
    if frame is not None and frame.stream_id == stream_id:
        consumer.accept(frame)           # raises on a bound/order violation
if consumer.closed and not consumer.aborted:
    use(consumer.text)                   # the reassembled body
```

## Retention is shallow and explicit

A stream is **transient** coordination, not durable task state. The frames ride
the chat path and are therefore subject to the same relay mirroring as chat, but
they are bounded by the producer, so their footprint is capped rather than
open-ended. The durable record of what a task produced is its **final reply and
release receipt**, not the intermediate chunks: the event log and replay
reconstruct task state, not stream bodies. A fully non-journalled stream message
type — ephemeral delivery that never touches the relay log — is a deliberate
later tranche, called out here so the retention boundary is explicit today.

## Boundaries

- A stream is **bounded**: the producer refuses to exceed its declared
  `StreamBounds`, so no stream is unbounded.
- Streams are **transient, not replay state**: do not rely on replaying chunks;
  rely on the final reply and the release receipt.
- The transport is **the existing WebSocket chat path**, not a new SSE endpoint
  or an always-on streaming service; it adds no new local-core dependency.
- Delivery and ordering are **enforced**: the consumer rejects an out-of-order,
  oversized, or post-close frame rather than silently accepting it.
