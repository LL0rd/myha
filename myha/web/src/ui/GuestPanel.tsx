import { ConnectionPill } from "./ConnectionPill";
import styles from "./guest-panel.module.css";
import { noticeIcon as NoticeIcon } from "./icons";
import { ResourceCard } from "./ResourceCard";
import type { Action, ActionParams, Command, DashboardStatus, Layout, Room } from "./types";

// Every visible text of the panel; Task 8.3 swaps in translations.
export const strings = {
  status: { online: "Connected", stale: "Reconnecting", waiting: "Not started yet", expired: "Access ended" } as Record<DashboardStatus, string>,
  notice: {
    stale: "Controls are paused until the connection is back. Shown values may be outdated.",
    waiting: "Your stay has not started yet. Controls unlock when it begins.",
    expired: "This access has ended.",
  } as Partial<Record<DashboardStatus, string>>,
  command: {
    accepted: "Checking and sending…",
    sent: "Accepted by the hub · waiting for the device",
    confirmed: "Confirmed",
    unknown: "Result unclear · device not reachable",
    failed: "Failed · nothing was changed",
  },
  turnOn: "Turn on",
  turnOff: "Turn off",
  open: "Open",
  stop: "Stop",
  close: "Close",
  brightness: "Brightness",
  lower: "Lower target",
  raise: "Raise target",
  celsius: "°C",
  mode: { off: "Off", cool: "Cool", heat: "Heat" } as Record<string, string>,
  on: "On",
  off: "Off",
  onLevel: (pct: number) => `On · ${pct} %`,
  now: (c: string) => `Now ${c} °C`,
  openPct: (pct: number) => `${pct} % open`,
  range: (ranges: string, steps: string) => `Allowed ${ranges} °C · ${steps}° steps`,
  readOnlyTemp: "Temperature is read-only for you",
  unknown: "State unknown",
  unavailable: "Unavailable",
  notShared: "State not shared",
};
export type Strings = typeof strings;

export interface GuestPanelProps {
  rooms: Room[];
  layout?: Layout;
  wall?: boolean;
  simulate?: boolean;
  explain?: boolean;
  status: DashboardStatus;
  title: string;
  subtitle?: string;
  footer?: string | null;
  commands?: Command[];
  onAction: (resourceId: string, action: Action, params: ActionParams) => void;
}

export function GuestPanel({
  rooms,
  layout = "tiles",
  wall = false,
  simulate = false,
  explain = false,
  status,
  title,
  subtitle,
  footer,
  commands = [],
  onAction,
}: GuestPanelProps) {
  const t = strings;
  const latest = new Map<string, Command>();
  for (const c of commands) {
    const seen = latest.get(c.resource_id);
    if (!seen || c.updated_at >= seen.updated_at) latest.set(c.resource_id, c);
  }
  const notice = t.notice[status];
  return (
    <div className={styles.panel} data-layout={layout} data-wall={wall || undefined} data-explain={explain || undefined}>
      <header className={styles.header}>
        <div className={styles.titles}>
          <h1 className={styles.title}>{title}</h1>
          {subtitle && <div className={styles.subtitle}>{subtitle}</div>}
        </div>
        <ConnectionPill status={status} label={t.status[status]} />
      </header>
      {notice && (
        <div className={styles.notice}>
          <NoticeIcon size={20} aria-hidden="true" className={styles.muted} />
          <span>{notice}</span>
        </div>
      )}
      {rooms.map((room) => {
        const resources =
          layout === "focus"
            ? [...room.resources.filter((r) => r.kind === "climate"), ...room.resources.filter((r) => r.kind !== "climate")]
            : room.resources;
        return (
          <section key={room.id} className={styles.room} aria-labelledby={`room-${room.id}`}>
            <h2 id={`room-${room.id}`} className={styles.roomName}>
              {room.name}
            </h2>
            <div className={styles.grid}>
              {resources.map((r) => (
                <ResourceCard
                  key={r.id}
                  resource={r}
                  command={latest.get(r.id)}
                  live={status === "online"}
                  simulate={simulate}
                  explain={explain}
                  t={t}
                  onAction={onAction}
                />
              ))}
            </div>
          </section>
        );
      })}
      {footer && <footer className={styles.footer}>{footer}</footer>}
    </div>
  );
}
