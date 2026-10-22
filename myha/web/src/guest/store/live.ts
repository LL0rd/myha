// The live dashboard: the server view kept current by WebSocket events, plus the client's own view of the connection.
import type { CommandPayload, DashboardResponse, ServerEvent } from "../../api/schema";
import type { DashboardStatus, Room } from "../../ui/types";

export const STALE_CLEAR_MS = 30_000; // after this long without a connection, shown states are dropped
export const CONFIRMED_FADE_MS = 4_800; // as in the design mockup
const MAX_TIMER_MS = 2 ** 31 - 1; // setTimeout fires at once beyond this

export interface LiveView {
  status: "online" | "waiting" | "expired"; // the server's view of the session window
  rooms: Room[];
  commands: CommandPayload[];
  /** When the socket was lost; null while a fresh snapshot is in. */
  staleSince: number | null;
  /** States were dropped after STALE_CLEAR_MS. */
  cleared: boolean;
  upstream: "online" | "stale" | "offline";
  policyVersion: number | null;
  ended: boolean;
}

/** What the panel shows: locked whenever the data may be outdated. */
export function panelStatus(view: LiveView): DashboardStatus {
  if (view.status !== "online") return view.status;
  return view.staleSince !== null || view.upstream !== "online" ? "stale" : "online";
}

const unknownStates = (rooms: Room[]): Room[] =>
  rooms.map((room) => ({
    ...room,
    resources: room.resources.map((r) => (r.state ? { ...r, state: { availability: "unknown" as const } } : r)),
  }));

export function createLiveStore() {
  let view: LiveView = {
    status: "online",
    rooms: [],
    commands: [],
    staleSince: null,
    cleared: false,
    upstream: "online",
    policyVersion: null,
    ended: false,
  };
  let sequence = 0;
  let staleTimer: ReturnType<typeof setTimeout> | undefined;
  let endTimer: ReturnType<typeof setTimeout> | undefined;
  const fades = new Map<string, ReturnType<typeof setTimeout>>();
  const listeners = new Set<() => void>();

  const set = (next: Partial<LiveView>) => {
    view = { ...view, ...next };
    listeners.forEach((fn) => fn());
  };
  const fresh = (status: LiveView["status"], rooms: Room[], policyVersion: number) => {
    clearTimeout(staleTimer);
    set({ status, rooms, policyVersion, staleSince: null, cleared: false });
  };
  const putCommand = (command: CommandPayload) => {
    clearTimeout(fades.get(command.id));
    fades.delete(command.id);
    if (command.status === "confirmed") {
      const drop = () => set({ commands: view.commands.filter((c) => c.id !== command.id) });
      fades.set(command.id, setTimeout(drop, CONFIRMED_FADE_MS));
    }
    set({ commands: [...view.commands.filter((c) => c.id !== command.id), command] });
  };
  const withRooms = (fn: (rooms: Room[]) => Room[]) =>
    set({ rooms: fn(view.rooms).filter((room) => room.resources.length > 0) });

  return {
    get: () => view,
    subscribe(fn: () => void) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    /** GET /api/v1/dashboard: the view before the socket's first snapshot. */
    load(dashboard: DashboardResponse) {
      fresh(dashboard.status, dashboard.rooms, dashboard.policy_version);
    },
    apply(event: ServerEvent) {
      switch (event.type) {
        case "snapshot":
          sequence = event.sequence;
          return fresh(event.dashboard.status, event.dashboard.rooms, event.policy_version);
        case "resource.updated":
        case "resource.removed":
          if (event.sequence <= sequence) return; // a repeat after a reconnect
          sequence = event.sequence;
          if (event.type === "resource.removed")
            return withRooms((rooms) =>
              rooms.map((room) => ({ ...room, resources: room.resources.filter((r) => r.id !== event.resource_id) })),
            );
          // shortcut: an update for a room the client does not know is dropped; the next snapshot brings it.
          return withRooms((rooms) =>
            rooms.map((room) => {
              if (room.id !== event.room_id) return room;
              const at = room.resources.findIndex((r) => r.id === event.resource.id);
              const resources = [...room.resources];
              if (at < 0) resources.push(event.resource);
              else resources[at] = event.resource;
              return { ...room, resources };
            }),
          );
        case "policy.changed":
          return set({ policyVersion: event.policy_version }); // a new snapshot follows
        case "session.revoked":
          return set({ ended: true });
        case "connection.status":
          return set({ upstream: event.status });
        case "command.updated":
          return putCommand(event.command);
      }
    },
    /** A command the client just sent; an event about it may have come first and wins. */
    addCommand(command: CommandPayload) {
      if (!view.commands.some((c) => c.id === command.id)) putCommand(command);
    },
    /** The socket is gone; the panel stays locked until the next snapshot. */
    socketLost() {
      if (view.staleSince !== null) return;
      staleTimer = setTimeout(() => set({ rooms: unknownStates(view.rooms), cleared: true }), STALE_CLEAR_MS);
      set({ staleSince: Date.now() });
    },
    /** Ends the page at `notAfter`, also beyond setTimeout's range. */
    expireAt(notAfter: string) {
      const end = Date.parse(notAfter);
      const arm = () => {
        clearTimeout(endTimer);
        const left = end - Date.now();
        if (left <= 0) return set({ ended: true });
        endTimer = setTimeout(arm, Math.min(left, MAX_TIMER_MS));
      };
      arm();
    },
    /** Stops the timers; subscribers stay, since React may mount the same store again. */
    dispose() {
      clearTimeout(staleTimer);
      clearTimeout(endTimer);
      fades.forEach(clearTimeout);
    },
  };
}

export type LiveStore = ReturnType<typeof createLiveStore>;
