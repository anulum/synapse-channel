// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — per-connection frame signatures over RFC 8785 canonical JSON

import type { Envelope } from "./protocol.js";

/** Frame field that carries the signature envelope. */
export const FRAME_SIGNATURE_FIELD = "frame_signature";

/** Prefix of the signed bytes; separates frame signatures from every other signature. */
export const FRAME_SIGNATURE_DOMAIN = "synapse-channel:frame-signature:v1\u0000";

/** Raised when a frame holds a value the hub could not verify a signature over. */
export class FrameCanonicalError extends Error {
  override readonly name = "FrameCanonicalError";
}

/** Signs frames with the identity key that registered the connection. */
export interface FrameSigner {
  /** Identifier of that key, as named in the registration signature. */
  readonly keyId: string;
  /** Return the raw 64-byte Ed25519 signature of `message`. */
  sign(message: Uint8Array): Uint8Array;
}

/** The hub and session a connection's welcome names, and its last signed sequence. */
export interface FrameSession {
  readonly hubId: string;
  readonly sessionNonce: string;
  seq: number;
}

const LONE_SURROGATE = /[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/;

/** Refuse what the wire form would silently change: non-finite numbers and bigints. */
function checkSource(value: unknown, depth: number): void {
  if (depth > 512) {
    throw new FrameCanonicalError("frame nesting is too deep to sign");
  }
  if (typeof value === "number" && !Number.isFinite(value)) {
    throw new FrameCanonicalError("a signed frame cannot hold NaN or an infinity");
  }
  if (typeof value === "bigint") {
    throw new FrameCanonicalError("a signed frame cannot hold a bigint");
  }
  if (Array.isArray(value)) {
    for (const item of value) checkSource(item, depth + 1);
  } else if (value !== null && typeof value === "object") {
    for (const item of Object.values(value)) checkSource(item, depth + 1);
  }
}

function render(value: unknown): string {
  if (value === null || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    return JSON.stringify(value);
  }
  if (typeof value === "string") {
    if (LONE_SURROGATE.test(value)) {
      throw new FrameCanonicalError("a signed frame cannot hold a lone surrogate");
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(render).join(",")}]`;
  }
  const record = value as Record<string, unknown>;
  // Default sort compares UTF-16 code units, as RFC 8785 requires.
  return `{${Object.keys(record).sort().map((key) => `${render(key)}:${render(record[key])}`).join(",")}}`;
}

/**
 * Return the RFC 8785 canonical JSON of a value as the hub receives it.
 *
 * The value is first reduced to its JSON wire form, so members a JSON encoder
 * drops are dropped here too. Numbers must be finite and strings well-formed
 * UTF-16, as the hub checks.
 */
export function frameCanonicalJson(value: unknown): string {
  checkSource(value, 0);
  let wire: unknown;
  try {
    wire = JSON.parse(JSON.stringify(value)) as unknown;
  } catch (error) {
    throw new FrameCanonicalError(error instanceof Error ? error.message : "frame is not JSON");
  }
  return render(wire);
}

/** Return the bytes a frame signature covers: the domain, then the frame without the value. */
export function frameSigningBytes(frame: Envelope): Uint8Array {
  const unsigned: Record<string, unknown> = { ...frame };
  const envelope = unsigned[FRAME_SIGNATURE_FIELD];
  if (envelope !== null && typeof envelope === "object" && !Array.isArray(envelope)) {
    const { value: _value, ...rest } = envelope as Record<string, unknown>;
    unsigned[FRAME_SIGNATURE_FIELD] = rest;
  }
  return new TextEncoder().encode(FRAME_SIGNATURE_DOMAIN + frameCanonicalJson(unsigned));
}

/** Return a new session when the welcome requires signed frames, otherwise `null`. */
export function sessionFromWelcome(welcome: Envelope): FrameSession | null {
  const hubId = welcome["hub_id"];
  const sessionNonce = welcome["session_nonce"];
  if (welcome["frame_signatures"] !== "required" || typeof hubId !== "string" || hubId === "" ||
      typeof sessionNonce !== "string" || sessionNonce === "") {
    return null;
  }
  return { hubId, sessionNonce, seq: 0 };
}

/**
 * Refuse a number the hub would read as an integer beyond ±(2**53 − 1).
 *
 * Below 1e21 JSON writes a whole number as plain digits, which the hub reads
 * as an integer and refuses outside the I-JSON exact range; from 1e21 it
 * writes an exponent, which the hub reads as a double.
 */
function checkWireIntegers(value: unknown): void {
  if (typeof value === "number") {
    if (Number.isInteger(value) && !Number.isSafeInteger(value) && Math.abs(value) < 1e21) {
      throw new FrameCanonicalError(`integer ${value} is outside the I-JSON exact range`);
    }
  } else if (Array.isArray(value)) {
    for (const item of value) checkWireIntegers(item);
  } else if (value !== null && typeof value === "object") {
    for (const item of Object.values(value)) checkWireIntegers(item);
  }
}

function base64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/**
 * Return a copy of `frame` signed as the next frame of `session`.
 *
 * A frame the hub could not verify is refused with {@link FrameCanonicalError}
 * before anything is signed, including a whole number the hub would read as
 * an integer beyond ±(2**53 − 1). The sequence advances only when signing
 * succeeds.
 */
export function signSessionFrame(frame: Envelope, session: FrameSession, signer: FrameSigner): Envelope {
  checkSource(frame, 0);
  checkWireIntegers(frame);
  const seq = session.seq + 1;
  const signed: Envelope = {
    ...frame,
    [FRAME_SIGNATURE_FIELD]: {
      version: 1,
      algorithm: "ed25519",
      key_id: signer.keyId,
      hub_id: session.hubId,
      session_nonce: session.sessionNonce,
      seq,
    },
  };
  const value = base64(signer.sign(frameSigningBytes(signed)));
  (signed[FRAME_SIGNATURE_FIELD] as Record<string, unknown>)["value"] = value;
  session.seq = seq;
  return signed;
}
