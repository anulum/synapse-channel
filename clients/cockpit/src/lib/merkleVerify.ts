// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — client-side RFC 6962 inclusion verification: proofs, not trust

// The dashboard serves the proof; this module refuses to take its word for
// it. The audit path is recomputed here, in the browser, with WebCrypto —
// the same RFC 6962 shape `core/merkle.py` builds and verifies (leaf =
// sha256(0x00 || event), node = sha256(0x01 || left || right), path split
// at the largest power of two below the size, consumed from the end). A
// verify that passes means the row is committed to the tree root even if
// the dashboard lied about everything else; a mismatched proof says so.

/** The proof document `/merkle-proof.json?seq=N` serves for a present seq. */
export interface InclusionProof {
  readonly seq: number;
  readonly index: number;
  readonly treeSize: number;
  readonly leaf: string;
  readonly path: readonly string[];
  readonly root: string;
}

/** A verify outcome the row can state plainly. */
export type VerifyResult =
  | { readonly kind: "verified"; readonly root: string }
  | { readonly kind: "mismatch" }
  | { readonly kind: "absent"; readonly note: string }
  | { readonly kind: "unserved" }
  | { readonly kind: "error"; readonly message: string };

/** Parse the proof feed's payload; null = not an object at all. */
export function parseProof(
  raw: unknown,
): { readonly present: true; readonly proof: InclusionProof } | { readonly present: false; readonly note: string } | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const record = raw as Record<string, unknown>;
  if (record["present"] === false) {
    return { present: false, note: typeof record["note"] === "string" ? record["note"] : "no event at that sequence" };
  }
  const path = Array.isArray(record["path"])
    ? record["path"].filter((node): node is string => typeof node === "string")
    : [];
  return {
    present: true,
    proof: {
      seq: typeof record["seq"] === "number" ? record["seq"] : 0,
      index: typeof record["index"] === "number" ? record["index"] : -1,
      treeSize: typeof record["tree_size"] === "number" ? record["tree_size"] : 0,
      leaf: typeof record["leaf"] === "string" ? record["leaf"] : "",
      path,
      root: typeof record["root"] === "string" ? record["root"] : "",
    },
  };
}

function hexToBytes(hex: string): Uint8Array | null {
  if (hex.length === 0 || hex.length % 2 !== 0 || /[^0-9a-fA-F]/.test(hex)) return null;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i += 1) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

function bytesToHex(bytes: Uint8Array): string {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function nodeHash(left: Uint8Array, right: Uint8Array): Promise<Uint8Array> {
  const joined = new Uint8Array(1 + left.length + right.length);
  joined[0] = 0x01; // RFC 6962 interior-node prefix
  joined.set(left, 1);
  joined.set(right, 1 + left.length);
  return new Uint8Array(await crypto.subtle.digest("SHA-256", joined));
}

function largestPowerOfTwoBelow(size: number): number {
  let power = 1;
  while (power * 2 < size) power *= 2;
  return power;
}

/** Expected audit-path length for `index` in a tree of `size`. */
export function auditPathLength(index: number, size: number): number {
  if (size <= 1) return 0;
  const split = largestPowerOfTwoBelow(size);
  if (index < split) return auditPathLength(index, split) + 1;
  return auditPathLength(index - split, size - split) + 1;
}

async function rootFromPath(
  index: number,
  size: number,
  leaf: Uint8Array,
  path: readonly Uint8Array[],
): Promise<Uint8Array> {
  if (size === 1) return leaf;
  const split = largestPowerOfTwoBelow(size);
  const sibling = path[path.length - 1] as Uint8Array;
  const rest = path.slice(0, -1);
  if (index < split) return nodeHash(await rootFromPath(index, split, leaf, rest), sibling);
  return nodeHash(sibling, await rootFromPath(index - split, size - split, leaf, rest));
}

/**
 * Recompute the root from the leaf and audit path and compare it to the
 * claimed root — the dashboard's arithmetic checked in the operator's own
 * browser. Malformed positions, wrong path lengths, and non-hex material
 * all read as a mismatch, never as verified.
 */
export async function verifyInclusion(proof: InclusionProof): Promise<boolean> {
  if (proof.index < 0 || proof.index >= proof.treeSize) return false;
  if (proof.path.length !== auditPathLength(proof.index, proof.treeSize)) return false;
  const leaf = hexToBytes(proof.leaf);
  if (leaf === null) return false;
  const path: Uint8Array[] = [];
  for (const node of proof.path) {
    const bytes = hexToBytes(node);
    if (bytes === null) return false;
    path.push(bytes);
  }
  const computed = bytesToHex(await rootFromPath(proof.index, proof.treeSize, leaf, path));
  return computed === proof.root.trim().toLowerCase();
}

const PROOF_URL = "/merkle-proof.json";

/** Fetch the proof for one sequence and verify it client-side. */
export async function fetchAndVerify(
  seq: number,
  fetcher: typeof fetch = fetch,
  url: string = PROOF_URL,
): Promise<VerifyResult> {
  try {
    const response = await fetcher(`${url}?seq=${Math.trunc(seq)}`);
    if (response.status === 404) return { kind: "unserved" };
    if (!response.ok) return { kind: "error", message: `hub returned ${response.status}` };
    const parsed = parseProof(await response.json());
    if (parsed === null) return { kind: "error", message: "proof payload was not an object" };
    if (!parsed.present) return { kind: "absent", note: parsed.note };
    return (await verifyInclusion(parsed.proof))
      ? { kind: "verified", root: parsed.proof.root }
      : { kind: "mismatch" };
  } catch (cause) {
    return { kind: "error", message: cause instanceof Error ? cause.message : String(cause) };
  }
}
