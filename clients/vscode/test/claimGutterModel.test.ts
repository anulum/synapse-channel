// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for the pure claim-gutter projection

import { describe, expect, it } from "vitest";
import {
  buildGutterPlan,
  gutterClaimsForFile,
  type GutterClaim,
  type SymbolNode,
} from "../src/claimGutterModel.js";

function fileClaim(owner = "other", mine = false): GutterClaim {
  return {
    owner,
    mine,
    scope: { kind: "file", claimedPath: "src" },
  };
}

function symbolClaim(symbol: string, owner = "other", mine = false): GutterClaim {
  return {
    owner,
    mine,
    scope: {
      kind: "symbol",
      claimedPath: `src/a.ts/.synapse-symbol/${symbol.replaceAll(".", "/")}`,
      sourcePath: "src/a.ts",
      symbol,
    },
  };
}

const symbols: SymbolNode[] = [
  {
    name: "Worker",
    qualifiedName: "Worker",
    startLine: 2,
    endLine: 8,
    children: [
      {
        name: "run",
        qualifiedName: "Worker.run",
        startLine: 4,
        endLine: 6,
        children: [],
      },
    ],
  },
];

describe("gutterClaimsForFile", () => {
  it("projects exact, directory, and whole-worktree claims onto a file", () => {
    expect(
      gutterClaimsForFile([{ owner: "me", paths: ["src/a.ts"] }], "me", "src/a.ts"),
    ).toEqual([
      {
        owner: "me",
        mine: true,
        scope: { kind: "file", claimedPath: "src/a.ts" },
      },
    ]);
    expect(
      gutterClaimsForFile([{ owner: "other", paths: ["src"] }], "me", "src/a.ts"),
    ).toEqual([
      {
        owner: "other",
        mine: false,
        scope: { kind: "file", claimedPath: "src" },
      },
    ]);
    expect(
      gutterClaimsForFile([{ owner: "other", paths: [] }], "me", "src/a.ts")[0]?.scope,
    ).toEqual({ kind: "file", claimedPath: "" });
    expect(
      gutterClaimsForFile([{ owner: "other" }], "me", "src/a.ts")[0]?.scope,
    ).toEqual({ kind: "file", claimedPath: "" });
  });

  it("normalises separators without accepting files outside the workspace", () => {
    expect(
      gutterClaimsForFile([{ owner: "me", paths: ["src"] }], "me", "src\\a.ts"),
    ).toHaveLength(1);
    expect(
      gutterClaimsForFile([{ owner: "me", paths: [""] }], "me", "/tmp/a.ts"),
    ).toEqual([]);
    expect(
      gutterClaimsForFile([{ owner: "me", paths: [""] }], "me", "../a.ts"),
    ).toEqual([]);
  });

  it("keeps semantic claims narrow and decodes their canonical components", () => {
    const claims = gutterClaimsForFile(
      [
        {
          owner: "other",
          paths: ["src/a.ts/.synapse-symbol/Worker/handle%20request"],
        },
      ],
      "me",
      "src/a.ts",
    );
    expect(claims).toEqual([
      {
        owner: "other",
        mine: false,
        scope: {
          kind: "symbol",
          claimedPath: "src/a.ts/.synapse-symbol/Worker/handle%20request",
          sourcePath: "src/a.ts",
          symbol: "Worker.handle request",
        },
      },
    ]);
    expect(claims[0]?.scope.kind).not.toBe("file");
  });

  it("ignores semantic scopes for other files and malformed encodings", () => {
    expect(
      gutterClaimsForFile(
        [{ owner: "other", paths: ["src/b.ts/.synapse-symbol/run"] }],
        "me",
        "src/a.ts",
      ),
    ).toEqual([]);
    expect(
      gutterClaimsForFile(
        [{ owner: "other", paths: ["src/a.ts/.synapse-symbol/%ZZ"] }],
        "me",
        "src/a.ts",
      ),
    ).toEqual([]);
  });

  it("prefers a whole-file claim over narrower stale semantic records", () => {
    const claims = gutterClaimsForFile(
      [
        { owner: "me", paths: ["src/a.ts/.synapse-symbol/run"] },
        { owner: "me", paths: ["src/a.ts"] },
      ],
      "me",
      "src/a.ts",
    );
    expect(claims).toEqual([
      {
        owner: "me",
        mine: true,
        scope: { kind: "file", claimedPath: "src/a.ts" },
      },
    ]);
  });
});

describe("buildGutterPlan", () => {
  it("marks every visible line but one full-file overview span", () => {
    const claim = fileClaim();
    const plan = buildGutterPlan(
      [claim],
      [],
      [
        { startLine: 2, endLine: 4 },
        { startLine: 7, endLine: 7 },
      ],
      10,
    );
    expect(plan.lines.map((mark) => mark.line)).toEqual([2, 3, 4, 7]);
    expect(plan.lines.every((mark) => mark.claim === claim && !mark.unresolved)).toBe(true);
    expect(plan.spans).toEqual([
      { startLine: 0, endLine: 9, claim, unresolved: false },
    ]);
  });

  it("limits a semantic claim to its resolved symbol and viewport intersection", () => {
    const claim = symbolClaim("Worker.run", "me", true);
    const plan = buildGutterPlan(
      [claim],
      symbols,
      [{ startLine: 3, endLine: 5 }],
      12,
    );
    expect(plan.lines.map((mark) => mark.line)).toEqual([4, 5]);
    expect(plan.spans).toEqual([
      { startLine: 4, endLine: 6, claim, unresolved: false },
    ]);
  });

  it("uses one honest alert line when a semantic range is unresolved", () => {
    const claim = symbolClaim("Worker.missing");
    const plan = buildGutterPlan(
      [claim],
      symbols,
      [{ startLine: 5, endLine: 9 }],
      12,
    );
    expect(plan.lines).toEqual([{ line: 5, claim, unresolved: true }]);
    expect(plan.spans).toEqual([
      { startLine: 5, endLine: 5, claim, unresolved: true },
    ]);
  });

  it("clamps provider spans and de-duplicates overlapping visible ranges", () => {
    const claim = symbolClaim("run");
    const outOfBounds: SymbolNode[] = [
      {
        name: "run",
        qualifiedName: "run",
        startLine: -4,
        endLine: 50,
        children: [],
      },
    ];
    const plan = buildGutterPlan(
      [claim],
      outOfBounds,
      [
        { startLine: -2, endLine: 2 },
        { startLine: 2, endLine: 20 },
      ],
      4,
    );
    expect(plan.lines.map((mark) => mark.line)).toEqual([0, 1, 2, 3]);
    expect(plan.spans[0]).toMatchObject({ startLine: 0, endLine: 3, unresolved: false });
  });

  it("returns no decorations for an impossible empty document", () => {
    expect(buildGutterPlan([fileClaim()], [], [{ startLine: 0, endLine: 1 }], 0)).toEqual({
      lines: [],
      spans: [],
    });
  });
});
