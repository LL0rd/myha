import type { Icon } from "@tabler/icons-react";
import { useRef, useState } from "react";
import { CommandStatus } from "./CommandStatus";
import type { Strings } from "./GuestPanel";
import styles from "./guest-panel.module.css";
import { actionIcon, kindIcon, modeIcon } from "./icons";
import type { Action, ActionParams, Command, Interval, Resource } from "./types";

interface Props {
  resource: Resource;
  command?: Command;
  live: boolean;
  simulate: boolean;
  explain: boolean;
  t: Strings;
  onAction: (resourceId: string, action: Action, params: ActionParams) => void;
}

const decimals = (s: string) => (s.split(".")[1] ?? "").length;
const trimZero = (s: string) => s.replace(/\.0+$/, "");

/** Next allowed value above (dir 1) or below (dir -1) `current` across all intervals, as a decimal string; null if none. */
export function stepTarget(intervals: Interval[], current: string | null | undefined, dir: 1 | -1): string | null {
  // Scaled integers, so 0.1 steps never drift; at least one decimal, as the API expects "22.0".
  const digits = Math.max(1, ...intervals.map((i) => Math.max(decimals(i.step), decimals(i.origin))));
  const scale = 10 ** digits;
  const n = (s: string) => Math.round(Number(s) * scale);
  const cur = current == null ? null : n(current);
  let best: number | null = null;
  for (const i of intervals) {
    const [lo, hi, step, origin] = [n(i.min), n(i.max), n(i.step), n(i.origin)];
    const v =
      dir > 0
        ? cur === null || cur < lo ? lo : origin + (Math.floor((cur - origin) / step) + 1) * step
        : cur === null || cur > hi ? hi : origin + (Math.ceil((cur - origin) / step) - 1) * step;
    if (v >= lo && v <= hi && (best === null || (dir > 0 ? v < best : v > best))) best = v;
  }
  return best === null ? null : (best / scale).toFixed(digits);
}

/** A locally chosen value shown until the device reports a new one or the command fails. */
function usePending<T>(observed: T, command: Command | undefined) {
  const [pending, setPending] = useState<T | null>(null);
  const settled = command && (command.status === "failed" || command.status === "unknown") ? command.id : "";
  const [seen, setSeen] = useState({ observed, settled });
  if (seen.observed !== observed || seen.settled !== settled) {
    setSeen({ observed, settled });
    if (seen.observed !== observed || settled) setPending(null);
  }
  return [pending, setPending] as const;
}

