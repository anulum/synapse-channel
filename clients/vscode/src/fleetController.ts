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
  evidenceItems,
  type EvidenceItem,
} from "./evidenceModel.js";
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
import {
  type HubAgentLiveness,
  type HubDeadLetter,
  type HubMailboxCount,
  type HubProgressNote,
  type HubRelayApproval,
} from "./hubEvidenceProtocol.js";
import { HubTransport } from "./hubTransport.js";
import { type MutationSendResult } from "./hubTransportTypes.js";
import { workspaceClaimRequest, type WorkspaceClaimRequest } from "./workspaceScope.js";

interface HubState {
  connection: HubConnectionState;
  agents: string[];
  tasks: HubTask[];
  claims: HubClaim[];
  progress: HubProgressNote[];
  mailbox: HubMailboxCount[];
  liveness: HubAgentLiveness[];
  deadLetters: HubDeadLetter[];
  relayApprovals: HubRelayApproval[];
  mailboxAvailable: boolean;
  livenessAvailable: boolean;
}

interface BoardSink {
  replace(items: BoardItem[]): void;
}

interface EvidenceSink {
  replace(items: readonly EvidenceItem[]): void;
}

/** Own the editor projection while transport details remain in `HubTransport`. */
export class FleetController {
  private readonly stateValue: HubState = {
    connection: disconnectedConnection(),
    agents: [],
    tasks: [],
    claims: [],
    progress: [],
    mailbox: [],
    liveness: [],
    deadLetters: [],
    relayApprovals: [],
    mailboxAvailable: false,
    livenessAvailable: false,
  };
  private readonly transport: HubTransport;
  private identityValue = "";
  private projectionKey: { uri: string; identity: string } | undefined;

  constructor(
    private readonly statusBar: vscode.StatusBarItem,
    private readonly board: BoardSink,
    private readonly evidence: EvidenceSink,
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
      this.stateValue.progress = [];
      this.stateValue.mailbox = [];
      this.stateValue.liveness = [];
      this.stateValue.deadLetters = [];
      this.stateValue.relayApprovals = [];
      this.stateValue.mailboxAvailable = false;
      this.stateValue.livenessAvailable = false;
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
      if (frame.evidence !== null) {
        this.stateValue.mailbox = frame.evidence.mailbox;
        this.stateValue.liveness = frame.evidence.liveness;
        this.stateValue.mailboxAvailable = frame.evidence.mailboxAvailable;
        this.stateValue.livenessAvailable = frame.evidence.livenessAvailable;
      }
    } else if (frame.kind === "board") {
      this.stateValue.tasks = frame.tasks;
      this.stateValue.progress = frame.progress;
    } else if (frame.kind === "state") {
      this.stateValue.claims = frame.claims;
      this.stateValue.deadLetters = frame.deadLetters;
      this.stateValue.relayApprovals = frame.relayApprovals;
    } else if (frame.kind === "state-changed") {
      this.transport.request("state_request");
    } else if (frame.kind === "board-changed") {
      this.transport.request("board_request");
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
    this.evidence.replace(this.evidenceSnapshot());
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

  /** Read-only evidence currently rendered in the editor tree. */
  evidenceSnapshot(): readonly EvidenceItem[] {
    return evidenceItems({
      connection: this.stateValue.connection,
      progress: this.stateValue.progress,
      mailbox: this.stateValue.mailbox,
      liveness: this.stateValue.liveness,
      deadLetters: this.stateValue.deadLetters,
      relayApprovals: this.stateValue.relayApprovals,
      mailboxAvailable: this.stateValue.mailboxAvailable,
      livenessAvailable: this.stateValue.livenessAvailable,
    });
  }

  /** Request fresh read-only roster, board, and state evidence. */
  refreshEvidence(): void {
    this.transport.request("who_request");
    this.transport.request("board_request");
    this.transport.request("state_request");
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
