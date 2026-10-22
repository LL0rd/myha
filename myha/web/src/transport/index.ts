import type { PublicErrorCode } from "../api/schema";

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  /** A GET that needs the CSRF token too; unsafe methods always send it. */
  sensitive?: boolean;
  idempotencyKey?: string;
}

/** How the app talks to the gateway: cookies and CSRF in the browser, a bearer token natively (Plan 3). */
export interface Transport {
  /** The parsed JSON body of a 2xx answer, `undefined` for 204; throws ApiError otherwise. */
  request(path: string, options?: RequestOptions): Promise<unknown>;
  /** A socket to `path` on the page's host that authenticates with `ticket` as its first frame. */
  openSocket(path: string, ticket: string): WebSocket;
}

export class ApiError extends Error {
  override name = "ApiError";
  constructor(
    /** HTTP status; 0 for a refused redirect. */
    readonly status: number,
    /** The public error code from the body; null when the body carried none. */
    readonly code: PublicErrorCode | null,
    message: string,
  ) {
    super(message);
  }
}
