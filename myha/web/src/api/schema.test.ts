import { afterEach, expect, it, vi } from "vitest";
import type { Room } from "../ui/types";
import fixture from "./dashboard.fixture.json";
import { dashboardResponse, errorBody, events } from "./schema";
import type { components } from "./types";

afterEach(() => vi.restoreAllMocks());

const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

it("accepts a real dashboard projection and fits the UI types", () => {
  const parsed = dashboardResponse.parse(fixture);
  const rooms: Room[] = parsed.rooms; // compile-time check: the panel renders what the API sends
  const generated: components["schemas"]["DashboardResponse"] = parsed; // and zod agrees with the gateway's OpenAPI
  expect(generated.policy_version).toBe(7);
  expect(rooms[0]?.resources).toHaveLength(7);
});

it("accepts the empty waiting and expired views", () => {
  expect(dashboardResponse.parse({ status: "waiting", rooms: [], policy_version: 1 }).rooms).toEqual([]);
  expect(dashboardResponse.parse({ status: "expired", rooms: [], policy_version: 1 }).status).toBe("expired");
});

it.each([
  ["entity_id on a resource", (d: typeof fixture) => Object.assign(d.rooms[0]!.resources[0]!, { entity_id: "light.room_203" })],
  ["an HA attribute in the state", (d: typeof fixture) => Object.assign(d.rooms[0]!.resources[0]!.state!, { brightness: 153 })],
  ["an unknown top-level field", (d: typeof fixture) => Object.assign(d, { debug: true })],
  ["an unknown field on a room", (d: typeof fixture) => Object.assign(d.rooms[0]!, { area_id: "bedroom" })],
  ["an unknown capability key", (d: typeof fixture) => Object.assign(d.rooms[0]!.resources[0]!.capabilities!, { "light.flash": {} })],
  ["a number where a decimal string belongs", (d: typeof fixture) => Object.assign(d.rooms[0]!.resources[2]!.state!, { target_c: 21.5 })],
  ["an unknown kind", (d: typeof fixture) => Object.assign(d.rooms[0]!.resources[0]!, { kind: "camera" })],
  ["the stale status, which only the client knows", (d: typeof fixture) => Object.assign(d, { status: "stale" })],
])("rejects %s", (_, mutate) => {
  const bad = clone(fixture);
  mutate(bad);
  expect(dashboardResponse.safeParse(bad).success).toBe(false);
});

const command = {
  id: "cmd_1",
  resource_id: "res_203_light",
  action: "light.off",
  status: "sent",
  created_at: "2026-10-22T10:00:00Z",
  updated_at: "2026-10-22T10:00:00.5Z",
};
const resource = fixture.rooms[0]!.resources[0]!;

it.each([
  ["snapshot", { type: "snapshot", generation: 3, sequence: 0, policy_version: 7, dashboard: { status: "online", rooms: fixture.rooms } }],
  ["resource.updated", { type: "resource.updated", sequence: 4, room_id: "unit_203", resource }],
  ["resource.removed", { type: "resource.removed", sequence: 5, resource_id: "res_203_light" }],
  ["policy.changed", { type: "policy.changed", policy_version: 8 }],
  ["session.revoked", { type: "session.revoked", reason: "checkout" }],
  ["connection.status", { type: "connection.status", status: "stale" }],
  ["command.updated", { type: "command.updated", command }],
  ["command.updated with a public code", { type: "command.updated", command: { ...command, status: "failed", public_code: "UPSTREAM_UNAVAILABLE" } }],
])("accepts the %s event", (_, event) => {
  expect(events.any.parse(event)).toEqual(event);
});

it.each([
  ["an unknown type", { type: "resource.created", sequence: 1 }],
  ["entity_id in an updated resource", { type: "resource.updated", sequence: 4, room_id: "unit_203", resource: { ...resource, entity_id: "light.x" } }],
  ["an extra field", { type: "policy.changed", policy_version: 8, entity_id: "light.x" }],
  ["a snapshot without generation", { type: "snapshot", sequence: 0, policy_version: 7, dashboard: { status: "online", rooms: [] } }],
  ["an unknown command state", { type: "command.updated", command: { ...command, status: "done" } }],
  ["a private decision code", { type: "command.updated", command: { ...command, public_code: "FENCE_VIOLATION" } }],
  ["an unknown connection status", { type: "connection.status", status: "online-ish" }],
])("rejects %s", (_, event) => {
  expect(events.any.safeParse(event).success).toBe(false);
});

it("knows exactly the eight public error codes", () => {
  const codes = ["UNAUTHENTICATED", "NOT_FOUND", "ACTION_NOT_ALLOWED", "INVALID_PARAMETERS", "ACCESS_EXPIRED", "UPSTREAM_UNAVAILABLE", "RATE_LIMITED", "CONFLICT"];
  for (const code of codes) expect(errorBody.safeParse({ error: { code, message: "m" } }).success).toBe(true);
  expect(errorBody.safeParse({ error: { code: "NO_GRANT", message: "m" } }).success).toBe(false);
});
