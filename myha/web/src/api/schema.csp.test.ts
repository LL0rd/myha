import { expect, it, vi } from "vitest";
import fixture from "./dashboard.fixture.json";

// Own file: zod caches its eval probe and compiled parsers per process, so the spy must exist before zod loads.
it("parses without new Function, which the CSP forbids", async () => {
  const spy = vi.spyOn(globalThis, "Function");
  const { dashboardResponse, events } = await import("./schema");
  dashboardResponse.parse(fixture);
  events.any.parse({ type: "policy.changed", policy_version: 1 });
  expect(spy).not.toHaveBeenCalled();
});
