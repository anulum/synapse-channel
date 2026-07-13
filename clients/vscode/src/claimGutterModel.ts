// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — pure claim-to-editor gutter projection

/**
 * Pure scope and line-range decisions for the VS Code claim gutter.
 *
 * File and directory claims cover every visible line. Synthetic semantic claim
 * paths are kept narrow: they decorate only the matching document-symbol range,
 * or one explicit unresolved marker when the editor cannot map that symbol.
 */

import { type RawClaim } from "./fleetModel.js";

const SEMANTIC_SCOPE_MARKER = "/.synapse-symbol/";

/** A line range whose ends are inclusive. */
export interface LineSpan {
  startLine: number;
  endLine: number;
}

/** Editor symbol data reduced to the fields needed by the pure planner. */
export interface SymbolNode extends LineSpan {
  name: string;
  qualifiedName: string;
  children: SymbolNode[];
}

interface FileScope {
  kind: "file";
  claimedPath: string;
}

interface SymbolScope {
  kind: "symbol";
  claimedPath: string;
  sourcePath: string;
  symbol: string;
}

/** One active claim that affects the open file. */
export interface GutterClaim {
  owner: string;
  mine: boolean;
  scope: FileScope | SymbolScope;
}

/** One visible gutter marker with enough context for an honest hover. */
export interface GutterLineMark {
  line: number;
  claim: GutterClaim;
  unresolved: boolean;
}

/** One overview-ruler span for a file or resolved semantic scope. */
export interface GutterSpanMark extends LineSpan {
  claim: GutterClaim;
  unresolved: boolean;
}

/** Complete editor-agnostic decoration plan. */
export interface GutterPlan {
  lines: GutterLineMark[];
  spans: GutterSpanMark[];
}

function normaliseClaimPath(path: string): string {
  const value = path.trim().replace(/\\/g, "/");
  if (value.startsWith("/") || /[\u0000-\u001f\u007f]/u.test(value)) {
    return "";
  }
  const segments: string[] = [];
  for (const segment of value.split("/")) {
    if (segment === "" || segment === ".") {
      continue;
    }
    if (segment === "..") {
      return "";
    }
    segments.push(segment);
  }
  return segments.join("/");
}

function normaliseEditorPath(path: string): string | undefined {
  const value = path.trim().replace(/\\/g, "/");
  if (!value || value.startsWith("/") || /[\u0000-\u001f\u007f]/u.test(value)) {
    return undefined;
  }
  const segments: string[] = [];
  for (const segment of value.split("/")) {
    if (segment === "" || segment === ".") {
      continue;
    }
    if (segment === "..") {
      return undefined;
    }
    segments.push(segment);
  }
  return segments.length > 0 ? segments.join("/") : undefined;
}

function parseSymbolScope(path: string): SymbolScope | undefined {
  const markerAt = path.lastIndexOf(SEMANTIC_SCOPE_MARKER);
  if (markerAt <= 0) {
    return undefined;
  }
  const sourcePath = path.slice(0, markerAt);
  const encoded = path.slice(markerAt + SEMANTIC_SCOPE_MARKER.length);
  if (!encoded) {
    return undefined;
  }
  try {
    const components = encoded.split("/").map((component) => decodeURIComponent(component));
    if (components.some((component) => !component || component === "." || component === "..")) {
      return undefined;
    }
    return {
      kind: "symbol",
      claimedPath: path,
      sourcePath,
      symbol: components.join("."),
    };
  } catch {
    return undefined;
  }
}

function affectsFile(claimedPath: string, filePath: string): boolean {
  return claimedPath === ""
    || claimedPath === filePath
    || filePath.startsWith(`${claimedPath}/`);
}

/**
 * Project raw hub claims onto one workspace-relative file.
 *
 * Empty path sets retain the hub's whole-worktree meaning. A semantic descendant
 * is never treated as a whole-file claim: it becomes a symbol target only when
 * its source path exactly matches this file.
 */
