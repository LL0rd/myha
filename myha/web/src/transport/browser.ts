import { errorBody } from "../api/schema";
import { ApiError, REQUEST_TIMEOUT_MS, type RequestOptions, type Transport } from ".";

// AbortSignal.timeout is missing in older WebViews (before Chrome 103).
function timeoutSignal(ms: number): AbortSignal {
  if (typeof AbortSignal.timeout === "function") return AbortSignal.timeout(ms);
  const controller = new AbortController();
  setTimeout(() => controller.abort(), ms);
  return controller.signal;
}

/** Same-origin fetch with the session cookie; `csrf` returns the token from redeem or /me. */
export function browserTransport(csrf: () => string | null = () => null): Transport {
  let offset = 0;
  return {
    serverOffsetMs: () => offset,
    async request(path, { method = "GET", body, sensitive = false, idempotencyKey, signal }: RequestOptions = {}) {
      const headers: Record<string, string> = {};
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const token = csrf();
      if (token && (method !== "GET" || sensitive)) headers["X-MyHA-CSRF"] = token;
      if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
      const active = signal ?? timeoutSignal(REQUEST_TIMEOUT_MS);
      // Our own timeout surfaces as TimeoutError, whether it hits the request or the body read.
      const timedOut = () => !signal && active.aborted;
      let response: Response;
      try {
        response = await fetch(path, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
          credentials: "include",
          redirect: "manual", // the API never redirects; a redirect is an attack or a proxy error
          signal: active,
        });
      } catch (error) {
        throw timedOut() ? new DOMException("request timed out", "TimeoutError") : error;
      }
      if (response.type === "opaqueredirect") throw new ApiError(0, null, "unexpected redirect");
      const date = Date.parse(response.headers?.get("date") ?? ""); // whole seconds: good enough for timers on session ends
      if (!Number.isNaN(date)) offset = date - Date.now();
      if (!response.ok) {
        const parsed = errorBody.safeParse(await response.json().catch(() => null));
        if (parsed.success) throw new ApiError(response.status, parsed.data.error.code, parsed.data.error.message);
        throw new ApiError(response.status, null, `HTTP ${response.status}`);
      }
      if (response.status === 204) return undefined;
      try {
        return (await response.json()) as unknown;
      } catch (error) {
        if (active.aborted) throw timedOut() ? new DOMException("request timed out", "TimeoutError") : error;
        throw new ApiError(response.status, null, "invalid response");
      }
    },

    openSocket(path, ticket) {
      const url = new URL(path, location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(url.toString());
      // The ticket goes in the first frame, never into the URL, where logs would keep it.
      socket.addEventListener("open", () => socket.send(JSON.stringify({ type: "auth", ticket })));
      return socket;
    },
  };
}
