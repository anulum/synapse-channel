// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — VS Code claim-gutter renderer

/** Render claim scope without owning any hub or credential state. */

import * as vscode from "vscode";
import {
  buildGutterPlan,
  gutterClaimsForFile,
  type GutterLineMark,
  type GutterSpanMark,
  type LineSpan,
  type SymbolNode,
} from "./claimGutterModel.js";
import { type RawClaim } from "./fleetModel.js";

type ProviderSymbol = vscode.DocumentSymbol | vscode.SymbolInformation;

function isDocumentSymbol(symbol: ProviderSymbol): symbol is vscode.DocumentSymbol {
  return "range" in symbol && "children" in symbol;
}

function documentSymbolNode(
  symbol: vscode.DocumentSymbol,
  parents: readonly string[],
): SymbolNode {
  const names = [...parents, symbol.name];
  return {
    name: symbol.name,
    qualifiedName: names.join("."),
    startLine: symbol.range.start.line,
    endLine: symbol.range.end.line,
    children: symbol.children.map((child) => documentSymbolNode(child, names)),
  };
}

function symbolInformationNode(symbol: vscode.SymbolInformation): SymbolNode {
  const container = symbol.containerName.trim();
  return {
    name: symbol.name,
    qualifiedName: container ? `${container}.${symbol.name}` : symbol.name,
    startLine: symbol.location.range.start.line,
    endLine: symbol.location.range.end.line,
    children: [],
  };
}

function providerSymbolNodes(symbols: readonly ProviderSymbol[]): SymbolNode[] {
  return symbols.map((symbol) =>
    isDocumentSymbol(symbol)
      ? documentSymbolNode(symbol, [])
      : symbolInformationNode(symbol),
  );
}

function visibleLineSpans(editor: vscode.TextEditor): LineSpan[] {
  return editor.visibleRanges.map((range) => ({
    startLine: range.start.line,
    endLine: range.end.line,
  }));
}

function lineRange(document: vscode.TextDocument, lineNumber: number): vscode.Range {
  const line = document.lineAt(lineNumber);
  return new vscode.Range(lineNumber, 0, lineNumber, Math.max(1, line.text.length));
}

function spanRange(document: vscode.TextDocument, span: LineSpan): vscode.Range {
  const end = document.lineAt(span.endLine);
  return new vscode.Range(span.startLine, 0, span.endLine, Math.max(1, end.text.length));
}

function claimOwner(mark: GutterLineMark): string {
  const owner = mark.claim.owner || "an unknown agent";
  return mark.claim.mine ? `you (${owner})` : owner;
}

function hoverMessage(mark: GutterLineMark): vscode.MarkdownString {
  const hover = new vscode.MarkdownString();
  if (mark.claim.scope.kind === "file") {
    hover.appendText(
      `SYNAPSE file-scope claim held by ${claimOwner(mark)}. The lease covers every line in this file.`,
    );
  } else if (mark.unresolved) {
    hover.appendText(
      `SYNAPSE symbol claim ${mark.claim.scope.symbol} is held by ${claimOwner(mark)}, `
      + "but this editor has no matching document-symbol range. This is an alert marker only; "
      + "it does not claim the marked line or widen the lease to the whole file.",
    );
  } else {
    hover.appendText(
      `SYNAPSE symbol claim ${mark.claim.scope.symbol} is held by ${claimOwner(mark)}. `
      + "The gutter follows the editor's resolved symbol range.",
    );
  }
  return hover;
}

function decorationOptions(
  document: vscode.TextDocument,
  marks: readonly GutterLineMark[],
): vscode.DecorationOptions[] {
  return marks.map((mark) => ({
    range: lineRange(document, mark.line),
    hoverMessage: hoverMessage(mark),
  }));
}

function decorationRanges(
  document: vscode.TextDocument,
  marks: readonly GutterSpanMark[],
): vscode.Range[] {
  return marks.map((mark) => spanRange(document, mark));
}

