import { useEffect, useState, useSyncExternalStore } from "react";
import { useNavigate } from "react-router-dom";
import { events } from "../../api/schema";
import { useI18n, type Lang } from "../../i18n";
import { ApiError } from "../../transport";
import { GuestPanel } from "../../ui/GuestPanel";
import type { Action, ActionParams } from "../../ui/types";
import styles from "../guest.module.css";
import { createLiveStore, panelStatus } from "../store/live";
import { useSession } from "../store/session";

type Me = Awaited<ReturnType<ReturnType<typeof useSession>["api"]["me"]>>;

const BACKOFF_MS = [1_000, 10_000] as const; // first and longest reconnect delay
const ACTION_RETRIES = 2; // after a timeout, with the same idempotency key

/** The session is gone for good: revoked, expired or never valid. */
const sessionGone = (error: unknown) => error instanceof ApiError && (error.status === 401 || error.code === "ACCESS_EXPIRED");

/** A UUID v4 per click; crypto.randomUUID is missing in older WebViews. */
function idempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6]! & 0x0f) | 0x40;
  b[8] = (b[8]! & 0x3f) | 0x80;
  const hex = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

const formatTime = (iso: string, lang: Lang) =>
  new Intl.DateTimeFormat(lang, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));

export function Dashboard() {
  const { api, transport, setCsrf } = useSession();
  const { t, lang } = useI18n();
  const navigate = useNavigate();
  const [store] = useState(createLiveStore);
  const view = useSyncExternalStore(store.subscribe, store.get);
  const [me, setMe] = useState<Me | null>(null);
  const [failed, setFailed] = useState(false);
  const [run, setRun] = useState(0); // bumped to start over: retry, stay start, return from the background
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let begins: ReturnType<typeof setTimeout> | undefined;
    let delay: number = BACKOFF_MS[0];
    const end = () => navigate("/ended", { replace: true });

    const reconnect = () => {
      store.socketLost();
      retry = setTimeout(connect, delay);
      delay = Math.min(delay * 2, BACKOFF_MS[1]);
    };
    async function connect() {
      if (stopped) return;
      try {
        const { ticket } = await api.wsTicket(); // a fresh ticket for every attempt: tickets are single-use
        if (stopped) return;
        socket = transport.openSocket("/api/v1/events", ticket);
        socket.addEventListener("message", (message) => {
          let data: unknown;
          try {
            data = JSON.parse(String(message.data));
          } catch {
            return;
          }
          const parsed = events.any.safeParse(data);
          if (!parsed.success) return; // shortcut: an invalid frame is dropped; the next snapshot repairs the view
          if (parsed.data.type === "snapshot") delay = BACKOFF_MS[0];
          store.apply(parsed.data);
        });
        socket.addEventListener("close", () => {
          socket = null;
          if (!stopped) reconnect(); // a revoked session fails at the next ticket with 401
        });
      } catch (error) {
        if (sessionGone(error)) return end();
        if (!stopped) reconnect();
      }
    }

    (async () => {
      try {
        const who = await api.me();
        if (stopped) return;
        setCsrf(who.csrf_token);
        setMe(who);
        setFailed(false);
        store.expireAt(who.session.not_after);
        if (who.session.waiting && who.session.not_before) {
          const left = Date.parse(who.session.not_before) - Date.now();
          begins = setTimeout(() => setRun((n) => n + 1), Math.min(Math.max(left, 0), 2 ** 31 - 1));
        }
        store.load(await api.dashboard());
        if (!stopped) void connect();
      } catch (error) {
        if (sessionGone(error)) end();
        else if (!stopped) setFailed(true);
      }
    })();

    return () => {
      stopped = true;
      clearTimeout(retry);
      clearTimeout(begins);
      socket?.close();
      store.dispose();
    };
  }, [api, transport, setCsrf, store, navigate, run]);

  // Back from the background: the session may have ended, the socket may be dead.
  useEffect(() => {
    const visible = () => {
      if (document.visibilityState !== "visible") return;
      api.me().then(
        () => store.get().staleSince !== null && setRun((n) => n + 1),
        (error: unknown) => sessionGone(error) && navigate("/ended", { replace: true }),
      );
    };
    document.addEventListener("visibilitychange", visible);
    return () => document.removeEventListener("visibilitychange", visible);
  }, [api, store, navigate]);

  useEffect(() => {
    if (view.ended) navigate("/ended", { replace: true });
  }, [view.ended, navigate]);

  // The stale age in the subtitle counts up.
  useEffect(() => {
    if (view.staleSince === null) return;
    const tick = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(tick);
  }, [view.staleSince]);

  const onAction = (resourceId: string, action: Action, params: ActionParams) => {
    const key = idempotencyKey();
    const local = (status: "accepted" | "failed", id = key) => {
      const at = new Date().toISOString();
      store.addCommand({ id, resource_id: resourceId, action, status, created_at: at, updated_at: at });
    };
    // A timeout leaves open whether the gateway got the click: the same key makes the retry safe.
    const send = (retries: number): Promise<void> =>
      api.actions(resourceId, action, params, key).then(
        (answer) => local(answer.status === "failed" ? "failed" : "accepted", answer.command_id),
        (error: unknown) => {
          if (error instanceof DOMException && error.name === "TimeoutError" && retries > 0) return send(retries - 1);
          if (sessionGone(error)) navigate("/ended", { replace: true });
          else local("failed");
        },
      );
    void send(ACTION_RETRIES);
  };

  if (failed)
    return (
      <main className={styles.page}>
        <p className={styles.error}>{t("error.generic")}</p>
        <button type="button" className={styles.retry} onClick={() => setRun((n) => n + 1)}>
          {t("dashboard.retry")}
        </button>
      </main>
    );
  if (!me)
    return (
      <main className={styles.page}>
        <p className={styles.muted}>{t("dashboard.loading")}</p>
      </main>
    );

  const status = panelStatus(view);
  const contact = me.instance.operator_contact;
  let subtitle = me.instance.name;
  if (status === "waiting" && me.session.not_before) subtitle = t("status.waiting_until", { time: formatTime(me.session.not_before, lang) });
  else if (view.cleared) subtitle = t("notice.stale_cleared");
  else if (view.staleSince !== null) subtitle = t("status.stale_since", { seconds: Math.max(0, Math.round((now - view.staleSince) / 1000)) });
  else if (me.stay) subtitle = `${me.instance.name} · ${t("dashboard.access_ends", { time: formatTime(me.stay.ends_at, lang) })}`;

  return (
    <main>
      <GuestPanel
        title={me.stay?.unit_name ?? me.instance.name}
        subtitle={subtitle}
        rooms={view.rooms}
        status={status}
        commands={view.commands}
        footer={contact ? t("ended.contact", { contact }) : null}
        onAction={onAction}
      />
      {view.rooms.length === 0 && status !== "expired" && <p className={styles.empty}>{t("dashboard.empty")}</p>}
    </main>
  );
}
