// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — client-side inclusion verification tests
//
// The known-answer tree is built here with the same RFC 6962 arithmetic
// (leaf = sha256(0x00||data), node = sha256(0x01||l||r)) so verifyInclusion
// is checked against independently computed material, not against itself.
// The material is computed with WebCrypto — the browser primitive the
// verifier itself runs on — never Node's Buffer, which the cockpit has no
// business depending on.

import { describe, expect, it, vi } from "vitest";
import {
  auditPathLength,
  fetchAndVerify,
  parseProof,
  verifyInclusion,
  type InclusionProof,
} from "../src/lib/merkleVerify";

async function sha256(...parts: readonly Uint8Array[]): Promise<Uint8Array> {
  const joined = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    joined.set(part, offset);
    offset += part.length;
  }
  return new Uint8Array(await crypto.subtle.digest("SHA-256", joined));
}

function hex(bytes: Uint8Array): string {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

const utf8 = new TextEncoder();

const LEAVES = (await Promise.all(
  [0, 1, 2, 3, 4].map((n) => sha256(Uint8Array.of(0x00), utf8.encode(`event-${n}`))),
)) as [Uint8Array, Uint8Array, Uint8Array, Uint8Array, Uint8Array];

function node(left: Uint8Array, right: Uint8Array): Promise<Uint8Array> {
  return sha256(Uint8Array.of(0x01), left, right);
}

// Tree of size 5 (split 4): root = node(node(node(l0,l1),node(l2,l3)), l4)
const N01 = await node(LEAVES[0], LEAVES[1]);
const N23 = await node(LEAVES[2], LEAVES[3]);
const N0123 = await node(N01, N23);
const ROOT = await node(N0123, LEAVES[4]);

/** Audit path for leaf 2 in size 5, consumed from the END by the verifier. */
const PROOF_FOR_2: InclusionProof = {
  seq: 3,
  index: 2,
  treeSize: 5,
  leaf: hex(LEAVES[2]),
  // Recursion order: [deepest sibling first ... outermost last]
  path: [hex(LEAVES[3]), hex(N01), hex(LEAVES[4])],
  root: hex(ROOT),
};

describe("auditPathLength", () => {
  it("matches RFC 6962 positions", () => {
    expect(auditPathLength(0, 1)).toBe(0);
    expect(auditPathLength(2, 5)).toBe(3);
    expect(auditPathLength(4, 5)).toBe(1);
  });
});

describe("verifyInclusion", () => {
  it("refuses a proof that moves a node boundary into its leaf and path", async () => {
    // The two leaves joined, L0 || L1, hash under 0x01 to the real two-leaf root. Split anywhere
    // else (one byte as the leaf, the other 63 as the one path entry), they reach the same root
    // for a leaf that is not in the log. Every hash must be 32 bytes.
    const joined = new Uint8Array([...LEAVES[0], ...LEAVES[1]]);
    const [head, rest] = [joined.slice(0, 1), joined.slice(1)];
    expect(hex(await sha256(Uint8Array.of(0x01), head, rest))).toBe(hex(N01));
    for (const [leaf, entry] of [
      [head, rest],
      [new Uint8Array(0), joined],
    ] as const) {
      expect(
        await verifyInclusion({
          seq: 1,
          index: 0,
          treeSize: 2,
          leaf: hex(leaf),
          path: [hex(entry)],
          root: hex(N01),
        }),
      ).toBe(false);
    }
  });

  it("verifies a known-answer proof and its single-leaf edge", async () => {
    expect(await verifyInclusion(PROOF_FOR_2)).toBe(true);
    const single = hex(LEAVES[0]);
    expect(
      await verifyInclusion({ seq: 1, index: 0, treeSize: 1, leaf: single, path: [], root: single }),
    ).toBe(true);
    // Right-arm recursion: leaf 4 of 5 has path [N0123].
    expect(
      await verifyInclusion({
        seq: 5,
        index: 4,
        treeSize: 5,
        leaf: hex(LEAVES[4]),
        path: [hex(N0123)],
        root: hex(ROOT),
      }),
    ).toBe(true);
  });

  it("rejects tampered material, wrong positions, and non-hex", async () => {
    expect(await verifyInclusion({ ...PROOF_FOR_2, root: PROOF_FOR_2.root.replace(/^./, "f") })).toBe(false);
    expect(await verifyInclusion({ ...PROOF_FOR_2, leaf: hex(LEAVES[0]) })).toBe(false);
    expect(await verifyInclusion({ ...PROOF_FOR_2, index: 9 })).toBe(false);
    expect(await verifyInclusion({ ...PROOF_FOR_2, path: PROOF_FOR_2.path.slice(1) })).toBe(false);
    expect(await verifyInclusion({ ...PROOF_FOR_2, leaf: "zz" })).toBe(false);
    expect(await verifyInclusion({ ...PROOF_FOR_2, path: ["zz", "aa", "bb"] })).toBe(false);
  });
  it.each([
    { leaf: "ab" },
    { path: ["ab", ...PROOF_FOR_2.path.slice(1)] },
    { root: "ab" },
    { index: 2.5 },
    { seq: 3.5 },
    { treeSize: Number.MAX_SAFE_INTEGER + 1 },
  ])("refuses malformed proof fields before hashing (%o)", async (changes) => {
    const digest = vi.spyOn(crypto.subtle, "digest");
    try {
      const proof = { ...PROOF_FOR_2, root: "00".repeat(32), ...changes };
      expect(await verifyInclusion(proof)).toBe(false);
      expect(digest).not.toHaveBeenCalled();
    } finally {
      digest.mockRestore();
    }
  });

  it("requires lower-case SHA-256 text", async () => {
    expect(await verifyInclusion({ ...PROOF_FOR_2, root: PROOF_FOR_2.root.toUpperCase() })).toBe(false);
  });
});

describe("parseProof", () => {
  const payload = {
    seq: 3, index: 2, tree_size: 5,
    leaf: PROOF_FOR_2.leaf, path: PROOF_FOR_2.path, root: PROOF_FOR_2.root,
  };

  it("parses well-typed present and absent proofs", () => {
    expect(parseProof(null)).toBeNull();
    expect(parseProof({ present: false, note: "past the log" })).toEqual({ present: false, note: "past the log" });
    expect(parseProof({ present: false })).toEqual({ present: false, note: "no event at that sequence" });
    expect(parseProof(payload)).toEqual({ present: true, proof: PROOF_FOR_2 });
  });

  it.each([
    {},
    { ...payload, seq: true },
    { ...payload, index: false },
    { ...payload, tree_size: true },
    { ...payload, leaf: 12 },
    { ...payload, path: "ab" },
    { ...payload, path: [12, ...payload.path] },
    { ...payload, root: 12 },
  ])("refuses malformed payloads (%o)", (malformed) => {
    expect(parseProof(malformed)).toBeNull();
  });
});

describe("fetchAndVerify", () => {
  it("maps the outcome ladder end to end", async () => {
    const good = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ seq: 3, index: 2, tree_size: 5, leaf: PROOF_FOR_2.leaf, path: PROOF_FOR_2.path, root: PROOF_FOR_2.root })),
    );
    expect(await fetchAndVerify(3, good)).toEqual({ kind: "verified", root: PROOF_FOR_2.root });
    expect(good.mock.calls[0]?.[0]).toBe("/merkle-proof.json?seq=3");

    const lying = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ seq: 3, index: 2, tree_size: 5, leaf: PROOF_FOR_2.leaf, path: PROOF_FOR_2.path, root: "00" })),
    );
    expect(await fetchAndVerify(3, lying)).toEqual({ kind: "mismatch" });

    expect(
      await fetchAndVerify(9, vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ present: false, note: "past" })))),
    ).toEqual({ kind: "absent", note: "past" });
    expect(
      await fetchAndVerify(1, vi.fn<typeof fetch>().mockResolvedValue(new Response("nf", { status: 404 }))),
    ).toEqual({ kind: "unserved" });
    expect(
      await fetchAndVerify(1, vi.fn<typeof fetch>().mockResolvedValue(new Response("boom", { status: 500 }))),
    ).toEqual({ kind: "error", message: "hub returned 500" });
    expect(
      await fetchAndVerify(1, vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([1])))),
    ).toEqual({ kind: "error", message: "malformed proof payload" });
    expect(await fetchAndVerify(1, vi.fn<typeof fetch>().mockRejectedValue(new Error("down")))).toEqual({
      kind: "error",
      message: "down",
    });
    expect(await fetchAndVerify(1, vi.fn<typeof fetch>().mockRejectedValue("plain"))).toEqual({
      kind: "error",
      message: "plain",
    });
  });


  it("checks the returned sequence before hashing", async () => {
    const digest = vi.spyOn(crypto.subtle, "digest");
    try {
      const response = new Response(JSON.stringify({
        seq: 4, index: 2, tree_size: 5,
        leaf: PROOF_FOR_2.leaf, path: PROOF_FOR_2.path, root: "00".repeat(32),
      }));
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response);
      expect(await fetchAndVerify(3, fetcher)).toEqual({ kind: "mismatch" });
      expect(digest).not.toHaveBeenCalled();
    } finally {
      digest.mockRestore();
    }
  });

  it("does not make a request for a non-integer sequence", async () => {
    const fetcher = vi.fn<typeof fetch>();
    expect((await fetchAndVerify(3.5, fetcher)).kind).toBe("error");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("runs on its defaults against the global fetch, which fails visibly in tests", async () => {
    expect((await fetchAndVerify(1)).kind).toBe("error");
  });
});