/** Theme-aware, visible-line-bounded claim decorations for open editors. */
export class ClaimGutter implements vscode.Disposable {
  private readonly ownGutter: vscode.TextEditorDecorationType;
  private readonly otherGutter: vscode.TextEditorDecorationType;
  private readonly ownOverview: vscode.TextEditorDecorationType;
  private readonly otherOverview: vscode.TextEditorDecorationType;
  private readonly epochs = new WeakMap<vscode.TextEditor, number>();

  constructor(extensionUri: vscode.Uri) {
    const media = vscode.Uri.joinPath(extensionUri, "media");
    const ownLight = vscode.Uri.joinPath(media, "claim-mine-light.svg");
    const ownDark = vscode.Uri.joinPath(media, "claim-mine-dark.svg");
    const otherLight = vscode.Uri.joinPath(media, "claim-other-light.svg");
    const otherDark = vscode.Uri.joinPath(media, "claim-other-dark.svg");
    this.ownGutter = vscode.window.createTextEditorDecorationType({
      gutterIconPath: ownDark,
      gutterIconSize: "80%",
      rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
      light: { gutterIconPath: ownLight },
      dark: { gutterIconPath: ownDark },
    });
    this.otherGutter = vscode.window.createTextEditorDecorationType({
      gutterIconPath: otherDark,
      gutterIconSize: "80%",
      rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
      light: { gutterIconPath: otherLight },
      dark: { gutterIconPath: otherDark },
    });
    this.ownOverview = vscode.window.createTextEditorDecorationType({
      isWholeLine: true,
      overviewRulerColor: new vscode.ThemeColor("editorInfo.foreground"),
      overviewRulerLane: vscode.OverviewRulerLane.Left,
      rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
    });
    this.otherOverview = vscode.window.createTextEditorDecorationType({
      isWholeLine: true,
      overviewRulerColor: new vscode.ThemeColor("editorWarning.foreground"),
      overviewRulerLane: vscode.OverviewRulerLane.Left,
      rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
    });
  }

  private clear(editor: vscode.TextEditor): void {
    editor.setDecorations(this.ownGutter, []);
    editor.setDecorations(this.otherGutter, []);
    editor.setDecorations(this.ownOverview, []);
    editor.setDecorations(this.otherOverview, []);
  }

  async render(
    editor: vscode.TextEditor,
    claims: readonly RawClaim[],
    selfName: string,
  ): Promise<void> {
    const epoch = (this.epochs.get(editor) ?? 0) + 1;
    this.epochs.set(editor, epoch);
    const path = vscode.workspace.asRelativePath(editor.document.uri, false);
    const targets = gutterClaimsForFile(claims, selfName, path);
    if (targets.length === 0) {
      this.clear(editor);
      return;
    }

    let symbols: SymbolNode[] = [];
    if (targets.some((claim) => claim.scope.kind === "symbol")) {
      try {
        const result = await vscode.commands.executeCommand<ProviderSymbol[]>(
          "vscode.executeDocumentSymbolProvider",
          editor.document.uri,
        );
        symbols = providerSymbolNodes(result ?? []);
      } catch {
        symbols = [];
      }
    }
    if (this.epochs.get(editor) !== epoch || !vscode.window.visibleTextEditors.includes(editor)) {
      return;
    }

    const plan = buildGutterPlan(
      targets,
      symbols,
      visibleLineSpans(editor),
      editor.document.lineCount,
    );
    const ownLines = plan.lines.filter((mark) => mark.claim.mine);
    const otherLines = plan.lines.filter((mark) => !mark.claim.mine);
    const ownSpans = plan.spans.filter((mark) => mark.claim.mine);
    const otherSpans = plan.spans.filter((mark) => !mark.claim.mine);
    editor.setDecorations(this.ownGutter, decorationOptions(editor.document, ownLines));
    editor.setDecorations(this.otherGutter, decorationOptions(editor.document, otherLines));
    editor.setDecorations(this.ownOverview, decorationRanges(editor.document, ownSpans));
    editor.setDecorations(this.otherOverview, decorationRanges(editor.document, otherSpans));
  }

  dispose(): void {
    this.ownGutter.dispose();
    this.otherGutter.dispose();
    this.ownOverview.dispose();
    this.otherOverview.dispose();
  }
}
