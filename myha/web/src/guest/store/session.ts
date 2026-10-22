// One guest session per page: the transport with its CSRF token, the typed API, and what the join link carried.
import { createContext, useContext, useEffect, useState } from "react";
import { ZodError } from "zod";
import { apiClient, type ApiClient } from "../../api/client";
import { ApiError, type Transport } from "../../transport";
import { browserTransport } from "../../transport/browser";

export interface JoinFragment {
  secret: string | null;
  /** Instance id and certificate pin, kept in memory only for the native pairing of Plan 3. */
  inst: string | null;
  fp: string | null;
}

/**
 * Reads the invite from `#i=…&inst=…&fp=…` and removes the fragment from the address bar at once,
 * before any request, so the secret never stays in history, screenshots or a shared URL.
 */
export function takeJoinFragment(): JoinFragment {
  const params = new URLSearchParams(location.hash.slice(1)); // decodes the percent-encoded pin
  if (location.hash) history.replaceState(null, "", "/join");
  return { secret: params.get("i"), inst: params.get("inst"), fp: params.get("fp") };
}

export function createSession(makeTransport: (csrf: () => string | null) => Transport = browserTransport) {
  let csrf: string | null = null;
  const transport = makeTransport(() => csrf);
  const raw = apiClient(transport);
  // The one place where an answer that fails its schema becomes an ordinary error, never a crash.
  const api = Object.fromEntries(
    Object.entries(raw).map(([name, call]) => [
      name,
      async (...args: unknown[]) => {
        try {
          return await (call as (...a: unknown[]) => Promise<unknown>)(...args);
        } catch (error) {
          throw error instanceof ZodError ? new ApiError(0, null, "invalid response") : error;
        }
      },
    ]),
  ) as ApiClient;
  const pairing: Pick<JoinFragment, "inst" | "fp"> = { inst: null, fp: null };
  return {
    transport,
    api,
    setCsrf(token: string | null) {
      csrf = token;
    },
    /** Instance id and pin of the redeemed link, for the native pairing of Plan 3. */
    pairing: () => ({ ...pairing }),
    keepPairing(fragment: JoinFragment) {
      pairing.inst = fragment.inst;
      pairing.fp = fragment.fp;
    },
  };
}

export type Session = ReturnType<typeof createSession>;

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside SessionContext");
  return session;
}

type Config = Awaited<ReturnType<ApiClient["config"]>>;

/** Instance name and operator contact for pages without a session; null until loaded or on failure. */
export function useConfig(): Config | null {
  const { api } = useSession();
  const [config, setConfig] = useState<Config | null>(null);
  useEffect(() => {
    let current = true;
    api.config().then(
      (c) => current && setConfig(c),
      () => {}, // the pages work without it, just without the contact line
    );
    return () => {
      current = false;
    };
  }, [api]);
  return config;
}
