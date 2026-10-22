import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import fixture from "../../api/dashboard.fixture.json";
import { ApiError, type Transport } from "../../transport";
import { createSession, SessionContext } from "../store/session";
import { Dashboard } from "./Dashboard";

const me = {
  principal: { id: "p1", type: "guest", display_name: "Sam's phone", locale: null, theme: null },
  session: { not_before: "2026-10-22T00:00:00Z", not_after: "2026-10-24T10:00:00Z", waiting: false },
  csrf_token: "csrf-1",
  stay: { unit_name: "Room 203", ends_at: "2026-10-24T10:00:00Z" },
  instance: { name: "Example Inn", operator_contact: "contact@example.test" },
};

class FakeSocket extends EventTarget {
  close = vi.fn();
  send(data: unknown) {
    void data;
  }
  receive(event: unknown) {
    this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(event) }));
  }
}

let sockets: FakeSocket[];
let request: ReturnType<typeof vi.fn<Transport["request"]>>;
let answers: Record<string, () => unknown>;

beforeEach(() => {
  vi.useFakeTimers({ now: new Date("2026-10-22T10:00:00Z"), shouldAdvanceTime: true });
  sockets = [];
  let tickets = 0;
  answers = {
    "/api/v1/me": () => me,
    "/api/v1/dashboard": () => fixture,
    "/api/v1/ws-ticket": () => ({ ticket: `t${++tickets}`, expires_in: 30 }),
    "/api/v1/actions": () => ({ command_id: "cmd_9", status: "accepted", policy_version: 7 }),
  };
  request = vi.fn<Transport["request"]>(async (path) => answers[path]!());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function renderDashboard() {
  const openSocket = vi.fn((path: string, ticket: string) => {
    void path;
    void ticket;
    const socket = new FakeSocket();
    sockets.push(socket);
    return socket as unknown as WebSocket;
  });
  const session = createSession(() => ({ request, openSocket }));
  render(
    <SessionContext.Provider value={session}>
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/ended" element={<p>ended page</p>} />
        </Routes>
      </MemoryRouter>
    </SessionContext.Provider>,
  );
  return openSocket;
}

it("loads /me and the dashboard, then listens with a ticket", async () => {
  const openSocket = renderDashboard();
  expect(await screen.findByRole("heading", { level: 1, name: "Room 203" })).toBeTruthy();
  expect(screen.getByText("Connected")).toBeTruthy();
  await vi.waitFor(() => expect(openSocket).toHaveBeenCalledWith("/api/v1/events", "t1"));
});

it("a click sends the action with a fresh idempotency key and shows its progress", async () => {
  renderDashboard();
  const light = within(await screen.findByRole("group", { name: "Ceiling" }));
  fireEvent.click(light.getByRole("button", { name: "Turn off" }));
  fireEvent.click(light.getByRole("button", { name: "Turn off" }));
  await vi.waitFor(() => expect(light.getByText("Checking and sending…")).toBeTruthy());
  const keys = request.mock.calls.filter(([path]) => path === "/api/v1/actions").map(([, options]) => options?.idempotencyKey);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(keys[0]).not.toBe(keys[1]);
  sockets[0]?.receive({
    type: "command.updated",
    command: { id: "cmd_9", resource_id: "res_203_light", action: "light.off", status: "confirmed", created_at: "2026-10-22T10:00:00Z", updated_at: "2026-10-22T10:00:01Z" },
  });
  expect(await light.findByText("Confirmed")).toBeTruthy();
});

it("a timed-out click is sent again with the same key", async () => {
  let calls = 0;
  answers["/api/v1/actions"] = () => {
    if (++calls === 1) throw new DOMException("timed out", "TimeoutError");
    return { command_id: "cmd_9", status: "accepted", policy_version: 7 };
  };
  renderDashboard();
  const light = within(await screen.findByRole("group", { name: "Ceiling" }));
  fireEvent.click(light.getByRole("button", { name: "Turn off" }));
  await vi.waitFor(() => expect(light.getByText("Checking and sending…")).toBeTruthy());
  const keys = request.mock.calls.filter(([path]) => path === "/api/v1/actions").map(([, options]) => options?.idempotencyKey);
  expect(keys).toHaveLength(2);
  expect(keys[1]).toBe(keys[0]);
});

it("a click whose outcome stays unknown says so, a refusal says failed", async () => {
  answers["/api/v1/actions"] = () => {
    throw new DOMException("timed out", "TimeoutError");
  };
  renderDashboard();
  const light = within(await screen.findByRole("group", { name: "Ceiling" }));
  fireEvent.click(light.getByRole("button", { name: "Turn off" }));
  expect(await light.findByText("Result unclear · device not reachable")).toBeTruthy();
  expect(request.mock.calls.filter(([path]) => path === "/api/v1/actions")).toHaveLength(3); // the first try and two retries
  cleanup();
  answers["/api/v1/actions"] = () => {
    throw new TypeError("Failed to fetch");
  };
  renderDashboard();
  const again = within(await screen.findByRole("group", { name: "Ceiling" }));
  fireEvent.click(again.getByRole("button", { name: "Turn off" }));
  expect(await again.findByText("Result unclear · device not reachable")).toBeTruthy();
  cleanup();
  answers["/api/v1/actions"] = () => {
    throw new ApiError(403, "ACTION_NOT_ALLOWED", "Not allowed");
  };
  renderDashboard();
  const refused = within(await screen.findByRole("group", { name: "Ceiling" }));
  fireEvent.click(refused.getByRole("button", { name: "Turn off" }));
  expect(await refused.findByText("Failed · nothing was changed")).toBeTruthy();
  expect(refused.getByText("This action is not allowed for you.")).toBeTruthy();
});

it("an invalid frame or a sequence gap closes the socket for a fresh snapshot", async () => {
  renderDashboard();
  await vi.waitFor(() => expect(sockets).toHaveLength(1));
  act(() => sockets[0]!.receive({ type: "resource.created", sequence: 1 }));
  expect(sockets[0]!.close).toHaveBeenCalled();
  act(() => sockets[0]!.dispatchEvent(new Event("close")));
  await act(() => vi.advanceTimersByTimeAsync(1_000));
  await vi.waitFor(() => expect(sockets).toHaveLength(2));
  act(() => sockets[1]!.receive({ type: "snapshot", generation: 2, sequence: 0, policy_version: 7, dashboard: { status: "online", rooms: fixture.rooms } }));
  act(() => sockets[1]!.receive({ type: "resource.removed", sequence: 2, resource_id: "res_203_hum" }));
  expect(sockets[1]!.close).toHaveBeenCalled();
});

it("a frame the page cannot read after every snapshot backs off 1, 2, 4 s, then reloads the page", async () => {
  vi.spyOn(Math, "random").mockReturnValue(1); // no jitter: the delays are exact
  const reload = vi.fn();
  vi.stubGlobal("location", { ...window.location, reload });
  renderDashboard();
  const snapshotThenJunk = (socket: FakeSocket) =>
    act(() => {
      socket.receive({ type: "snapshot", generation: 2, sequence: 0, policy_version: 7, dashboard: { status: "online", rooms: fixture.rooms } });
      socket.receive({ type: "resource.created", sequence: 1 }); // e.g. a newer gateway's event
      socket.dispatchEvent(new Event("close"));
    });
  await vi.waitFor(() => expect(sockets).toHaveLength(1));
  for (const [n, delay] of [1_000, 2_000, 4_000].entries()) {
    snapshotThenJunk(sockets[n]!);
    await act(() => vi.advanceTimersByTimeAsync(delay - 1));
    expect(sockets).toHaveLength(n + 1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    await vi.waitFor(() => expect(sockets).toHaveLength(n + 2));
  }
  expect(reload).not.toHaveBeenCalled();
  snapshotThenJunk(sockets[3]!);
  await act(() => vi.advanceTimersByTimeAsync(8_000));
  await vi.waitFor(() => expect(sockets).toHaveLength(5));
  snapshotThenJunk(sockets[4]!); // the fifth unreadable frame in a row: this page is older than the gateway
  expect(reload).toHaveBeenCalledTimes(1);
  vi.unstubAllGlobals();
});

it("a click answered late keeps its click time, a 5xx is unknown", async () => {
  let fail: (error: unknown) => void = () => {};
  answers["/api/v1/actions"] = () => new Promise((_, reject) => (fail = reject));
  renderDashboard();
  const light = within(await screen.findByRole("group", { name: "Ceiling" }));
  const clicked = new Date().toISOString();
  fireEvent.click(light.getByRole("button", { name: "Turn off" }));
  await act(() => vi.advanceTimersByTimeAsync(20_000));
  act(() => fail(new ApiError(503, "UPSTREAM_UNAVAILABLE", "Unavailable")));
  expect(await light.findByText("Result unclear · device not reachable")).toBeTruthy();
  // The server's own record of the click, created at click time, wins over the local note.
  await vi.waitFor(() => expect(sockets).toHaveLength(1));
  act(() =>
    sockets[0]!.receive({
      type: "command.updated",
      command: { id: "cmd_srv", resource_id: "res_203_light", action: "light.off", status: "confirmed", created_at: clicked, updated_at: new Date().toISOString() },
    }),
  );
  expect(await light.findByText("Confirmed")).toBeTruthy();
});

it("after more than 30 s in the background the page resyncs completely", async () => {
  renderDashboard();
  await vi.waitFor(() => expect(sockets).toHaveLength(1));
  const visibility = vi.spyOn(document, "visibilityState", "get");
  visibility.mockReturnValue("hidden");
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await act(() => vi.advanceTimersByTimeAsync(31_000));
  const before = request.mock.calls.filter(([path]) => path === "/api/v1/dashboard").length;
  visibility.mockReturnValue("visible");
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await vi.waitFor(() => expect(request.mock.calls.filter(([path]) => path === "/api/v1/dashboard").length).toBe(before + 1));
  await vi.waitFor(() => expect(sockets).toHaveLength(2));
  expect(sockets[0]!.close).toHaveBeenCalled();
  visibility.mockRestore();
});

it("a lost socket locks the panel and reconnects with a new ticket", async () => {
  const openSocket = renderDashboard();
  await vi.waitFor(() => expect(sockets).toHaveLength(1));
  act(() => {
    sockets[0]!.dispatchEvent(new Event("close"));
  });
  expect(await screen.findByText("Reconnecting")).toBeTruthy();
  expect(screen.getByText(/Reconnecting · data from \d+ s ago/)).toBeTruthy();
  await act(() => vi.advanceTimersByTimeAsync(1_000));
  await vi.waitFor(() => expect(openSocket).toHaveBeenLastCalledWith("/api/v1/events", "t2"));
  act(() => sockets[1]!.receive({ type: "snapshot", generation: 2, sequence: 0, policy_version: 7, dashboard: { status: "online", rooms: fixture.rooms } }));
  expect(await screen.findByText("Connected")).toBeTruthy();
});

it("revocation over the socket ends the page", async () => {
  renderDashboard();
  await vi.waitFor(() => expect(sockets).toHaveLength(1));
  act(() => sockets[0]!.receive({ type: "session.revoked", reason: "checkout" }));
  expect(await screen.findByText("ended page")).toBeTruthy();
});

it("a session the server no longer knows ends the page", async () => {
  answers["/api/v1/me"] = () => {
    throw new ApiError(401, "UNAUTHENTICATED", "Unauthenticated");
  };
  renderDashboard();
  expect(await screen.findByText("ended page")).toBeTruthy();
});

it("the session end ends the page on time", async () => {
  answers["/api/v1/me"] = () => ({ ...me, session: { ...me.session, not_after: "2026-10-22T10:00:05Z" } });
  renderDashboard();
  await screen.findByRole("heading", { level: 1, name: "Room 203" });
  await act(() => vi.advanceTimersByTimeAsync(5_000));
  expect(await screen.findByText("ended page")).toBeTruthy();
});
