// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — canonical editor workspace claim scopes

/** Bind file mutations to an exact canonical repository root and relative path. */

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { lstatSync, realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, join, relative, sep } from "node:path";

/** Exact claim inputs safe to send after a live protocol handshake. */
export interface WorkspaceClaimRequest {
  taskId: string;
  worktree: string;
  paths: [string];
}

/** Canonical Git worktree and worktree-relative path for one editor file. */
export interface WorkspaceFileScope {
  worktree: string;
  path: string;
}

/** A canonical request or a bounded non-reflective refusal. */
export type WorkspaceClaimResult =
  | { ok: true; request: WorkspaceClaimRequest }
  | { ok: false; reason: string };

function isWithin(root: string, candidate: string): boolean {
  const path = relative(root, candidate);
  return path === "" || (path !== ".." && !path.startsWith(`..${sep}`) && !isAbsolute(path));
}

function canonicalPath(path: string): string | undefined {
  try {
    return realpathSync.native(path);
  } catch {
    return undefined;
  }
}

function canonicalFilePath(path: string): string | undefined {
  const existing = canonicalPath(path);
  if (existing !== undefined) {
    return existing;
  }
  try {
    lstatSync(path);
    return undefined;
  } catch {
    const parent = canonicalPath(dirname(path));
    return parent === undefined ? undefined : join(parent, basename(path));
  }
}

function repositoryRoot(workspaceRoot: string): string | undefined {
  const env: NodeJS.ProcessEnv = { ...process.env, LC_ALL: "C" };
  for (const name of [
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
  ]) {
    delete env[name];
  }
  const result = spawnSync("git", ["-C", workspaceRoot, "rev-parse", "--show-toplevel"], {
    encoding: "utf8",
    env,
    timeout: 1000,
    killSignal: "SIGKILL",
    maxBuffer: 65_536,
    windowsHide: true,
  });
  if (result.error !== undefined) return undefined;
  if (result.status !== 0) {
    return result.status === 128 && result.stderr.includes("not a git repository")
      ? workspaceRoot : undefined;
  }
  const output = result.stdout.replace(/\r?\n$/, "");
  const root = canonicalPath(output);
  return root !== undefined && isWithin(root, workspaceRoot) ? root : undefined;
}

/** Resolve an editor file against the narrowest containing workspace root. */
export function workspaceFileScope(
  filePath: string,
  workspaceRoots: readonly string[],
): WorkspaceFileScope | undefined {
  const file = canonicalFilePath(filePath);
  if (file === undefined) {
    return undefined;
  }
  const containingRoots = workspaceRoots
    .map(canonicalPath)
    .filter((root): root is string => root !== undefined && isWithin(root, file))
    .sort((left, right) => right.length - left.length);
  const workspaceRoot = containingRoots[0];
  if (workspaceRoot === undefined) {
    return undefined;
  }
  const worktree = repositoryRoot(workspaceRoot);
  if (worktree === undefined) {
    return undefined;
  }
  const path = relative(worktree, file).split(sep).join("/");
  return path.length > 0 && isWithin(worktree, file) ? { worktree, path } : undefined;
}

function exactTaskId(identity: string, worktree: string, path: string): string {
  const digest = createHash("sha256")
    .update(worktree)
    .update("\0")
    .update(path)
    .digest("hex")
    .slice(0, 16);
  return `vscode/${identity}/${digest}`;
}

/** Build an exact per-file request or refuse unresolvable/escaping paths. */
export function workspaceClaimRequest(
  identity: string,
  filePath: string,
  workspaceRoots: readonly string[],
): WorkspaceClaimResult {
  const canonicalIdentity = identity.trim();
  if (canonicalIdentity.length === 0) {
    return { ok: false, reason: "SYNAPSE mutation withheld because the editor identity is empty." };
  }
  const scope = workspaceFileScope(filePath, workspaceRoots);
  if (scope === undefined) {
    return {
      ok: false,
      reason: "SYNAPSE mutation withheld because the file is not in a canonical workspace root.",
    };
  }
  return {
    ok: true,
    request: {
      taskId: exactTaskId(canonicalIdentity, scope.worktree, scope.path),
      worktree: scope.worktree,
      paths: [scope.path],
    },
  };
}
