// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the JS client signs frames when the hub's welcome requires it

import { describe, expect, it } from "vitest";
import { SynapseClient, type WebSocketLike } from "../src/client.js";
import { frameSigningBytes } from "../src/frame-signature.js";
import { type Envelope, MessageType } from "../src/protocol.js";
import { seedSigner } from "./ed25519-seed.js";

const SEED = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f";

class FakeSocket implements WebSocketLike {
  sent: string[] = [];
  closed = false;
  onopen: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;

  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.closed = true;
    this.onclose?.({});
  }
  deliver(message: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
  envelopes(): Record<string, unknown>[] {
    return this.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
  }
}

function signedWelcome(nonce: string): Record<string, unknown> {
  return { type: MessageType.Welcome, sender: "SynapseHub", protocol_version: 4,
    hub_id: "hub-x", session_nonce: nonce, frame_signatures: "required" };
}

function makeClient(extra: Record<string, unknown> = {}): {
  client: SynapseClient; sockets: FakeSocket[];
} {
  const sockets: FakeSocket[] = [];
  const client = new SynapseClient({
    uri: "ws://localhost:8876",
    name: "P/alice",
    webSocketFactory: () => {
      const socket = new FakeSocket();
      sockets.push(socket);
      return socket;
    },
    ...extra,
  });
  return { client, sockets };
}

function envelopeOf(frame: Record<string, unknown>): Record<string, unknown> {
  return frame["frame_signature"] as Record<string, unknown>;
}

describe("SynapseClient frame signing", () => {
  const signer = seedSigner(SEED, "machine:abc");

  it("signs every frame after registration, numbered from one", async () => {
    const { client, sockets } = makeClient({ frameSigner: signer });
    const connected = client.connect();
    const socket = sockets[0]!;
    socket.onopen?.({});
    socket.deliver(signedWelcome("nonce-1"));
    await connected;
    client.claim("t-1", [], undefined, { taskOnly: true });
    client.requestWho();
    const [registration, claim, who] = socket.envelopes();
    expect(registration).not.toHaveProperty("frame_signature");
    expect(envelopeOf(claim!)).toMatchObject({
      version: 1, algorithm: "ed25519", key_id: "machine:abc", hub_id: "hub-x",
      session_nonce: "nonce-1", seq: 1,
    });
    expect(envelopeOf(who!)["seq"]).toBe(2);
    const value = Buffer.from(envelopeOf(claim!)["value"] as string, "base64");
    expect(signer.verify(frameSigningBytes(claim as Envelope), value)).toBe(true);
    client.close();
  });

  it("covers the per-message auth envelope it attaches first", async () => {
    const { client, sockets } = makeClient({
      frameSigner: signer,
      signAttachment: (frame: Envelope) => ({ ...frame, auth: { kid: "k", value: "hmac" } }),
    });
    const connected = client.connect();
    sockets[0]!.onopen?.({});
    sockets[0]!.deliver(signedWelcome("nonce-1"));
    await connected;
    client.attachment(MessageType.AttachmentInfo, { scope: "P", digest: "a" });
    const frame = sockets[0]!.envelopes().at(-1)!;
    expect(frame["auth"]).toEqual({ kid: "k", value: "hmac" });
    const value = Buffer.from(envelopeOf(frame)["value"] as string, "base64");
    expect(signer.verify(frameSigningBytes(frame as Envelope), value)).toBe(true);
    client.close();
  });

  it("starts a new session on every connection", async () => {
    const { client, sockets } = makeClient({ frameSigner: signer });
    const first = client.connect();
    sockets[0]!.onopen?.({});
    sockets[0]!.deliver(signedWelcome("nonce-1"));
    await first;
    client.requestState();
    client.close();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const second = client.connect();
    const socket = sockets[1]!;
    socket.onopen?.({});
    // Nothing is signed for the ended connection before the new welcome.
    expect(socket.envelopes()[0]).not.toHaveProperty("frame_signature");
    socket.deliver(signedWelcome("nonce-2"));
    await second;
    client.requestState();
    expect(envelopeOf(socket.envelopes().at(-1)!)).toMatchObject({ session_nonce: "nonce-2", seq: 1 });
    client.close();
  });

  it("refuses a hub that requires signed frames when no signer is configured", async () => {
    const { client, sockets } = makeClient();
    const connected = client.connect();
    sockets[0]!.onopen?.({});
    sockets[0]!.deliver(signedWelcome("nonce-1"));
    await expect(connected).rejects.toThrow(/requires signed frames; configure frameSigner/);
    expect(sockets[0]!.closed).toBe(true);
    expect(client.isReady).toBe(false);
  });

  it("sends plain frames to a hub that does not ask", async () => {
    const { client, sockets } = makeClient({ frameSigner: signer });
    const connected = client.connect();
    sockets[0]!.onopen?.({});
    sockets[0]!.deliver({ type: MessageType.Welcome, sender: "SynapseHub", hub_id: "h",
      session_nonce: "n" });
    await connected;
    client.requestWho();
    expect(sockets[0]!.envelopes().at(-1)).not.toHaveProperty("frame_signature");
    client.close();
  });
});
