// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — tests for canonical editor workspace claim scopes

import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative, sep } from "node:path";
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
    await mkdir(join(workspace, "src"), { recursive: true });
    const initialized = spawnSync("git", ["init", "--quiet", repository], {
      encoding: "utf8", timeout: 5000,
    });
    expect(initialized.status, initialized.stderr).toBe(0);
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
    const discovered = spawnSync("git", ["-C", workspace, "rev-parse", "--show-toplevel"], {
      encoding: "utf8",
      timeout: 5000,
      env: { ...process.env, LC_ALL: "C" },
    });
    expect(discovered.error).toBeUndefined();
    expect([0, 128]).toContain(discovered.status);
    if (discovered.status === 128) {
      expect(discovered.stderr).toContain("not a git repository");
    }
    const expectedRoot = realpathSync.native(
      discovered.status === 0 ? discovered.stdout.trim() : workspace,
    );
    expect(workspaceClaimRequest("", outside, [workspace])).toMatchObject({ ok: false });
    expect(workspaceClaimRequest("seat", join(workspace, "missing.ts"), [workspace])).toMatchObject({
      ok: true,
      request: {
        worktree: expectedRoot,
        paths: [
          relative(expectedRoot, join(realpathSync.native(workspace), "missing.ts"))
            .split(sep).join("/"),
        ],
      },
    });
    expect(workspaceClaimRequest("seat", join(workspace, "missing", "file.ts"), [workspace]))
      .toMatchObject({ ok: false });
    expect(workspaceClaimRequest("seat", outside, [workspace])).toMatchObject({ ok: false });
    expect(workspaceClaimRequest("seat", escape, [workspace])).toMatchObject({ ok: false });
  });

  it("ignores a parent marker that Git does not recognize as a repository", async () => {
    const parent = join(temporary, "invalid-repository");
    const workspace = join(parent, "workspace");
    const file = join(workspace, "live.ts");
    await mkdir(join(parent, ".git"), { recursive: true });
    await mkdir(workspace);
    await writeFile(file, "export {};\n");
    expect(workspaceClaimRequest("seat", file, [workspace])).toMatchObject({
      ok: true,
      request: { worktree: realpathSync.native(workspace), paths: ["live.ts"] },
    });
  });

  it("refuses an unverifiable scope when Git cannot be executed", async () => {
    const workspace = join(temporary, "no-git");
    const emptyPath = join(temporary, "empty-bin");
    const file = join(workspace, "live.ts");
    await mkdir(workspace);
    await mkdir(emptyPath);
    await writeFile(file, "export {};\n");
    const previous = process.env.PATH;
    try {
      process.env.PATH = emptyPath;
      expect(workspaceClaimRequest("seat", file, [workspace])).toMatchObject({ ok: false });
    } finally {
      if (previous === undefined) delete process.env.PATH;
      else process.env.PATH = previous;
    }
  });

  it("preserves meaningful whitespace in a real Git root", async () => {
    const repository = join(temporary, "repository with whitespace ");
    const file = join(repository, "live.ts");
    await mkdir(repository);
    const initialized = spawnSync("git", ["init", "--quiet", repository], {
      encoding: "utf8", timeout: 5000,
    });
    expect(initialized.status, initialized.stderr).toBe(0);
    await writeFile(file, "export {};\n");
    expect(workspaceClaimRequest("seat", file, [repository])).toMatchObject({
      ok: true,
      request: { worktree: realpathSync.native(repository), paths: ["live.ts"] },
    });
  });

  it("uses the narrowest ordinary workspace and refuses its empty relative path", async () => {
    const outer = join(temporary, "ordinary");
    const nested = join(outer, "nested");
    const file = join(nested, "live.ts");
    await mkdir(nested, { recursive: true });
    await writeFile(file, "export {};\n");
    expect(workspaceClaimRequest("seat", file, [outer, nested])).toMatchObject({
      ok: true,
      request: { worktree: realpathSync.native(nested), paths: ["live.ts"] },
    });
    expect(workspaceClaimRequest("seat", nested, [nested])).toMatchObject({ ok: false });
  });

  it("does not inherit a foreign Git directory or worktree override", async () => {
    const repository = join(temporary, "repository");
    const foreign = join(temporary, "foreign");
    for (const path of [repository, foreign]) {
      const initialized = spawnSync("git", ["init", "--quiet", path], {
        encoding: "utf8", timeout: 5000,
      });
      expect(initialized.status, initialized.stderr).toBe(0);
    }
    const file = join(repository, "live.ts");
    await writeFile(file, "export {};\n");
    const previousDirectory = process.env.GIT_DIR;
    const previousWorktree = process.env.GIT_WORK_TREE;
    try {
      process.env.GIT_DIR = join(foreign, ".git");
      process.env.GIT_WORK_TREE = foreign;
      expect(workspaceClaimRequest("seat", file, [repository])).toMatchObject({
        ok: true,
        request: { worktree: realpathSync.native(repository), paths: ["live.ts"] },
      });
    } finally {
      if (previousDirectory === undefined) delete process.env.GIT_DIR;
      else process.env.GIT_DIR = previousDirectory;
      if (previousWorktree === undefined) delete process.env.GIT_WORK_TREE;
      else process.env.GIT_WORK_TREE = previousWorktree;
    }
  });

  it("refuses Git metadata that redirects the worktree outside the selected workspace", async () => {
    const repository = join(temporary, "repository");
    const workspace = join(temporary, "workspace");
    const initialized = spawnSync("git", ["init", "--quiet", repository], {
      encoding: "utf8", timeout: 5000,
    });
    expect(initialized.status, initialized.stderr).toBe(0);
    const configured = spawnSync("git", ["-C", repository, "config", "core.worktree", repository], {
      encoding: "utf8", timeout: 5000,
    });
    expect(configured.status, configured.stderr).toBe(0);
    await mkdir(workspace);
    await writeFile(join(workspace, ".git"), `gitdir: ${join(repository, ".git")}\n`);
    const file = join(workspace, "live.ts");
    await writeFile(file, "export {};\n");
    expect(workspaceClaimRequest("seat", file, [workspace])).toMatchObject({ ok: false });
  });
});
