// The one place for the E2E stack's ports, origins and paths (plain JS: ingress-proxy.mjs runs without a build).
import { fileURLToPath } from "node:url";

const here = (relative) => fileURLToPath(new URL(relative, import.meta.url));

export const ROOT = here("../../../");
export const GATEWAY_DIR = here("../../../myha/gateway/");
export const WEB_DIST = here("../../../myha/web/dist/");

export const PORTS = { simulator: 18123, admin: 18099, public: 18443, ingress: 18098 };

/** The guest origin. localhost, not 127.0.0.1: Chromium treats it as a secure context, so __Host- cookies stick. */
export const PUBLIC_ORIGIN = `http://localhost:${PORTS.public}`;

/** What the Supervisor ingress would add in front of the admin listener. */
export const INGRESS = {
  url: `http://127.0.0.1:${PORTS.ingress}`,
  userId: "owner1", // the owner in the simulator's fixture
  path: "/api/hassio_ingress/e2e",
};

export const SIM_TOKEN = "sim-token";

/** Environment variable through which global setup hands the stack's state file to the tests. */
export const STATE_ENV = "MYHA_E2E_STATE";

/** Logs of simulator, gateway and proxy; CI uploads them when a run fails. */
export const STACK_LOGS = here("../stack-logs/");
