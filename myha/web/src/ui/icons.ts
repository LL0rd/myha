import {
  IconAirConditioning,
  IconAlertCircle,
  IconArrowBarDown,
  IconArrowBarUp,
  IconBulb,
  IconBulbOff,
  IconCircleCheck,
  IconColumns2,
  IconDoor,
  IconDroplet,
  IconDropletHalf2,
  IconGauge,
  IconFlame,
  IconHelpCircle,
  IconInfoCircle,
  IconLamp,
  IconLamp2,
  IconLayoutRows,
  IconLoader2,
  IconMinus,
  IconPlayerStop,
  IconPlug,
  IconPlus,
  IconPower,
  IconSend,
  IconSnowflake,
  IconSunLow,
  IconTemperature,
  IconThermometer,
  IconWindow,
  type Icon,
} from "@tabler/icons-react";
import type { CommandState, Kind } from "./types";

// Bundled SVG components only: the CSP allows no external icon URLs.
export const kindIcon = {
  light: IconBulb,
  climate: IconTemperature,
  cover: IconWindow,
  sensor: IconDroplet,
} satisfies Record<Kind, unknown>;

// The icon names a binding may carry (the admin UI offers exactly these); an optional "tabler:" prefix is accepted.
export const namedIcon: Record<string, Icon> = {
  bulb: IconBulb,
  lamp: IconLamp,
  "floor-lamp": IconLamp2,
  temperature: IconTemperature,
  thermometer: IconThermometer,
  "air-conditioning": IconAirConditioning,
  window: IconWindow,
  blind: IconLayoutRows,
  curtain: IconColumns2,
  door: IconDoor,
  droplet: IconDroplet,
  humidity: IconDropletHalf2,
  gauge: IconGauge,
  power: IconPower,
  plug: IconPlug,
};

/** The binding's icon if it is one of namedIcon, else the icon of the kind. */
export function resourceIcon(kind: Kind, name?: string | null): Icon {
  const key = (name ?? "").replace(/^tabler:/, "");
  return Object.prototype.hasOwnProperty.call(namedIcon, key) ? (namedIcon[key] as Icon) : kindIcon[kind];
}

export const actionIcon = {
  "light.on": IconBulb,
  "light.off": IconBulbOff,
  "cover.open": IconArrowBarUp,
  "cover.stop": IconPlayerStop,
  "cover.close": IconArrowBarDown,
  lower: IconMinus,
  raise: IconPlus,
  level: IconSunLow,
};

export const modeIcon: Record<string, Icon> = { off: IconPower, cool: IconSnowflake, heat: IconFlame };

export const commandIcon = {
  accepted: IconLoader2,
  sent: IconSend,
  confirmed: IconCircleCheck,
  unknown: IconHelpCircle,
  failed: IconAlertCircle,
} satisfies Record<CommandState, unknown>;

export const noticeIcon = IconInfoCircle;