export function ResourceCard({ resource, command, live, simulate, explain, t, onAction }: Props) {
  const { id, kind, label, state } = resource;
  const caps = resource.capabilities ?? {};
  const can = (a: Action) => caps[a] !== undefined;
  const send = (action: Action, params: ActionParams = {}) => onAction(id, action, params);
  const [target, setTarget] = usePending(state?.target_c ?? null, command);
  const [level, setLevel] = usePending(state?.level_pct ?? null, command);
  const dragged = useRef(false);

  const button = (action: Action, text: string, Icon: Icon, variant: "primary" | "secondary", params?: ActionParams, pressed?: boolean) =>
    can(action) || simulate ? (
      <button
        key={action + (params?.mode ?? "")}
        type="button"
        className={styles.btn}
        data-variant={can(action) ? variant : "denied"}
        aria-pressed={pressed}
        disabled={!live}
        onClick={() => send(action, params)}
      >
        <Icon size={20} aria-hidden="true" />
        {text}
      </button>
    ) : null;

  let stateText: string;
  let buttons: (JSX.Element | null)[] = [];
  let climate: JSX.Element | null = null;
  let slider: JSX.Element | null = null;

  if (!state) stateText = t.notShared;
  else if (state.availability === "offline") stateText = t.unavailable;
  else stateText = t.unknown;

  if (kind === "light") {
    const power = state?.power;
    if (state && state.availability !== "offline")
      stateText = power === "on" ? (state.level_pct != null ? t.onLevel(state.level_pct) : t.on) : power === "off" ? t.off : t.unknown;
    const on = button("light.on", t.turnOn, actionIcon["light.on"], "primary");
    const off = button("light.off", t.turnOff, actionIcon["light.off"], "secondary");
    buttons = can("light.on") && can("light.off") && (power === "on" || power === "off") ? [power === "on" ? off : on] : [on, off];
    const intervals = caps["light.set_level"]?.intervals;
    if (intervals?.length) {
      const min = Math.min(...intervals.map((i) => Number(i.min)));
      const max = Math.max(...intervals.map((i) => Number(i.max)));
      const value = level ?? state?.level_pct ?? min;
      const release = (e: { currentTarget: HTMLInputElement }) => {
        if (!dragged.current) return;
        dragged.current = false;
        send("light.set_level", { level_pct: Number(e.currentTarget.value) });
      };
      // shortcut: one slider over the hull of all intervals with the first step; gaps between intervals are left to the server check.
      slider = (
        <div className={styles.slider}>
          <actionIcon.level size={20} aria-hidden="true" className={styles.muted} />
          <input
            type="range"
            min={min}
            max={max}
            step={Number(intervals[0]?.step) || 1}
            value={value}
            aria-label={t.brightness}
            disabled={!live}
            onChange={(e) => {
              dragged.current = true;
              setLevel(Number(e.currentTarget.value));
            }}
            onPointerUp={release}
            onKeyUp={release}
          />
          <span className={styles.level}>{value} %</span>
        </div>
      );
    }
  } else if (kind === "climate") {
    if (state?.current_c != null && state.availability !== "offline") stateText = t.now(trimZero(state.current_c));
    const temp = caps["climate.set_temperature"];
    if (temp || simulate) {
      const intervals = temp?.intervals ?? [];
      const value = target ?? state?.target_c ?? null;
      const lower = stepTarget(intervals, value, -1);
      const raise = stepTarget(intervals, value, 1);
      const set = (v: string) => {
        setTarget(v);
        send("climate.set_temperature", { target_c: v });
      };
      const ranges = intervals.map((i) => `${trimZero(i.min)}–${trimZero(i.max)}`).join(", ");
      const steps = Array.from(new Set(intervals.map((i) => trimZero(i.step)))).join("/");
      climate = (
        <div className={styles.climate}>
          <div className={styles.stepper}>
            <button type="button" className={styles.step} aria-label={t.lower} disabled={!live || lower === null} onClick={() => lower && set(lower)}>
              <actionIcon.lower size={24} aria-hidden="true" />
            </button>
            <div className={styles.target}>
              <span className={styles.temp}>{value == null ? "–" : trimZero(value)}</span>
              <span className={styles.muted}>{t.celsius}</span>
            </div>
            <button type="button" className={styles.step} data-variant="primary" aria-label={t.raise} disabled={!live || raise === null} onClick={() => raise && set(raise)}>
              <actionIcon.raise size={24} aria-hidden="true" />
            </button>
          </div>
          <div className={styles.range}>{temp ? t.range(ranges, steps) : t.readOnlyTemp}</div>
        </div>
      );
    }
    buttons = (caps["climate.set_mode"]?.allowed ?? []).map((mode) =>
      button("climate.set_mode", t.mode[mode] ?? mode, modeIcon[mode] ?? kindIcon.climate, "secondary", { mode }, state?.hvac_mode === mode),
    );
  } else if (kind === "cover") {
    if (state?.position_pct != null && state.availability !== "offline") stateText = t.openPct(state.position_pct);
    buttons = [
      button("cover.open", t.open, actionIcon["cover.open"], "primary"),
      button("cover.stop", t.stop, actionIcon["cover.stop"], "secondary"),
      button("cover.close", t.close, actionIcon["cover.close"], "secondary"),
    ];
  } else if (state?.value != null && state.availability !== "offline") {
    stateText = `${state.value} ${state.unit ?? ""}`.trim();
  }

  buttons = buttons.filter(Boolean);
  const KindIcon = kindIcon[kind];
  return (
    <div role="group" aria-label={label} className={styles.card} data-kind={kind}>
      <div className={styles.head}>
        <div className={styles.iconBox}>
          <KindIcon className={styles.kindIcon} aria-hidden="true" />
        </div>
        <div className={styles.meta}>
          <div className={styles.label}>{label}</div>
          <div className={styles.state} data-hidden={!state || undefined}>
            {stateText}
          </div>
        </div>
      </div>
      {(climate || buttons.length > 0) && (
        <div className={styles.controls}>
          {climate}
          {buttons.length > 0 && <div className={styles.buttons}>{buttons}</div>}
        </div>
      )}
      {slider}
      <div className={styles.live} role="status" aria-live="polite">
        {command && <CommandStatus status={command.status} text={t.command[command.status]} />}
      </div>
      {explain && resource.rules?.length ? (
        <ul className={styles.rules}>
          {resource.rules.map((r) => (
            <li key={r.action + r.text} className={styles.rule}>
              <span className={styles.ruleDot} data-status={r.status} aria-hidden="true" />
              <span>
                <span className={styles.ruleAction}>{r.action}</span> <span className={styles.muted}>{r.text}</span>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
