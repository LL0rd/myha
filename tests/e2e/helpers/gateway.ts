// Global setup: simulator, dev seed, gateway and ingress proxy for one test run; returns the teardown.
import { spawn, type ChildProcess } from "node:child_process";
import { createWriteStream, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { GATEWAY_DIR, PORTS, PUBLIC_ORIGIN, ROOT, SIM_TOKEN, STACK_LOGS, STATE_ENV, WEB_DIST } from "./constants.mjs";

export interface StackState {
  /** One-time join URLs of the seeded stay, in invite order. */
  joinUrls: string[];
  /** Where the joining test stores the guest's browser state (the session cookie) for the later cases. */
  guestState: string;
}

/** The state global setup wrote for this run. */
export function stackState(): StackState {
  const file = process.env[STATE_ENV];
  if (!file) throw new Error(`${STATE_ENV} unset: run through playwright.config.ts`);
  return JSON.parse(readFileSync(file, "utf8")) as StackState;
}

const listening = (port: number) =>
  new Promise<boolean>((resolve) => {
    const socket = net.connect(port, "127.0.0.1");
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
  });

/** Polls a readiness check; startup, not a test, so a short poll interval is fine. */
async function waitFor(what: string, ready: () => Promise<boolean>, timeoutMs = 60_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!(await ready())) {
    if (Date.now() > deadline) throw new Error(`${what} not ready after ${timeoutMs / 1000} s; see ${STACK_LOGS}`);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}

const uv = (...args: string[]) => ["--directory", GATEWAY_DIR, "run", "--locked", ...args];

/** A long-running process in its own process group, so the teardown also reaches what uv starts. */
function start(name: string, command: string, args: string[], env: NodeJS.ProcessEnv): ChildProcess {
  const log = createWriteStream(path.join(STACK_LOGS, `${name}.log`));
  const child = spawn(command, args, { env: { ...process.env, ...env }, detached: true, stdio: ["ignore", "pipe", "pipe"] });
  child.stdout?.pipe(log);
  child.stderr?.pipe(log);
  return child;
}

async function stop(child: ChildProcess): Promise<void> {
  if (child.pid === undefined || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  const group = (signal: NodeJS.Signals) => {
    try {
      process.kill(-child.pid!, signal);
    } catch {
      // already gone
    }
  };
  group("SIGTERM");
  const killer = setTimeout(() => group("SIGKILL"), 15_000); // the gateway shuts down within 10 s
  await exited;
  clearTimeout(killer);
}

/** A short command; its stdout, or an error with its stderr. */
function run(command: string, args: string[], env: NodeJS.ProcessEnv): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { env: { ...process.env, ...env }, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    let err = "";
    child.stdout.on("data", (chunk) => (out += chunk));
    child.stderr.on("data", (chunk) => (err += chunk));
    child.once("error", reject);
    child.once("exit", (code) => (code === 0 ? resolve(out) : reject(new Error(`${args.join(" ")} failed (${code}):\n${err}`))));
  });
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  if (!existsSync(path.join(WEB_DIST, "guest", "index.html")))
    throw new Error("no web bundle: run `pnpm --dir myha/web build` first");
  for (const [name, port] of Object.entries(PORTS))
    if (await listening(port)) throw new Error(`port ${port} (${name}) is taken: another E2E run or a dev stack?`);

  rmSync(STACK_LOGS, { recursive: true, force: true });
  mkdirSync(STACK_LOGS, { recursive: true });
  const dataDir = mkdtempSync(path.join(os.tmpdir(), "myha-e2e-"));
  const env = {
    MYHA_DATA_DIR: dataDir,
    MYHA_HA_WS_URL: `ws://127.0.0.1:${PORTS.simulator}/api/websocket`,
    MYHA_SUPERVISOR_URL: `http://127.0.0.1:${PORTS.simulator}`,
    SUPERVISOR_TOKEN: SIM_TOKEN,
    MYHA_TRUSTED_INGRESS_PEER: "127.0.0.1",
    MYHA_ADMIN_PORT: String(PORTS.admin),
    MYHA_PUBLIC_PORT: String(PORTS.public),
    MYHA_PUBLIC_ENABLED: "1",
    MYHA_PUBLIC_ORIGIN: PUBLIC_ORIGIN,
    MYHA_WEB_DIST: WEB_DIST,
  };
  const children: ChildProcess[] = [];
  const teardown = async () => {
    for (const child of children.reverse()) await stop(child); // proxy, gateway, then the simulator
    rmSync(dataDir, { recursive: true, force: true });
  };

  try {
    children.push(start("simulator", "uv", uv("myha-simulator", "--port", String(PORTS.simulator)), {}));
    await waitFor("simulator", () => listening(PORTS.simulator));
    // Before the gateway starts: the policy loader then reads the seeded world at startup.
    const seeded = await run("uv", uv("myha-gateway", "dev-seed", "room203"), env);
    const joinUrls = seeded.split("\n").filter((line) => line.startsWith(PUBLIC_ORIGIN));
    if (joinUrls.length < 2) throw new Error(`dev-seed printed ${joinUrls.length} join URLs`);
    children.push(start("gateway", "uv", uv("myha-gateway", "serve"), env));
    children.push(start("ingress", process.execPath, [path.join(ROOT, "tests/e2e/ingress-proxy.mjs")], {}));
    await waitFor("public listener", async () => {
      const answer = await fetch(`http://127.0.0.1:${PORTS.public}/healthz`).catch(() => null);
      return answer?.ok === true;
    });
    await waitFor("ingress proxy", () => listening(PORTS.ingress));

    const stateFile = path.join(dataDir, "e2e-state.json");
    const guestState = path.join(dataDir, "guest-state.json");
    writeFileSync(stateFile, JSON.stringify({ joinUrls, guestState } satisfies StackState));
    process.env[STATE_ENV] = stateFile; // the workers start after global setup and inherit it
    return teardown;
  } catch (error) {
    await teardown();
    throw error;
  }
}
