// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — VS Code fleet projection controller

/** Join validated hub state to VS Code views and commands. */

import * as vscode from "vscode";
import {
  disconnectedConnection,
  type HubConnectionState,
} from "./connectionState.js";
import { hubConnectionVerdict } from "./hubAuth.js";
import {
  boardItems,
  claimMarks,
  hubHealth,
  hubProjectionChanged,
  statusBarText,
  type BoardItem,
  type ClaimMark,
} from "./fleetModel.js";
import { type HubClaim, type HubFrame, type HubTask } from "./hubProtocol.js";
import { HubTransport } from "./hubTransport.js";
import { type MutationSendResult } from "./hubTransportTypes.js";
import { workspaceClaimRequest, type WorkspaceClaimRequest } from "./workspaceScope.js";

interface HubState {
  connection: HubConnectionState;
  agents: string[];
  tasks: HubTask[];
  claims: HubClaim[];
}

interface BoardSink {
  replace(items: BoardItem[]): void;
}

/** Own the editor projection while transport details remain in `HubTransport`. */
export class FleetController {
  private readonly stateValue: HubState = {
    connection: disconnectedConnection(),
    agents: [],
    tasks: [],
    claims: [],
  };
  private readonly transport: HubTransport;
  private identityValue = "";
  private projectionKey: { uri: string; identity: string } | undefined;

  constructor(
    private readonly statusBar: vscode.StatusBarItem,
    private readonly board: BoardSink,
    private readonly onChange: () => void,
  ) {
    this.transport = new HubTransport({
      onConnectionState: (state) => {
        this.stateValue.connection = state;
        this.render();
      },
      onFrame: (frame) => this.ingest(frame),
    });
  }

  /** Connect the current editor identity after URI policy has passed. */
  connect(identity: string, uri: string, token?: string): string | undefined {
    const verdict = hubConnectionVerdict(uri);
    const nextKey = { uri: verdict.allowed ? verdict.uri : uri, identity };
    if (hubProjectionChanged(this.projectionKey, nextKey)) {
      this.stateValue.connection = disconnectedConnection();
      this.stateValue.agents = [];
      this.stateValue.tasks = [];
      this.stateValue.claims = [];
    }
    this.projectionKey = nextKey;
    this.identityValue = identity;
    if (!verdict.allowed) {
      this.transport.dispose();
      this.stateValue.connection = disconnectedConnection();
      this.render();
      return verdict.reason;
    }
    this.transport.connect(verdict.uri, identity, token);
    return undefined;
  }

  /** Identity bound by the most recent complete configuration. */
  identity(): string {
    return this.identityValue;
  }

  private ingest(frame: HubFrame): void {
    if (frame.kind === "welcome") {
      this.stateValue.agents = frame.agents;
      this.transport.request("who_request");
      this.transport.request("board_request");
      this.transport.request("state_request");
    } else if (frame.kind === "roster") {
      this.stateValue.agents = frame.agents;
    } else if (frame.kind === "board") {
      this.stateValue.tasks = frame.tasks;
    } else if (frame.kind === "state") {
      this.stateValue.claims = frame.claims;
    } else if (frame.kind === "state-changed") {
      this.transport.request("state_request");
    }
    this.render();
  }

  /** Render retained last-good data with explicit connection freshness. */
  render(): void {
    const health = hubHealth(this.stateValue.connection, this.stateValue.agents);
    const mine = this.marks().filter((mark) => mark.mine).length;
    this.statusBar.text = statusBarText(health, mine);
    this.statusBar.tooltip = this.stateValue.connection.warning;
    this.statusBar.show();
    this.board.replace(boardItems(this.stateValue.tasks));
    this.onChange();
  }

  /** Per-path claim marks for tree and status projections. */
  marks(): ClaimMark[] {
    return claimMarks(this.stateValue.claims, this.identityValue);
  }

  /** Validated active claims for the gutter renderer. */
  claimSnapshot(): readonly HubClaim[] {
    return this.stateValue.claims;
  }

  /** Claim the active editor's current workspace-relative path. */
  claimActiveFile(): void {
    const request = this.activeFileRequest();
    if (request === undefined) {
      return;
    }
    this.reportMutation(
      this.transport.mutate("claim", {
        task_id: request.taskId,
        worktree: request.worktree,
        paths: request.paths,
      }),
    );
  }

  /** Release the editor-owned claim task for the active workspace. */
  releaseActiveFile(): void {
    const request = this.activeFileRequest();
    if (request === undefined) {
      return;
    }
    this.reportMutation(
      this.transport.mutate("release", {
        task_id: request.taskId,
      }),
    );
  }

  private activeFileRequest(): WorkspaceClaimRequest | undefined {
    const editor = vscode.window.activeTextEditor;
    if (editor === undefined) {
      return undefined;
    }
    const roots = vscode.workspace.workspaceFolders?.map((folder) => folder.uri.fsPath) ?? [];
    const result = workspaceClaimRequest(this.identityValue, editor.document.uri.fsPath, roots);
    if (!result.ok) {
      this.reportMutation({ sent: false, reason: result.reason });
      return undefined;
    }
    return result.request;
  }

  private reportMutation(result: MutationSendResult): void {
    if (!result.sent) {
      void vscode.window.showWarningMessage(result.reason);
    }
  }

  /** Dispose the transport; VS Code owns the remaining registered resources. */
  dispose(): void {
    this.transport.dispose();
  }
}
