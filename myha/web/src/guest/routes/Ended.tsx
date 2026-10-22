import { useI18n } from "../../i18n";
import styles from "../guest.module.css";
import { useConfig } from "../store/session";

export function Ended() {
  const { t } = useI18n();
  const config = useConfig();
  return (
    <main className={styles.page}>
      <h1 className={styles.title}>{t("ended.title")}</h1>
      <p className={styles.hint}>{t("ended.text")}</p>
      {config?.operator_contact && <p className={styles.small}>{t("ended.contact", { contact: config.operator_contact })}</p>}
    </main>
  );
}
