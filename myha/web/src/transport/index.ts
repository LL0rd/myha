import type { PublicErrorCode } from "../api/schema";

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  /** A GET that needs the CSRF token too; unsafe methods always send it. */
  sensitive?: boolean;
  idempotencyKey?: string;
  /** Cancels the request; without one, a request gives up after REQUEST_TIMEOUT_MS. */
  signal?: AbortSignal;
}

export const REQUEST_TIMEOUT_MS = 15_000;

/** How the app talks to the gateway: cookies and CSRF in the browser, a bearer token natively (Plan 3). */
export interface Transport {
  /**
   * The parsed JSON body of a 2xx answer, `undefined` for 204. Throws ApiError for a non-2xx answer, a redirect
   * or a 2xx body that is not JSON ("invalid response"); a DOMException named TimeoutError after the timeout;
   * a TypeError when the network fails.
   */
  request(path: string, options?: RequestOptions): Promise<unknown>;
  /** A socket to `path` on the page's host that authenticates with `ticket` as its first frame. */
  openSocket(path: string, ticket: string): WebSocket;
  /** Server clock minus local clock in ms, from the last answer's Date header; 0 before any. */
  serverOffsetMs?(): number;
}

export class ApiError extends Error {
  override name = "ApiError";
  constructor(
    /** HTTP status; 0 when none applies: a refused redirect, or an answer that failed its schema (guest/store/session.ts). */
    readonly status: number,
    /** The public error code from the body; null when the body carried none. */
    readonly code: PublicErrorCode | null,
    message: string,
  ) {
    super(message);
  }
}
