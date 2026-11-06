// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — a deterministic Ed25519 frame signer for tests

import { createPrivateKey, createPublicKey, sign, verify } from "node:crypto";
import type { FrameSigner } from "../src/frame-signature.js";

/** An Ed25519 signer from a raw 32-byte seed, through the PKCS#8 wrapping Node expects. */
export function seedSigner(seedHex: string, keyId: string): FrameSigner & { verify(message: Uint8Array, signature: Uint8Array): boolean } {
  const key = createPrivateKey({
    key: Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), Buffer.from(seedHex, "hex")]),
    format: "der",
    type: "pkcs8",
  });
  const publicKey = createPublicKey(key);
  return {
    keyId,
    sign: (message) => new Uint8Array(sign(null, message, key)),
    verify: (message, signature) => verify(null, message, publicKey, signature),
  };
}
