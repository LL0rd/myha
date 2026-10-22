import styles from "./guest-panel.module.css";
import type { DashboardStatus } from "./types";

export function ConnectionPill({ status, label }: { status: DashboardStatus; label: string }) {
  return (
    <div className={styles.pill} data-status={status} role="status">
      <span className={styles.dot} aria-hidden="true" />
      {label}
    </div>
  );
}
