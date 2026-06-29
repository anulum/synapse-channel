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
 * bar, the board tree, gutter decorations, and the claim/release commands — and
 * delegates every decision to the editor-agnostic {@link ./fleetModel.js}, which
 * is unit-tested without an editor host. The hub connection is a plain WebSocket
 * to the local hub; the extension registers an identity, tracks presence, board,
 * and active claims from inbound frames, and renders them.
 */

import * as vscode from "vscode";
import {
  boardItems,
  claimMarks,
  claimRequest,
  hubHealth,
  statusBarText,
  type BoardItem,
  type ClaimMark,
  type RawClaim,
  type RawTask,
} from "./fleetModel.js";

interface Envelope {
  type?: string;
  sender?: string;
  online_agents?: string[];
  tasks?: RawTask[];
  active_claims?: RawClaim[];
}

interface HubState {
  connected: boolean;
  agents: string[];
  tasks: RawTask[];
  claims: RawClaim[];
}

const CLAIM_TASK_PREFIX = "vscode";

class FleetController {
  private readonly state: HubState = { connected: false, agents: [], tasks: [], claims: [] };
  private socket: WebSocket | undefined;

  constructor(
    private readonly identity: string,
    private readonly statusBar: vscode.StatusBarItem,
    private readonly decoration: vscode.TextEditorDecorationType,
    private readonly board: BoardProvider,
    private readonly onChange: () => void,
  ) {}

  connect(uri: string): void {
    const socket = new WebSocket(uri);
    this.socket = socket;
    socket.addEventListener("open", () => {
      this.state.connected = true;
      socket.send(JSON.stringify({ type: "register", sender: this.identity }));
      this.render();
    });
    socket.addEventListener("close", () => {
      this.state.connected = false;
      this.render();
    });
    socket.addEventListener("message", (event: MessageEvent) => this.ingest(String(event.data)));
  }

  private ingest(raw: string): void {
    let frame: Envelope;
    try {
      frame = JSON.parse(raw) as Envelope;
    } catch {
      return;
    }
    if (frame.online_agents) {
      this.state.agents = frame.online_agents;
    }
    if (frame.tasks) {
      this.state.tasks = frame.tasks;
    }
    if (frame.active_claims) {
      this.state.claims = frame.active_claims;
    }
    this.render();
  }

  render(): void {
    const health = hubHealth(this.state.connected, this.state.agents);
    const marks = this.marks();
    const mine = marks.filter((mark) => mark.mine).length;
    this.statusBar.text = statusBarText(health, mine);
    this.statusBar.show();
    this.board.replace(boardItems(this.state.tasks));
    this.onChange();
  }

  marks(): ClaimMark[] {
    return claimMarks(this.state.claims, this.identity);
  }

  decorate(editor: vscode.TextEditor): void {
    const path = vscode.workspace.asRelativePath(editor.document.uri, false);
    const claimed = this.marks().some((mark) => mark.path === path.replace(/\\/g, "/"));
    const ranges = claimed ? [new vscode.Range(0, 0, 0, 0)] : [];
    editor.setDecorations(this.decoration, ranges);
  }

  claimActiveFile(): void {
    const editor = vscode.window.activeTextEditor;
    if (!editor || !this.socket) {
      return;
    }
    const path = vscode.workspace.asRelativePath(editor.document.uri, false);
    const request = claimRequest(`${CLAIM_TASK_PREFIX}/${this.identity}`, path);
    this.socket.send(
      JSON.stringify({ type: "claim", sender: this.identity, task_id: request.taskId, paths: request.paths }),
    );
  }

  releaseActiveFile(): void {
    const editor = vscode.window.activeTextEditor;
    if (!editor || !this.socket) {
      return;
    }
    this.socket.send(
      JSON.stringify({ type: "release", sender: this.identity, task_id: `${CLAIM_TASK_PREFIX}/${this.identity}` }),
    );
  }

  dispose(): void {
    this.socket?.close();
  }
}

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

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration("synapse");
  const uri = config.get<string>("hubUri", "ws://127.0.0.1:8876");
  const identity = resolveIdentity(config.get<string>("identity", ""));

  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusBar.command = "synapse.showBoard";
  const decoration = vscode.window.createTextEditorDecorationType({
    overviewRulerColor: new vscode.ThemeColor("editorWarning.foreground"),
    overviewRulerLane: vscode.OverviewRulerLane.Left,
    isWholeLine: true,
  });
  const board = new BoardProvider();

  const redecorate = (): void => {
    const editor = vscode.window.activeTextEditor;
    if (editor) {
      controller.decorate(editor);
    }
  };
  const controller = new FleetController(identity, statusBar, decoration, board, redecorate);

  context.subscriptions.push(
    statusBar,
    decoration,
    vscode.window.registerTreeDataProvider("synapseBoard", board),
    vscode.window.onDidChangeActiveTextEditor(redecorate),
    vscode.commands.registerCommand("synapse.claimFile", () => controller.claimActiveFile()),
    vscode.commands.registerCommand("synapse.releaseFile", () => controller.releaseActiveFile()),
    vscode.commands.registerCommand("synapse.refreshHealth", () => controller.render()),
    vscode.commands.registerCommand("synapse.showBoard", () =>
      vscode.commands.executeCommand("synapseBoard.focus"),
    ),
    { dispose: () => controller.dispose() },
  );

  controller.connect(uri);
}

export function deactivate(): void {
  // The extension host disposes registered subscriptions; nothing else to undo.
}
