import { useI18n } from "../i18n";
import { ConnectionPill } from "./ConnectionPill";
import styles from "./guest-panel.module.css";
import { noticeIcon as NoticeIcon } from "./icons";
import { ResourceCard } from "./ResourceCard";
import type { Action, ActionParams, Command, DashboardStatus, Layout, Room } from "./types";


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
  const { t } = useI18n();
  const latest = new Map<string, Command>();
  for (const c of commands) {
    const seen = latest.get(c.resource_id);
    // By creation: an older command timing out to "unknown" must not hide a newer one.
    if (!seen || Date.parse(c.created_at) >= Date.parse(seen.created_at)) latest.set(c.resource_id, c);
  }
  const notice = status === "online" ? null : t(`notice.${status}`);
  return (
    <div className={styles.panel} data-layout={layout} data-wall={wall || undefined} data-explain={explain || undefined}>
      <header className={styles.header}>
        <div className={styles.titles}>
          <h1 className={styles.title}>{title}</h1>
          {subtitle && <div className={styles.subtitle}>{subtitle}</div>}
        </div>
        <ConnectionPill status={status} label={t(`status.${status}`)} />
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
