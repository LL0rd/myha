// Dashboard projection as served by GET /api/v1/dashboard; Task 8.1 replaces these with the generated API types.

export type Kind = "light" | "switch" | "climate" | "cover" | "sensor";

export type Action =
  | "switch.on"
  | "switch.off"
  | "light.on"
  | "light.off"
  | "light.set_level"
  | "climate.set_temperature"
  | "climate.set_mode"
  | "cover.open"
  | "cover.stop"
  | "cover.close"
  | "cover.set_position";

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
  action: Action;
  status: CommandState;
  created_at: string;
  updated_at: string;
}

export type DashboardStatus = "online" | "waiting" | "expired" | "stale";

export type Layout = "tiles" | "list" | "focus";

export type ActionParams = Record<string, string | number>;
