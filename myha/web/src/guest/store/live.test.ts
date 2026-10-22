import { afterEach, beforeEach, expect, it, vi } from "vitest";
import fixture from "../../api/dashboard.fixture.json";
import { dashboardResponse, type CommandPayload, type ServerEvent } from "../../api/schema";
import { createLiveStore, panelStatus } from "./live";

const dashboard = dashboardResponse.parse(fixture);
const ids = (store: ReturnType<typeof createLiveStore>) => store.get().rooms.flatMap((r) => r.resources.map((x) => x.id));
const snapshot = (generation: number, rooms = dashboard.rooms): ServerEvent => ({
  type: "snapshot",
  generation,
  sequence: 0,
  policy_version: 8,
  dashboard: { status: "online", rooms },
});
const command = (id: string, status: CommandPayload["status"]): ServerEvent => ({
  type: "command.updated",
  command: { id, resource_id: "res_203_light", action: "light.off", status, created_at: "2026-10-22T10:00:00Z", updated_at: "2026-10-22T10:00:01Z" },
});

let store: ReturnType<typeof createLiveStore>;
beforeEach(() => {
  vi.useFakeTimers({ now: new Date("2026-10-22T10:00:00Z") });
  store = createLiveStore();
  store.load(dashboard);
});
afterEach(() => {
  store.dispose();
  vi.useRealTimers();
});

it("a snapshot replaces everything", () => {
  const light = dashboard.rooms[0]!.resources[0]!;
  store.apply(snapshot(1, [{ id: "unit_203", name: "Room 203", resources: [light] }]));
  expect(ids(store)).toEqual(["res_203_light"]);
  expect(store.get().policyVersion).toBe(8);
});

it("a sequence gap asks for a resync instead of applying", () => {
  store.apply(snapshot(1));
  const light = dashboard.rooms[0]!.resources[0]!;
  expect(store.apply({ type: "resource.updated", sequence: 1, room_id: "unit_203", resource: { ...light, label: "One" } })).toBe(true);
  expect(store.apply({ type: "resource.updated", sequence: 3, room_id: "unit_203", resource: { ...light, label: "Three" } })).toBe(false);
  expect(store.get().rooms[0]!.resources[0]!.label).toBe("One");
  expect(store.apply({ type: "resource.updated", sequence: 1, room_id: "unit_203", resource: light })).toBe(true); // a repeat: dropped, no resync
  expect(store.get().rooms[0]!.resources[0]!.label).toBe("One");
});

it("applies resource deltas in sequence and drops repeats", () => {
  store.apply(snapshot(1));
  const light = dashboard.rooms[0]!.resources[0]!;
  store.apply({ type: "resource.updated", sequence: 1, room_id: "unit_203", resource: { ...light, label: "Main light" } });
  expect(store.get().rooms[0]!.resources[0]!.label).toBe("Main light");
  store.apply({ type: "resource.updated", sequence: 1, room_id: "unit_203", resource: { ...light, label: "Stale copy" } });
  expect(store.get().rooms[0]!.resources[0]!.label).toBe("Main light");
  store.apply({ type: "resource.removed", sequence: 2, resource_id: "res_203_light" });
  expect(ids(store)).not.toContain("res_203_light");
  for (const [n, id] of ids(store).entries()) store.apply({ type: "resource.removed", sequence: 3 + n, resource_id: id });
  expect(store.get().rooms).toEqual([]); // an emptied room goes too
});

it("a lost connection turns stale and drops all states after 30 s", () => {
  store.socketLost();
  expect(panelStatus(store.get())).toBe("stale");
  expect(store.get().staleSince).toBe(Date.now());
  vi.advanceTimersByTime(29_999);
  expect(store.get().rooms[0]!.resources[0]!.state?.power).toBe("on");
  vi.advanceTimersByTime(1);
  const states = store.get().rooms.flatMap((r) => r.resources.map((x) => x.state));
  expect(states.every((s) => s === undefined || (s?.availability === "unknown" && Object.keys(s).length === 1))).toBe(true);
  expect(store.get().cleared).toBe(true);
  expect(panelStatus(store.get())).toBe("stale"); // locked until a fresh snapshot arrives
  store.apply(snapshot(2));
  expect(panelStatus(store.get())).toBe("online");
  expect(store.get().rooms[0]!.resources[0]!.state?.power).toBe("on");
});

it("a short drop keeps the states", () => {
  store.socketLost();
  vi.advanceTimersByTime(5_000);
  store.apply(snapshot(2));
  vi.advanceTimersByTime(60_000);
  expect(store.get().cleared).toBe(false);
  expect(store.get().staleSince).toBeNull();
});

it("an upstream outage reported by the gateway locks the controls", () => {
  store.apply({ type: "connection.status", status: "offline" });
  expect(panelStatus(store.get())).toBe("stale");
  store.apply({ type: "connection.status", status: "online" });
  expect(panelStatus(store.get())).toBe("online");
  store.apply({ type: "connection.status", status: "offline" });
  store.apply(snapshot(2)); // a new snapshot starts from online; the server repeats the state if it is not
  expect(panelStatus(store.get())).toBe("online");
});

it("a confirmed command fades after 4.8 s, a failed one stays", () => {
  store.apply(command("c1", "sent"));
  store.apply(command("c1", "confirmed"));
  expect(store.get().commands.map((c) => c.status)).toEqual(["confirmed"]);
  vi.advanceTimersByTime(4_800);
  expect(store.get().commands).toEqual([]);
  store.apply(command("c2", "failed"));
  vi.advanceTimersByTime(60_000);
  expect(store.get().commands.map((c) => c.id)).toEqual(["c2"]);
});

it("the answer to a click never overwrites a newer event about the same command", () => {
  store.apply(command("c3", "sent"));
  store.addCommand({ id: "c3", resource_id: "res_203_light", action: "light.off", status: "accepted", created_at: "2026-10-22T10:00:00Z", updated_at: "2026-10-22T10:00:00Z" });
  expect(store.get().commands.map((c) => c.status)).toEqual(["sent"]);
});

it("revocation and the session end both end the page", () => {
  store.apply({ type: "session.revoked", reason: "checkout" });
  expect(store.get().ended).toBe(true);
  const other = createLiveStore();
  other.expireAt("2026-10-22T12:00:00Z");
  vi.advanceTimersByTime(2 * 3600_000 - 1);
  expect(other.get().ended).toBe(false);
  vi.advanceTimersByTime(1);
  expect(other.get().ended).toBe(true);
  other.dispose();
});

it("an end beyond the timer range still fires", () => {
  const far = new Date(Date.now() + 40 * 24 * 3600_000).toISOString(); // past setTimeout's 24.8-day limit
  store.expireAt(far);
  vi.advanceTimersByTime(40 * 24 * 3600_000 - 1);
  expect(store.get().ended).toBe(false);
  vi.advanceTimersByTime(1);
  expect(store.get().ended).toBe(true);
});

it("waiting and expired come from the server view", () => {
  store.load({ status: "waiting", rooms: [], policy_version: 1 });
  expect(panelStatus(store.get())).toBe("waiting");
  store.apply({ ...snapshot(3, []), dashboard: { status: "expired", rooms: [] } } as ServerEvent);
  expect(panelStatus(store.get())).toBe("expired");
});
