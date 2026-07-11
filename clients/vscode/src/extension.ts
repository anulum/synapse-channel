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

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const config = vscode.workspace.getConfiguration("synapse");
  const identity = resolveIdentity(config.get<string>("identity", ""));

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBar.command = "synapse.showBoard";
  const board = new BoardProvider();
  const gutter = new ClaimGutter(context.extensionUri);
  let renderVisibleGutters = (): void => {};
  const controller = new FleetController(identity, statusBar, board, () => {
    renderVisibleGutters();
  });
  const renderGutter = (editor: vscode.TextEditor): void => {
    void gutter.render(editor, controller.claimSnapshot(), identity);
  };
  renderVisibleGutters = (): void => {
    for (const editor of vscode.window.visibleTextEditors) {
      renderGutter(editor);
    }
  };
  const credentials = new HubCredentialStore(context.secrets);
  const configuredUri = (): string =>
    vscode.workspace.getConfiguration("synapse").get<string>("hubUri", "ws://127.0.0.1:8876");
  const reconnectConfiguredHub = async (): Promise<void> => {
    const uri = configuredUri();
    const verdict = hubConnectionVerdict(uri);
    if (!verdict.allowed) {
      controller.connect(uri);
      void vscode.window.showErrorMessage(verdict.reason);
      return;
    }
    try {
      const token = await credentials.get(verdict.uri);
      const error = controller.connect(verdict.uri, token);
      if (error !== undefined) {
        void vscode.window.showErrorMessage(error);
      }
    } catch (error) {
      controller.connect(verdict.uri);
      const reason = error instanceof Error ? error.message : "SecretStorage access failed.";
      void vscode.window.showErrorMessage(`Could not read the SYNAPSE hub token: ${reason}`);
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
    } catch (error) {
      const reason = error instanceof Error ? error.message : "SecretStorage access failed.";
      void vscode.window.showErrorMessage(`Could not store the SYNAPSE hub token: ${reason}`);
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
    } catch (error) {
      const reason = error instanceof Error ? error.message : "SecretStorage access failed.";
      void vscode.window.showErrorMessage(`Could not clear the SYNAPSE hub token: ${reason}`);
    }
  };

  context.subscriptions.push(
    statusBar,
    gutter,
    vscode.window.registerTreeDataProvider("synapseBoard", board),
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
    vscode.commands.registerCommand("synapse.setHubToken", setHubToken),
    vscode.commands.registerCommand("synapse.clearHubToken", clearHubToken),
    vscode.commands.registerCommand("synapse.showBoard", () =>
      vscode.commands.executeCommand("synapseBoard.focus"),
    ),
    { dispose: () => controller.dispose() },
  );

  await reconnectConfiguredHub();
  renderVisibleGutters();
}

export function deactivate(): void {
  // The extension host disposes registered subscriptions; nothing else to undo.
}
