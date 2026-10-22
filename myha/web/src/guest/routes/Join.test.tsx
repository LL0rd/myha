import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, type Transport } from "../../transport";
import { createSession, SessionContext, takeJoinFragment, type JoinFragment } from "../store/session";
import { Join } from "./Join";

const SECRET = "s3cr3t-s3cr3t-s3cr3t-s3cr3t-s3cr3t-s3cr3t-x"; // 43 characters, as the server issues them
const config = { instance_name: "Example Inn", languages: ["de", "en"], theme_default: "system", operator_contact: "contact@example.test" };
const session = { session: { not_before: "2026-10-22T00:00:00Z", not_after: "2026-10-24T10:00:00Z", waiting: false }, csrf_token: "csrf-1" };

let csrf: () => string | null;
let request: ReturnType<typeof vi.fn<Transport["request"]>>;
let logged: ReturnType<typeof vi.spyOn>[];

beforeEach(() => {
  logged = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
});
afterEach(() => {
  // The secret never reaches a log, whatever happened in the test.
  for (const spy of logged) expect(JSON.stringify(spy.mock.calls)).not.toContain(SECRET);
  cleanup();
  vi.restoreAllMocks();
});

function renderJoin(fragment: JoinFragment, redeem: () => Promise<unknown>) {
  request = vi.fn<Transport["request"]>(async (path) => (path === "/api/v1/public/config" ? config : redeem()));
  const s = createSession((getCsrf) => {
    csrf = getCsrf;
    return { request, openSocket: vi.fn() };
  });
  render(
    <SessionContext.Provider value={s}>
      <MemoryRouter initialEntries={["/join"]}>
        <Routes>
          <Route path="/join" element={<Join fragment={fragment} />} />
          <Route path="/" element={<p>dashboard</p>} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
  return s;
}

it("takes the invite from the fragment and removes it before anything else", () => {
  history.replaceState(null, "", `/join#i=${SECRET}&inst=inst_1&fp=sha256%3AAB%3ACD`);
  const fragment = takeJoinFragment();
  expect(fragment).toEqual({ secret: SECRET, inst: "inst_1", fp: "sha256:AB:CD" });
  expect(location.href).toBe(`${location.origin}/join`);
});

it("redeems on the button, with the fragment already gone, and opens the dashboard", async () => {
  history.replaceState(null, "", `/join#i=${SECRET}&inst=inst_1&fp=x`);
  const fragment = takeJoinFragment();
  const s = renderJoin(fragment, async () => {
    expect(location.hash).toBe(""); // the request never starts while the secret is in the address bar
    return session;
  });
  expect(document.title).toBe("You're invited");
  expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
  await userEvent.type(screen.getByLabelText("Name on this device (optional)"), "Sam's phone");
  await userEvent.click(screen.getByRole("button", { name: "Open my room" }));
  expect(await screen.findByText("dashboard")).toBeTruthy();
  expect(request).toHaveBeenCalledWith("/api/v1/auth/redeem", {
    method: "POST",
    body: { invite_secret: SECRET, device_label: "Sam's phone", client: "browser" },
  });
  expect(csrf()).toBe("csrf-1");
  expect(s.pairing()).toEqual({ inst: "inst_1", fp: "x" });
  expect(fragment.secret).toBeNull(); // the spent secret does not linger in memory
});

it("a refused invite shows the operator contact", async () => {
  renderJoin({ secret: SECRET, inst: null, fp: null }, async () => {
    throw new ApiError(401, "UNAUTHENTICATED", "Unauthenticated");
  });
  await userEvent.click(screen.getByRole("button", { name: "Open my room" }));
  expect(await screen.findByText("This link could not be used.")).toBeTruthy();
  expect(screen.getByText("Problems? contact@example.test")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Open my room" }) as HTMLButtonElement).disabled).toBe(false);
});

it("an answer that fails its schema is an ordinary, generic failure", async () => {
  renderJoin({ secret: SECRET, inst: null, fp: null }, async () => ({ ...session, entity_id: "light.x" }));
  await userEvent.click(screen.getByRole("button", { name: "Open my room" }));
  expect(await screen.findByText("Something went wrong. Please try again.")).toBeTruthy();
});

it("a refused link with a live session in this browser opens the dashboard", async () => {
  const me = {
    principal: { id: "p1", type: "guest", display_name: "", locale: null, theme: null },
    session: session.session,
    csrf_token: "csrf-2",
    stay: { unit_name: "Room 203", ends_at: "2026-10-24T10:00:00Z" },
    instance: { name: "Example Inn", operator_contact: null },
  };
  renderJoin({ secret: SECRET, inst: null, fp: null }, async () => {
    throw new ApiError(401, "UNAUTHENTICATED", "Unauthenticated");
  });
  request.mockImplementation(async (path) => {
    if (path === "/api/v1/me") return me;
    if (path === "/api/v1/public/config") return config;
    throw new ApiError(401, "UNAUTHENTICATED", "Unauthenticated");
  });
  await userEvent.click(screen.getByRole("button", { name: "Open my room" }));
  expect(await screen.findByText("dashboard")).toBeTruthy();
  expect(csrf()).toBe("csrf-2");
});

it("without a secret the link is incomplete and nothing is sent", async () => {
  renderJoin({ secret: null, inst: null, fp: null }, async () => session);
  expect(screen.getByRole("heading", { name: "Link incomplete" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open my room" })).toBeNull();
  expect(await screen.findByText("Problems? contact@example.test")).toBeTruthy();
  expect(request.mock.calls.map(([path]) => path)).toEqual(["/api/v1/public/config"]);
});
