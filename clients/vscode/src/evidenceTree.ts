// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — VS Code coordination evidence tree

/** Thin VS Code tree adapter for the editor-agnostic evidence model. */

import * as vscode from "vscode";
import { type EvidenceItem, type EvidenceSeverity } from "./evidenceModel.js";

const ICONS: Record<EvidenceSeverity, { id: string; color?: string }> = {
  critical: { id: "error", color: "errorForeground" },
  warning: { id: "warning", color: "editorWarning.foreground" },
  info: { id: "history" },
  ok: { id: "pass", color: "testing.iconPassed" },
};

/** Actual tree-item fields exposed for host integration verification. */
export interface RenderedEvidenceItem {
  id: string;
  label: string;
  description: string;
  tooltip: string;
  iconId: string;
}

/** Replace-only tree provider; it never mutates hub state. */
export class EvidenceTree implements vscode.TreeDataProvider<EvidenceItem> {
  private items: readonly EvidenceItem[] = [];
  private readonly emitter = new vscode.EventEmitter<undefined>();
  readonly onDidChangeTreeData = this.emitter.event;

  /** Replace the complete authoritative projection. */
  replace(items: readonly EvidenceItem[]): void {
    this.items = [...items];
    this.emitter.fire(undefined);
  }

  getTreeItem(item: EvidenceItem): vscode.TreeItem {
    const node = new vscode.TreeItem(item.label, vscode.TreeItemCollapsibleState.None);
    const icon = ICONS[item.severity];
    node.id = item.id;
    node.description = item.description;
    node.tooltip = item.detail;
    node.iconPath = new vscode.ThemeIcon(
      icon.id,
      icon.color === undefined ? undefined : new vscode.ThemeColor(icon.color),
    );
    return node;
  }

  getChildren(): EvidenceItem[] {
    return [...this.items];
  }

  /** Snapshot fields from the same `TreeItem` objects returned to VS Code. */
  renderedSnapshot(): readonly RenderedEvidenceItem[] {
    return this.items.map((item) => {
      const node = this.getTreeItem(item);
      const label = typeof node.label === "string" ? node.label : node.label?.label ?? "";
      const description = typeof node.description === "string" ? node.description : "";
      const tooltip = typeof node.tooltip === "string" ? node.tooltip : "";
      const iconId = node.iconPath instanceof vscode.ThemeIcon ? node.iconPath.id : "";
      return { id: node.id ?? "", label, description, tooltip, iconId };
    });
  }
}
