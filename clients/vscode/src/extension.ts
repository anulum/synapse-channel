// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — VS Code / Cursor extension glue

/**
 * VS Code / Cursor extension entry point.
 *
 * This is the thin editor-host glue: it owns the VS Code API surface — the status
 * bar, the board tree, and the claim/release commands — and delegates gutter
 * rendering to {@link ./claimGutter.js}, socket/state lifecycle to
 * {@link ./fleetController.js}, credential policy to {@link ./hubAuth.js}, and
 * display decisions to editor-agnostic model modules.
 */

import * as vscode from "vscode";
import { ClaimGutter } from "./claimGutter.js";
import { ConfigurationReconnectGate } from "./configurationReconnect.js";
import { type EvidenceItem } from "./evidenceModel.js";
import { EvidenceTree, type RenderedEvidenceItem } from "./evidenceTree.js";
import { FleetController } from "./fleetController.js";
import { HubCredentialStore, hubConnectionVerdict } from "./hubAuth.js";
import { type BoardItem } from "./fleetModel.js";

class BoardProvider implements vscode.TreeDataProvider<BoardItem> {
  private items: BoardItem[] = [];
  private readonly emitter = new vscode.EventEmitter<undefined>();
  readonly onDidChangeTreeData = this.emitter.event;

  replace(items: BoardItem[]): void {
    this.items = items;
    this.emitter.fire(undefined);
  }

  getTreeItem(item: BoardItem): vscode.TreeItem {
    const node = new vscode.TreeItem(item.label, vscode.TreeItemCollapsibleState.None);
    node.description = item.status;
    return node;
  }

  getChildren(): BoardItem[] {
    return this.items;
  }
}

function resolveIdentity(configured: string): string {
  if (configured.trim()) {
    return configured.trim();
  }
  const folder = vscode.workspace.workspaceFolders?.[0]?.name ?? "workspace";
  return `${folder}/vscode`;
}

/** Read-only API exposed to editor hosts and integration consumers. */
export interface SynapseExtensionApi {
  readonly apiVersion: 1;
  evidenceSnapshot(): readonly EvidenceItem[];
  renderedEvidenceSnapshot(): readonly RenderedEvidenceItem[];
}

