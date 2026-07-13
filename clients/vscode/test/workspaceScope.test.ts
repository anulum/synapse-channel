// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for canonical editor workspace claim scopes

import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { workspaceClaimRequest } from "../src/workspaceScope.js";

describe("workspaceClaimRequest", () => {
  let temporary = "";

  beforeEach(async () => {
    temporary = await mkdtemp(join(tmpdir(), "synapse-vscode-scope-"));
  });

  afterEach(async () => {
    await rm(temporary, { recursive: true, force: true });
  });

  it("binds a nested workspace folder to its canonical Git root", async () => {
    const repository = join(temporary, "repo");
    const workspace = join(repository, "packages", "editor");
    const file = join(workspace, "src", "live.ts");
    await mkdir(join(repository, ".git"), { recursive: true });
    await mkdir(join(workspace, "src"), { recursive: true });
    await writeFile(file, "export {};\n");
    const result = workspaceClaimRequest(" editor/seat ", file, [workspace]);
    expect(result).toMatchObject({
      ok: true,
      request: {
        worktree: repository,
        paths: ["packages/editor/src/live.ts"],
      },
    });
    expect(result.ok && result.request.taskId).toMatch(/^vscode\/editor\/seat\/[0-9a-f]{16}$/);
  });

  it("produces stable per-file ids and distinct ids across roots and paths", async () => {
    const firstRoot = join(temporary, "first");
    const secondRoot = join(temporary, "second");
    const firstFile = join(firstRoot, "a.ts");
    const siblingFile = join(firstRoot, "b.ts");
    const secondFile = join(secondRoot, "a.ts");
    await mkdir(firstRoot);
    await mkdir(secondRoot);
    await writeFile(firstFile, "a\n");
    await writeFile(siblingFile, "b\n");
    await writeFile(secondFile, "a\n");
    const first = workspaceClaimRequest("seat", firstFile, [firstRoot, secondRoot]);
    const repeated = workspaceClaimRequest("seat", firstFile, [firstRoot]);
    const sibling = workspaceClaimRequest("seat", siblingFile, [firstRoot]);
    const second = workspaceClaimRequest("seat", secondFile, [firstRoot, secondRoot]);
    expect(first).toEqual(repeated);
    if (!first.ok || !sibling.ok || !second.ok) {
      throw new Error("Expected every in-root fixture to produce a claim request.");
    }
    expect(first.request.taskId).not.toBe(sibling.request.taskId);
    expect(first.request.taskId).not.toBe(second.request.taskId);
  });

  it("supports a missing final file but rejects broken parents and symlink escapes", async () => {
    const workspace = join(temporary, "workspace");
    const outside = join(temporary, "outside.ts");
    const escape = join(workspace, "escape.ts");
    await mkdir(workspace);
    await writeFile(outside, "outside\n");
    await symlink(outside, escape);
    expect(workspaceClaimRequest("", outside, [workspace])).toMatchObject({ ok: false });
    expect(workspaceClaimRequest("seat", join(workspace, "missing.ts"), [workspace])).toMatchObject({
      ok: true,
      request: { paths: ["missing.ts"] },
    });
    expect(workspaceClaimRequest("seat", join(workspace, "missing", "file.ts"), [workspace]))
      .toMatchObject({ ok: false });
    expect(workspaceClaimRequest("seat", outside, [workspace])).toMatchObject({ ok: false });
    expect(workspaceClaimRequest("seat", escape, [workspace])).toMatchObject({ ok: false });
  });
});
