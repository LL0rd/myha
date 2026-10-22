// Runtime contract of the public API, mirroring myha_gateway/projection/schema.py (extra="forbid"):
// every object is strict, so a field the server must never send (entity_id, HA attributes) fails.
import { z } from "zod";

// The CSP has no 'unsafe-eval': without this, zod probes `new Function` and the browser reports a violation.
z.config({ jitless: true });

const datetime = z.iso.datetime({ offset: true });
const decimal = z.string().regex(/^-?\d+(\.\d+)?$/);
const count = z.number().int().nonnegative();

export const kinds = ["light", "switch", "climate", "cover", "sensor"] as const;
export const actions = [
  "metadata.read",
  "state.read",
  "history.read",
  "switch.on",
  "switch.off",
  "light.on",
  "light.off",
  "light.set_level",
  "climate.set_temperature",
  "climate.set_mode",
  "cover.open",
  "cover.close",
  "cover.stop",
  "cover.set_position",
] as const;
// myha_gateway/policy/codes.py PublicError: the only codes a public client ever sees.
export const publicErrorCodes = [
  "UNAUTHENTICATED",
  "NOT_FOUND",
  "ACTION_NOT_ALLOWED",
  "INVALID_PARAMETERS",
  "ACCESS_EXPIRED",
  "UPSTREAM_UNAVAILABLE",
  "RATE_LIMITED",
  "CONFLICT",
] as const;
export type PublicErrorCode = (typeof publicErrorCodes)[number];
export const commandStates = ["accepted", "sent", "confirmed", "failed", "unknown"] as const;

export const resourceState = z.strictObject({
  availability: z.enum(["online", "offline", "unknown"]),
  observed_at: datetime.nullable().optional(),
  power: z.string().nullable().optional(),
  level_pct: z.number().int().nullable().optional(),
  current_c: decimal.nullable().optional(),
  target_c: decimal.nullable().optional(),
  hvac_mode: z.string().nullable().optional(),
  position_pct: z.number().int().nullable().optional(),
  cover_state: z.string().nullable().optional(),
  value: z.string().nullable().optional(),
  unit: z.string().nullable().optional(),
});

export const interval = z.strictObject({ min: decimal, max: decimal, step: decimal, origin: decimal });

export const capability = z.strictObject({
  intervals: z.array(interval).nullable().optional(),
  allowed: z.array(z.string()).nullable().optional(),
});

export const resource = z.strictObject({
  id: z.string(),
  kind: z.enum(kinds),
  label: z.string(),
  icon: z.string().nullable().optional(),
  state: resourceState.nullable().optional(),
  capabilities: z.partialRecord(z.enum(actions), capability).nullable().optional(),
});

export const room = z.strictObject({ id: z.string(), name: z.string(), resources: z.array(resource) });

export const dashboardView = z.strictObject({
  status: z.enum(["online", "waiting", "expired"]),
  rooms: z.array(room),
});

/** GET /api/v1/dashboard */
export const dashboardResponse = dashboardView.extend({ policy_version: count });

export const errorBody = z.strictObject({
  error: z.strictObject({ code: z.enum(publicErrorCodes), message: z.string(), details: z.unknown().optional() }),
});

export const command = z.strictObject({
  id: z.string(),
  resource_id: z.string(),
  action: z.enum(actions),
  status: z.enum(commandStates),
  created_at: datetime,
  updated_at: datetime,
  public_code: z.enum(publicErrorCodes).nullable().optional(),
});

// Responses of the guest routes of Task 5a.3 and 6.4, from the plan text; align when those routes land.
const sessionInfo = z.strictObject({ not_before: datetime, not_after: datetime, waiting: z.boolean() });

/** GET /api/v1/public/config */
export const configResponse = z.strictObject({
  instance_name: z.string(),
  languages: z.array(z.string()),
  theme_default: z.string(),
  operator_contact: z.string().nullable(),
});
/** POST /api/v1/auth/redeem (browser) and POST /api/v1/auth/session/renew */
export const sessionResponse = z.strictObject({ session: sessionInfo, csrf_token: z.string() });
/** GET /api/v1/me */
export const meResponse = z.strictObject({
  principal: z.strictObject({ id: z.string(), type: z.string(), display_name: z.string().nullable() }),
  session: sessionInfo,
  csrf_token: z.string(),
  stay: z.strictObject({ unit_name: z.string(), ends_at: datetime }).nullable(),
  instance: z.strictObject({ name: z.string(), operator_contact: z.string().nullable() }),
});
/** POST /api/v1/ws-ticket */
export const wsTicketResponse = z.strictObject({ ticket: z.string(), expires_in: count });
/** POST /api/v1/actions (202) */
export const actionResponse = z.strictObject({
  command_id: z.string(),
  status: z.enum(commandStates),
  policy_version: count,
});

// WebSocket events of /api/v1/events: the contract for Task 6.3.
const snapshot = z.strictObject({
  type: z.literal("snapshot"),
  generation: count,
  sequence: count,
  policy_version: count,
  dashboard: dashboardView,
});
const resourceUpdated = z.strictObject({
  type: z.literal("resource.updated"),
  sequence: count,
  room_id: z.string(),
  resource,
});
const resourceRemoved = z.strictObject({ type: z.literal("resource.removed"), sequence: count, resource_id: z.string() });
const policyChanged = z.strictObject({ type: z.literal("policy.changed"), policy_version: count });
const sessionRevoked = z.strictObject({ type: z.literal("session.revoked"), reason: z.string() });
const connectionStatus = z.strictObject({
  type: z.literal("connection.status"),
  status: z.enum(["online", "stale", "offline"]),
});
const commandUpdated = z.strictObject({ type: z.literal("command.updated"), command });

export const events = {
  snapshot,
  resourceUpdated,
  resourceRemoved,
  policyChanged,
  sessionRevoked,
  connectionStatus,
  commandUpdated,
  any: z.discriminatedUnion("type", [
    snapshot,
    resourceUpdated,
    resourceRemoved,
    policyChanged,
    sessionRevoked,
    connectionStatus,
    commandUpdated,
  ]),
};

export type DashboardResponse = z.infer<typeof dashboardResponse>;
export type ServerEvent = z.infer<typeof events.any>;
export type CommandPayload = z.infer<typeof command>;
