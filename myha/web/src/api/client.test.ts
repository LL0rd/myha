import { expect, it, vi } from "vitest";
import type { Transport } from "../transport";
import { apiClient } from "./client";
import fixture from "./dashboard.fixture.json";

const transportReturning = (body: unknown) => {
  const request = vi.fn<Transport["request"]>(async () => body);
  return { request, client: apiClient({ request, openSocket: vi.fn() }) };
};

it("validates the dashboard before handing it out", async () => {
  expect((await transportReturning(fixture).client.dashboard()).policy_version).toBe(7);
  const leaky = { ...fixture, rooms: [{ ...fixture.rooms[0], entity_id: "light.x" }] };
  await expect(transportReturning(leaky).client.dashboard()).rejects.toThrow();
});

it("sends an action with its idempotency key and validates the answer", async () => {
  const { request, client } = transportReturning({ command_id: "cmd_1", status: "accepted", policy_version: 7 });
  const answer = await client.actions("res_203_trv", "climate.set_temperature", { target_c: "22.0" }, "key-1");
  expect(answer.command_id).toBe("cmd_1");
  expect(request).toHaveBeenCalledWith("/api/v1/actions", {
    method: "POST",
    body: { resource_id: "res_203_trv", action: "climate.set_temperature", params: { target_c: "22.0" } },
    idempotencyKey: "key-1",
  });
});

it("redeems as a browser and escapes command ids", async () => {
  const session = { session: { not_before: "2026-10-22T00:00:00Z", not_after: "2026-10-24T10:00:00Z", waiting: false }, csrf_token: "c" };
  const { request, client } = transportReturning(session);
  await client.redeem("secret", "Sam's phone");
  expect(request).toHaveBeenCalledWith("/api/v1/auth/redeem", {
    method: "POST",
    body: { invite_secret: "secret", device_label: "Sam's phone", client: "browser" },
  });
  const cmd = transportReturning({ id: "a/b", resource_id: "r", action: "light.on", status: "sent", created_at: "2026-10-22T10:00:00Z", updated_at: "2026-10-22T10:00:00Z" });
  await cmd.client.command("a/b");
  expect(cmd.request).toHaveBeenCalledWith("/api/v1/commands/a%2Fb", undefined);
});

it("accepts /me as the guest auth routes send it", async () => {
  const me = {
    principal: { id: "prn_1", type: "guest", display_name: null, locale: null, theme: null },
    session: { not_before: "2026-10-22T00:00:00+00:00", not_after: "2026-10-24T10:00:00+00:00", waiting: false },
    csrf_token: "c",
    stay: { unit_name: null, ends_at: "2026-10-24T10:00:00+00:00" },
    instance: { name: "Seeblick", operator_contact: null },
  };
  expect((await transportReturning(me).client.me()).stay?.unit_name).toBeNull();
  await expect(transportReturning({ ...me, principal: { ...me.principal, password: "x" } }).client.me()).rejects.toThrow();
});
