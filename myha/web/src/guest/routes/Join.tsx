import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n } from "../../i18n";
import { ApiError } from "../../transport";
import styles from "../guest.module.css";
import { useConfig, useSession, type JoinFragment } from "../store/session";
import { usePage } from "../usePage";

export function Join({ fragment }: { fragment: JoinFragment | null }) {
  const { api, setCsrf, keepPairing } = useSession();
  const { t } = useI18n();
  const navigate = useNavigate();
  const config = useConfig();
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const contact = config?.operator_contact ? <p className={styles.small}>{t("join.contact", { contact: config.operator_contact })}</p> : null;
  const secret = fragment?.secret;
  usePage(secret ? t("join.title") : t("join.incomplete_title"));

  if (!secret)
    return (
      <main className={styles.page}>
        <h1 className={styles.title} tabIndex={-1}>
          {t("join.incomplete_title")}
        </h1>
        <p className={styles.hint}>{t("join.incomplete_text")}</p>
        {contact}
      </main>
    );

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const answer = await api.redeem(secret, label.trim() || undefined);
      setCsrf(answer.csrf_token);
      keepPairing(fragment);
      navigate("/", { replace: true });
    } catch (e) {
      if (!(e instanceof ApiError && e.status >= 400 && e.status < 500)) {
        setError(t("error.generic")); // the network or the server, not the link
        return setBusy(false);
      }
      // Refused: maybe this browser redeemed the link before and still holds its session.
      const session = await api.me().catch(() => null);
      if (session) {
        setCsrf(session.csrf_token);
        keepPairing(fragment);
        return navigate("/", { replace: true });
      }
      setError(e.code === "RATE_LIMITED" ? t("error.RATE_LIMITED") : t("join.failed"));
      setBusy(false);
    }
  };

  return (
    <main className={styles.page}>
      <div>
        <h1 className={styles.title} tabIndex={-1}>
          {t("join.title")}
        </h1>
        {config && <div className={styles.muted}>{config.instance_name}</div>}
      </div>
      <p className={styles.hint}>{t("join.hint")}</p>
      <form className={styles.form} onSubmit={submit}>
        <label className={styles.field}>
          {t("join.device_label")}
          <input
            value={label}
            maxLength={60}
            autoComplete="off"
            placeholder={t("join.device_placeholder")}
            onChange={(e) => setLabel(e.currentTarget.value)}
          />
        </label>
        <div aria-live="polite">{error && <p className={styles.error}>{error}</p>}</div>
        <button type="submit" className={styles.primary} disabled={busy}>
          {busy ? t("join.busy") : t("join.button")}
        </button>
      </form>
      {contact}
    </main>
  );
}
