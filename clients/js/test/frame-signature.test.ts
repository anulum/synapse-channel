// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the JS client reproduces the shared frame-signature vectors

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  FRAME_SIGNATURE_DOMAIN,
  FrameCanonicalError,
  frameCanonicalJson,
  frameSigningBytes,
  sessionFromWelcome,
  signSessionFrame,
} from "../src/frame-signature.js";
import type { Envelope } from "../src/protocol.js";
import { seedSigner } from "./ed25519-seed.js";

interface Vectors {
  canonical: { input: string; canonical: string }[];
  signature: {
    private_key_seed_hex: string;
    key_id: string;
    hub_id: string;
    session_nonce: string;
    seq: number;
    frame: Envelope;
    signed_frame: Envelope;
  };
}

const VECTORS = JSON.parse(readFileSync(
  new URL("../../../tests/fixtures/frame_signature_vectors.json", import.meta.url), "utf-8",
)) as Vectors;

describe("frame canonical JSON", () => {
  it("reproduces every shared RFC 8785 vector", () => {
    expect(VECTORS.canonical.length).toBeGreaterThan(0);
    for (const vector of VECTORS.canonical) {
      expect(frameCanonicalJson(JSON.parse(vector.input))).toBe(vector.canonical);
    }
  });

  it("drops the members a JSON encoder drops, as the hub never sees them", () => {
    expect(frameCanonicalJson({ b: 1, a: undefined, c: [undefined] })).toBe('{"b":1,"c":[null]}');
  });

  it("refuses values the hub could not verify a signature over", () => {
    const refused: unknown[] = [
      { n: Number.NaN }, { n: Number.POSITIVE_INFINITY }, { s: "a\ud800" }, { s: "\udc00b" },
      { b: 1n },
    ];
    for (const value of refused) {
      expect(() => frameCanonicalJson(value)).toThrow(FrameCanonicalError);
    }
    expect(frameCanonicalJson({ n: 2 ** 53 - 1, f: 1e21, e: "é😀" }))
      .toBe('{"e":"é😀","f":1e+21,"n":9007199254740991}');
  });
});

describe("frame signatures", () => {
  it("reproduces the shared deterministic signature byte for byte", () => {
    const vector = VECTORS.signature;
    const signer = seedSigner(vector.private_key_seed_hex, vector.key_id);
    const session = { hubId: vector.hub_id, sessionNonce: vector.session_nonce, seq: vector.seq - 1 };
    const signed = signSessionFrame(vector.frame, session, signer);
    expect(signed).toEqual(vector.signed_frame);
    expect(session.seq).toBe(vector.seq);
    const value = (signed["frame_signature"] as { value: string }).value;
    expect(signer.verify(frameSigningBytes(signed), Buffer.from(value, "base64"))).toBe(true);
  });

  it("covers the domain and every field but the value", () => {
    const vector = VECTORS.signature;
    const bytes = new TextDecoder().decode(frameSigningBytes(vector.signed_frame));
    expect(bytes.startsWith(FRAME_SIGNATURE_DOMAIN)).toBe(true);
    expect(bytes).not.toContain('"value"');
    expect(bytes).toContain('"session_nonce":"nonce-1"');
  });

  it("advances the sequence only when a frame could be signed", () => {
    const signer = seedSigner(VECTORS.signature.private_key_seed_hex, "k");
    const session = { hubId: "h", sessionNonce: "n", seq: 4 };
    const frame = { sender: "P/a", type: "claim", target: "System", payload: "", timestamp: 1 };
    const refused = [{ size: 2 ** 60 }, { nested: [{ size: -(2 ** 53) }] }, { n: Number.NaN },
      { note: "\ud800" }];
    for (const extra of refused) {
      expect(() => signSessionFrame({ ...frame, ...extra }, session, signer)).toThrow(FrameCanonicalError);
    }
    const failing = { keyId: "k", sign: (): Uint8Array => { throw new Error("key unavailable"); } };
    expect(() => signSessionFrame(frame, session, failing)).toThrow(/key unavailable/);
    expect(session.seq).toBe(4);
    // From 1e21 the wire form has an exponent and the hub reads a double.
    expect(() => signSessionFrame({ ...frame, big: 1e21 }, session, signer)).not.toThrow();
    session.seq = 4;
    const signed = signSessionFrame(frame, session, signer);
    expect((signed["frame_signature"] as { seq: number }).seq).toBe(5);
    expect(frame).not.toHaveProperty("frame_signature");
  });

  it("opens a session only when the welcome requires signed frames", () => {
    const welcome = { sender: "hub", type: "welcome", target: "a", payload: "", timestamp: 1,
      hub_id: "h", session_nonce: "n", frame_signatures: "required" };
    expect(sessionFromWelcome(welcome)).toEqual({ hubId: "h", sessionNonce: "n", seq: 0 });
    expect(sessionFromWelcome({ ...welcome, frame_signatures: "accepted" })).toBeNull();
    expect(sessionFromWelcome({ ...welcome, session_nonce: "" })).toBeNull();
    expect(sessionFromWelcome({ ...welcome, hub_id: 5 })).toBeNull();
  });
});
