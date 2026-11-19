export type Surface = "chat" | "studio" | "graphics" | "control";

export type FeedStatus = "connecting" | "live" | "turn";

export interface Proposal {
  proposal_id: string;
  path: string;
  tool: string;
  status?: string;
  diff?: string;
  bytes?: number;
  message?: string;
  created?: string;
}

export interface SurfaceEvent {
  surface: Surface;
  reason: string;
}

export interface PendingAction {
  tool?: string;
  arguments?: Record<string, unknown>;
}

export interface DoneResult {
  ok: boolean;
  reply?: string;
  error?: string;
  sid?: string;
  proposal?: Proposal;
  surface?: SurfaceEvent;
  needs_confirm?: boolean;
  pending_action?: PendingAction;
  suggestions?: string[];
}

export interface ActivityEvent {
  event: string;
  tool?: string;
  arguments?: Record<string, string>;
  result?: string;
  provider?: string;
  model?: string;
  request_id?: string;
  timestamp?: string;
  proposal_id?: string;
  path?: string;
  diff?: string;
  surface?: Surface;
  reason?: string;
}

export interface WorkspaceEntry {
  id: string;
  kind: "file" | "terminal" | "proposal" | "surface" | "brain" | "system";
  label: string;
  detail?: string;
  status: "running" | "done" | "error" | "pending";
  tool?: string;
  proposalId?: string;
  requestId?: string;
  ts: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  ts: number;
  pending?: boolean;
  error?: boolean;
}
