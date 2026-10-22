// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from ".";
import { browserTransport } from "./browser";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(response: { ok?: boolean; status?: number; type?: ResponseType; body?: unknown }) {
  const fetchMock = vi.fn<typeof fetch>(
    async () =>
      ({
        ok: response.ok ?? true,
        status: response.status ?? 200,
        type: response.type ?? "basic",
        json: async () => response.body,
      }) as Response,
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const init = (fetchMock: ReturnType<typeof stubFetch>) => fetchMock.mock.calls[0]?.[1] ?? {};
const headers = (fetchMock: ReturnType<typeof stubFetch>) => new Headers(init(fetchMock).headers);

it("posts JSON with the CSRF token, cookies and no redirects", async () => {
  const fetchMock = stubFetch({ body: { ticket: "t", expires_in: 30 } });
  const transport = browserTransport(() => "csrf-123");
  const result = await transport.request("/api/v1/ws-ticket", { method: "POST", body: { a: 1 }, idempotencyKey: "key-1" });
  expect(result).toEqual({ ticket: "t", expires_in: 30 });
  expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/ws-ticket");
  expect(init(fetchMock)).toMatchObject({ method: "POST", credentials: "include", redirect: "manual", body: '{"a":1}' });
  expect(headers(fetchMock).get("X-MyHA-CSRF")).toBe("csrf-123");
  expect(headers(fetchMock).get("Idempotency-Key")).toBe("key-1");
  expect(headers(fetchMock).get("Content-Type")).toBe("application/json");
});

it("sends no CSRF token on a plain GET, but on a sensitive one", async () => {
  const fetchMock = stubFetch({ body: {} });
  const transport = browserTransport(() => "csrf-123");
  await transport.request("/api/v1/me");
  expect(init(fetchMock).method).toBe("GET");
  expect(headers(fetchMock).has("X-MyHA-CSRF")).toBe(false);
  fetchMock.mockClear();
  await transport.request("/api/v1/me", { sensitive: true });
  expect(headers(fetchMock).get("X-MyHA-CSRF")).toBe("csrf-123");
});

it("fails on a redirect instead of following it", async () => {
  stubFetch({ ok: false, status: 0, type: "opaqueredirect" });
  await expect(browserTransport().request("/api/v1/me")).rejects.toMatchObject({ name: "ApiError", status: 0, code: null });
});

it("fails on non-2xx with the public error from the body", async () => {
  stubFetch({ ok: false, status: 403, body: { error: { code: "UNAUTHENTICATED", message: "Not signed in" } } });
  const error = await browserTransport().request("/api/v1/me").catch((e: unknown) => e);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ status: 403, code: "UNAUTHENTICATED", message: "Not signed in" });
});

it("fails on non-2xx with an unreadable body without a code", async () => {
  stubFetch({ ok: false, status: 502, body: "<html>bad gateway</html>" });
  await expect(browserTransport().request("/api/v1/me")).rejects.toMatchObject({ status: 502, code: null });
});

it("returns nothing for 204", async () => {
  stubFetch({ status: 204, body: undefined });
  expect(await browserTransport().request("/api/v1/auth/logout", { method: "POST" })).toBeUndefined();
});

class FakeSocket {
  static last: FakeSocket;
  sent: string[] = [];
  listeners: Record<string, (() => void)[]> = {};
  constructor(public url: string) {
    FakeSocket.last = this;
  }
  addEventListener(type: string, fn: () => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  send(data: string) {
    this.sent.push(data);
  }
}

it("opens the socket on the page's host and sends the ticket as the first frame", () => {
  vi.stubGlobal("WebSocket", FakeSocket);
  browserTransport().openSocket("/api/v1/events", "tkt");
  const socket = FakeSocket.last;
  expect(socket.url).toBe(`ws://${location.host}/api/v1/events`);
  expect(socket.sent).toEqual([]);
  socket.listeners.open?.forEach((fn) => fn());
  expect(socket.sent).toEqual([JSON.stringify({ type: "auth", ticket: "tkt" })]);
});
