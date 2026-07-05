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

import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import {
  auditPathLength,
  fetchAndVerify,
  parseProof,
  verifyInclusion,
  type InclusionProof,
} from "../src/lib/merkleVerify";

function sha256(...parts: Buffer[]): Buffer {
  return createHash("sha256").update(Buffer.concat(parts)).digest();
}

const LEAVES = [0, 1, 2, 3, 4].map((n) => sha256(Buffer.from([0x00]), Buffer.from(`event-${n}`)));

function node(left: Buffer, right: Buffer): Buffer {
  return sha256(Buffer.from([0x01]), left, right);
}

// Tree of size 5 (split 4): root = node(node(node(l0,l1),node(l2,l3)), l4)
const N01 = node(LEAVES[0] as Buffer, LEAVES[1] as Buffer);
const N23 = node(LEAVES[2] as Buffer, LEAVES[3] as Buffer);
const N0123 = node(N01, N23);
const ROOT = node(N0123, LEAVES[4] as Buffer);

/** Audit path for leaf 2 in size 5, consumed from the END by the verifier. */
const PROOF_FOR_2: InclusionProof = {
  seq: 3,
  index: 2,
  treeSize: 5,
  leaf: (LEAVES[2] as Buffer).toString("hex"),
  // Recursion order: [deepest sibling first ... outermost last]
  path: [(LEAVES[3] as Buffer).toString("hex"), N01.toString("hex"), (LEAVES[4] as Buffer).toString("hex")],
  root: ROOT.toString("hex"),
};

describe("auditPathLength", () => {
  it("matches RFC 6962 positions", () => {
    expect(auditPathLength(0, 1)).toBe(0);
    expect(auditPathLength(2, 5)).toBe(3);
    expect(auditPathLength(4, 5)).toBe(1);
  });
});

describe("verifyInclusion", () => {
  it("verifies a known-answer proof and its single-leaf edge", async () => {
    expect(await verifyInclusion(PROOF_FOR_2)).toBe(true);
    const single = LEAVES[0] as Buffer;
    expect(
      await verifyInclusion({ seq: 1, index: 0, treeSize: 1, leaf: single.toString("hex"), path: [], root: single.toString("hex") }),
    ).toBe(true);
    // Right-arm recursion: leaf 4 of 5 has path [N0123].
    expect(
      await verifyInclusion({
        seq: 5,
        index: 4,
        treeSize: 5,
        leaf: (LEAVES[4] as Buffer).toString("hex"),
        path: [N0123.toString("hex")],
        root: ROOT.toString("hex"),
      }),
    ).toBe(true);
  });

  it("rejects tampered material, wrong positions, and non-hex", async () => {
    expect(await verifyInclusion({ ...PROOF_FOR_2, root: PROOF_FOR_2.root.replace(/^./, "f") })).toBe(false);
    expect(await verifyInclusion({ ...PROOF_FOR_2, leaf: (LEAVES[0] as Buffer).toString("hex") })).toBe(false);
    expect(await verifyInclusion({ ...PROOF_FOR_2, index: 9 })).toBe(false);
    expect(await verifyInclusion({ ...PROOF_FOR_2, path: PROOF_FOR_2.path.slice(1) })).toBe(false);
    expect(await verifyInclusion({ ...PROOF_FOR_2, leaf: "zz" })).toBe(false);
    expect(await verifyInclusion({ ...PROOF_FOR_2, path: ["zz", "aa", "bb"] })).toBe(false);
  });
});

describe("parseProof", () => {
  it("parses present and absent shapes, defaulting junk fields", () => {
    expect(parseProof(null)).toBeNull();
    expect(parseProof({ present: false, note: "past the log" })).toEqual({ present: false, note: "past the log" });
    expect(parseProof({ present: false })).toEqual({ present: false, note: "no event at that sequence" });
    const parsed = parseProof({ seq: 3, index: 2, tree_size: 5, leaf: "ab", path: ["cd", 7], root: "ef" });
    expect(parsed).toEqual({
      present: true,
      proof: { seq: 3, index: 2, treeSize: 5, leaf: "ab", path: ["cd"], root: "ef" },
    });
    const junk = parseProof({});
    expect(junk?.present === true && junk.proof.index).toBe(-1);
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
    ).toEqual({ kind: "error", message: "proof payload was not an object" });
    expect(await fetchAndVerify(1, vi.fn<typeof fetch>().mockRejectedValue(new Error("down")))).toEqual({
      kind: "error",
      message: "down",
    });
    expect(await fetchAndVerify(1, vi.fn<typeof fetch>().mockRejectedValue("plain"))).toEqual({
      kind: "error",
      message: "plain",
    });
  });

  it("runs on its defaults against the global fetch, which fails visibly in tests", async () => {
    expect((await fetchAndVerify(1)).kind).toBe("error");
  });
});