export function gutterClaimsForFile(
  claims: readonly RawClaim[],
  selfName: string,
  worktree: string,
  filePath: string,
): GutterClaim[] {
  const canonicalFile = normaliseEditorPath(filePath);
  if (canonicalFile === undefined) {
    return [];
  }

  const matches: GutterClaim[] = [];
  const seen = new Set<string>();
  for (const claim of claims) {
    if (claim.worktree !== worktree) {
      continue;
    }
    const owner = claim.owner ?? "";
    const paths = claim.paths === undefined || claim.paths.length === 0 ? [""] : claim.paths;
    for (const rawPath of paths) {
      const claimedPath = normaliseClaimPath(rawPath);
      const symbolScope = parseSymbolScope(claimedPath);
      let scope: FileScope | SymbolScope | undefined;
      if (symbolScope !== undefined) {
        if (symbolScope.sourcePath === canonicalFile) {
          scope = symbolScope;
        }
      } else if (affectsFile(claimedPath, canonicalFile)) {
        scope = { kind: "file", claimedPath };
      }
      if (scope === undefined) {
        continue;
      }
      const key = `${owner}\u0000${scope.kind}\u0000${scope.claimedPath}`;
      if (!seen.has(key)) {
        seen.add(key);
        matches.push({ owner, mine: owner === selfName, scope });
      }
    }
  }

  const fileClaim = matches.find((claim) => claim.scope.kind === "file");
  return fileClaim === undefined ? matches : [fileClaim];
}

function flattenSymbols(nodes: readonly SymbolNode[]): SymbolNode[] {
  const flattened: SymbolNode[] = [];
  for (const node of nodes) {
    flattened.push(node, ...flattenSymbols(node.children));
  }
  return flattened;
}

function symbolSpan(symbol: string, nodes: readonly SymbolNode[]): LineSpan | undefined {
  const match = flattenSymbols(nodes).find((node) => node.qualifiedName === symbol);
  return match === undefined
    ? undefined
    : { startLine: match.startLine, endLine: match.endLine };
}

function clampSpan(span: LineSpan, lineCount: number): LineSpan | undefined {
  if (lineCount < 1 || !Number.isFinite(span.startLine) || !Number.isFinite(span.endLine)) {
    return undefined;
  }
  const startLine = Math.max(0, Math.min(lineCount - 1, Math.trunc(span.startLine)));
  const endLine = Math.max(0, Math.min(lineCount - 1, Math.trunc(span.endLine)));
  return endLine < startLine ? undefined : { startLine, endLine };
}

function visibleLines(span: LineSpan, visible: readonly LineSpan[], lineCount: number): number[] {
  const lines = new Set<number>();
  const boundedSpan = clampSpan(span, lineCount);
  if (boundedSpan === undefined) {
    return [];
  }
  for (const rawVisible of visible) {
    const boundedVisible = clampSpan(rawVisible, lineCount);
    if (boundedVisible === undefined) {
      continue;
    }
    const start = Math.max(boundedSpan.startLine, boundedVisible.startLine);
    const end = Math.min(boundedSpan.endLine, boundedVisible.endLine);
    for (let line = start; line <= end; line += 1) {
      lines.add(line);
    }
  }
  return [...lines].sort((a, b) => a - b);
}

function unresolvedLine(visible: readonly LineSpan[], lineCount: number): number {
  for (const range of visible) {
    const bounded = clampSpan(range, lineCount);
    if (bounded !== undefined) {
      return bounded.startLine;
    }
  }
  return 0;
}

/** Build visible gutter markers and whole-document overview spans. */
export function buildGutterPlan(
  claims: readonly GutterClaim[],
  symbols: readonly SymbolNode[],
  visible: readonly LineSpan[],
  lineCount: number,
): GutterPlan {
  if (lineCount < 1) {
    return { lines: [], spans: [] };
  }

  const lines: GutterLineMark[] = [];
  const spans: GutterSpanMark[] = [];
  for (const claim of claims) {
    let span: LineSpan | undefined;
    let unresolved = false;
    if (claim.scope.kind === "file") {
      span = { startLine: 0, endLine: lineCount - 1 };
    } else {
      span = symbolSpan(claim.scope.symbol, symbols);
      if (span === undefined) {
        const line = unresolvedLine(visible, lineCount);
        span = { startLine: line, endLine: line };
        unresolved = true;
      }
    }
    const bounded = clampSpan(span, lineCount);
    if (bounded === undefined) {
      continue;
    }
    spans.push({ ...bounded, claim, unresolved });
    for (const line of visibleLines(bounded, visible, lineCount)) {
      lines.push({ line, claim, unresolved });
    }
  }
  return { lines, spans };
}
