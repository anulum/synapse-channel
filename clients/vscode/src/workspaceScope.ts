// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — canonical editor workspace claim scopes

/** Bind file mutations to an exact canonical repository root and relative path. */

import { createHash } from "node:crypto";
import { existsSync, lstatSync, realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, join, parse, relative, sep } from "node:path";

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

function repositoryRoot(workspaceRoot: string): string {
  let cursor = workspaceRoot;
  const filesystemRoot = parse(cursor).root;
  while (true) {
    if (existsSync(join(cursor, ".git"))) {
      return cursor;
    }
    if (cursor === filesystemRoot) {
      return workspaceRoot;
    }
    cursor = dirname(cursor);
  }
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
