// What the panel renders: the dashboard projection plus the explain rules of the admin preview.
import type { actions, kinds, PublicErrorCode } from "../api/schema";

export type Kind = (typeof kinds)[number];

/** The write actions; the read actions never come with a control. */
export type Action = Exclude<(typeof actions)[number], `${string}.read`>;

/** Decimal strings as the API sends them, e.g. "18.0"; allowed values lie on origin + k * step. */
export interface Interval {
  min: string;
  max: string;
  step: string;
  origin: string;
}

export interface Capability {
  intervals?: Interval[] | null;
  allowed?: string[] | null;
}

export interface ResourceState {
  availability: "online" | "offline" | "unknown";
  observed_at?: string | null;
  power?: string | null;
  level_pct?: number | null;
  current_c?: string | null;
  target_c?: string | null;
  hvac_mode?: string | null;
  position_pct?: number | null;
  cover_state?: string | null;
  value?: string | null;
  unit?: string | null;
}

/** One line of the explain view: why an action is allowed, denied or fenced. */
export interface Rule {
  action: string;
  status: "allow" | "deny" | "fence" | "missing";
  text: string;
}

export interface Resource {
  id: string;
  kind: Kind;
  label: string;
  icon?: string | null;
  /** Absent without state.read. */
  state?: ResourceState | null;
  /** Only the write actions the viewer may use. */
  capabilities?: Partial<Record<Action, Capability>> | null;
  rules?: Rule[];
}

export interface Room {
  id: string;
  name: string;
  resources: Resource[];
}

export type CommandState = "accepted" | "sent" | "confirmed" | "failed" | "unknown";

export interface Command {
  id: string;
  resource_id: string;
  action: string;
  status: CommandState;
  /** Why a command failed, in the eight public codes. */
  public_code?: PublicErrorCode | null;
  created_at: string;
  updated_at: string;
}

export type DashboardStatus = "online" | "waiting" | "expired" | "stale";

export type Layout = "tiles" | "list" | "focus";

export type ActionParams = Record<string, string | number>;
