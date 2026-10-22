import styles from "./guest-panel.module.css";
import { commandIcon } from "./icons";
import type { CommandState } from "./types";

export function CommandStatus({ status, text }: { status: CommandState; text: string }) {
  const Icon = commandIcon[status];
  return (
    <div className={styles.command} data-status={status}>
      <Icon size={18} aria-hidden="true" className={styles.commandIcon} />
      <span>{text}</span>
    </div>
  );
}
