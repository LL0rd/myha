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
  // A new view starts with Home Assistant online; the server follows each snapshot with the real connection status.
  const fresh = (status: LiveView["status"], rooms: Room[], policyVersion: number) => {
    clearTimeout(staleTimer);
    set({ status, rooms, policyVersion, staleSince: null, cleared: false, upstream: "online" });
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
    /** Applies one event; false when a delta is missing and only a fresh snapshot can repair the view. */
    apply(event: ServerEvent): boolean {
      switch (event.type) {
        case "snapshot":
          sequence = event.sequence;
          fresh(event.dashboard.status, event.dashboard.rooms, event.policy_version);
          return true;
        case "resource.updated":
        case "resource.removed":
          if (event.sequence <= sequence) return true; // a repeat after a reconnect
          if (event.sequence !== sequence + 1) return false; // a gap: never guess what was missed
          sequence = event.sequence;
          if (event.type === "resource.removed") {
            withRooms((rooms) =>
              rooms.map((room) => ({ ...room, resources: room.resources.filter((r) => r.id !== event.resource_id) })),
            );
            return true;
          }
          if (!view.rooms.some((room) => room.id === event.room_id)) return false; // a room this view lacks: resync
          withRooms((rooms) =>
            rooms.map((room) => {
              if (room.id !== event.room_id) return room;
              const at = room.resources.findIndex((r) => r.id === event.resource.id);
              const resources = [...room.resources];
              if (at < 0) resources.push(event.resource);
              else resources[at] = event.resource;
              return { ...room, resources };
            }),
          );
          return true;
        case "policy.changed":
          set({ policyVersion: event.policy_version }); // a new snapshot follows
          return true;
        case "session.revoked":
          set({ ended: true });
          return true;
        case "connection.status":
          set({ upstream: event.status });
          return true;
        case "command.updated":
          putCommand(event.command);
          return true;
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
    /** Ends the page at `notAfter` on the server's clock (`offsetMs` = server minus local), also beyond setTimeout's range. */
    expireAt(notAfter: string, offsetMs = 0) {
      const end = Date.parse(notAfter) - offsetMs;
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
      fades.clear();
      // Their fade timers are gone; a confirmed command would otherwise stay forever.
      if (view.commands.some((c) => c.status === "confirmed"))
        set({ commands: view.commands.filter((c) => c.status !== "confirmed") });
    },
  };
}

export type LiveStore = ReturnType<typeof createLiveStore>;