export async function activate(context: vscode.ExtensionContext): Promise<SynapseExtensionApi> {
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBar.command = "synapse.showBoard";
  const board = new BoardProvider();
  const evidence = new EvidenceTree();
  const gutter = new ClaimGutter(context.extensionUri);
  let renderVisibleGutters = (): void => {};
  const controller = new FleetController(statusBar, board, evidence, () => {
    renderVisibleGutters();
  });
  const renderGutter = (editor: vscode.TextEditor): void => {
    void gutter.render(editor, controller.claimSnapshot(), controller.identity());
  };
  renderVisibleGutters = (): void => {
    for (const editor of vscode.window.visibleTextEditors) {
      renderGutter(editor);
    }
  };
  const credentials = new HubCredentialStore(context.secrets);
  const reconnects = new ConfigurationReconnectGate();
  const configuredUri = (): string =>
    vscode.workspace.getConfiguration("synapse").get<string>("hubUri", "ws://127.0.0.1:8876");
  const configuredIdentity = (): string =>
    resolveIdentity(vscode.workspace.getConfiguration("synapse").get<string>("identity", ""));
  const reconnectConfiguredHub = async (): Promise<void> => {
    const generation = reconnects.begin();
    const uri = configuredUri();
    const identity = configuredIdentity();
    const verdict = hubConnectionVerdict(uri);
    if (!verdict.allowed) {
      controller.connect(identity, uri);
      void vscode.window.showErrorMessage(verdict.reason);
      return;
    }
    const tokenRead = await reconnects.read(generation, () => credentials.get(verdict.uri));
    if (tokenRead.kind === "stale") {
      return;
    }
    if (tokenRead.kind === "error") {
      controller.connect(identity, verdict.uri);
      void vscode.window.showErrorMessage("Could not read the SYNAPSE hub token from SecretStorage.");
      return;
    }
    const error = controller.connect(identity, verdict.uri, tokenRead.value);
    if (error !== undefined) {
      void vscode.window.showErrorMessage(error);
    }
  };

  const setHubToken = async (provided?: unknown): Promise<void> => {
    const verdict = hubConnectionVerdict(configuredUri());
    if (!verdict.allowed) {
      void vscode.window.showErrorMessage(verdict.reason);
      return;
    }
    const token = typeof provided === "string"
      ? provided
      : await vscode.window.showInputBox({
          password: true,
          ignoreFocusOut: true,
          prompt: `Shared token for ${new URL(verdict.uri).host}; stored only in VS Code SecretStorage`,
        });
    if (token === undefined) {
      return;
    }
    try {
      await credentials.store(verdict.uri, token);
      await reconnectConfiguredHub();
      void vscode.window.showInformationMessage(
        `SYNAPSE hub token stored securely for ${new URL(verdict.uri).host}.`,
      );
    } catch {
      void vscode.window.showErrorMessage("Could not store the SYNAPSE hub token in SecretStorage.");
    }
  };

  const clearHubToken = async (): Promise<void> => {
    const verdict = hubConnectionVerdict(configuredUri());
    if (!verdict.allowed) {
      void vscode.window.showErrorMessage(verdict.reason);
      return;
    }
    try {
      await credentials.clear(verdict.uri);
      await reconnectConfiguredHub();
      void vscode.window.showInformationMessage(
        `SYNAPSE hub token cleared for ${new URL(verdict.uri).host}.`,
      );
    } catch {
      void vscode.window.showErrorMessage("Could not clear the SYNAPSE hub token from SecretStorage.");
    }
  };

  context.subscriptions.push(
    statusBar,
    gutter,
    vscode.window.registerTreeDataProvider("synapseBoard", board),
    vscode.window.registerTreeDataProvider("synapseEvidence", evidence),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("synapse.hubUri")
          || event.affectsConfiguration("synapse.identity")) {
        void reconnectConfiguredHub();
      }
    }),
    vscode.window.onDidChangeVisibleTextEditors(renderVisibleGutters),
    vscode.window.onDidChangeTextEditorVisibleRanges((event) => {
      renderGutter(event.textEditor);
    }),
    vscode.workspace.onDidSaveTextDocument((document) => {
      for (const editor of vscode.window.visibleTextEditors) {
        if (editor.document === document) {
          renderGutter(editor);
        }
      }
    }),
    vscode.commands.registerCommand("synapse.claimFile", () => controller.claimActiveFile()),
    vscode.commands.registerCommand("synapse.releaseFile", () => controller.releaseActiveFile()),
    vscode.commands.registerCommand("synapse.refreshHealth", () => controller.render()),
    vscode.commands.registerCommand("synapse.refreshEvidence", () => controller.refreshEvidence()),
    vscode.commands.registerCommand("synapse.setHubToken", setHubToken),
    vscode.commands.registerCommand("synapse.clearHubToken", clearHubToken),
    vscode.commands.registerCommand("synapse.showBoard", () =>
      vscode.commands.executeCommand("synapseBoard.focus"),
    ),
    vscode.commands.registerCommand("synapse.showEvidence", () =>
      vscode.commands.executeCommand("synapseEvidence.focus"),
    ),
    { dispose: () => controller.dispose() },
  );

  await reconnectConfiguredHub();
  renderVisibleGutters();
  return Object.freeze({
    apiVersion: 1 as const,
    evidenceSnapshot: () => controller.evidenceSnapshot(),
    renderedEvidenceSnapshot: () => evidence.renderedSnapshot(),
  });
}

export function deactivate(): void {
  // The extension host disposes registered subscriptions; nothing else to undo.
}
