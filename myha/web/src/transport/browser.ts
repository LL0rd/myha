import { errorBody } from "../api/schema";
import { ApiError, type RequestOptions, type Transport } from ".";

/** Same-origin fetch with the session cookie; `csrf` returns the token from redeem or /me. */
export function browserTransport(csrf: () => string | null = () => null): Transport {
  return {
    async request(path, { method = "GET", body, sensitive = false, idempotencyKey }: RequestOptions = {}) {
      const headers: Record<string, string> = {};
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const token = csrf();
      if (token && (method !== "GET" || sensitive)) headers["X-MyHA-CSRF"] = token;
      if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
      const response = await fetch(path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "include",
        redirect: "manual", // the API never redirects; a redirect is an attack or a proxy error
      });
      if (response.type === "opaqueredirect") throw new ApiError(0, null, "unexpected redirect");
      if (!response.ok) {
        const parsed = errorBody.safeParse(await response.json().catch(() => null));
        if (parsed.success) throw new ApiError(response.status, parsed.data.error.code, parsed.data.error.message);
        throw new ApiError(response.status, null, `HTTP ${response.status}`);
      }
      return response.status === 204 ? undefined : response.json();
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
