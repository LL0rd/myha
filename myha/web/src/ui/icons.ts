import {
  IconAlertCircle,
  IconArrowBarDown,
  IconArrowBarUp,
  IconBulb,
  IconBulbOff,
  IconCircleCheck,
  IconDroplet,
  IconFlame,
  IconHelpCircle,
  IconInfoCircle,
  IconLoader2,
  IconMinus,
  IconPlayerStop,
  IconPlus,
  IconPower,
  IconSend,
  IconSnowflake,
  IconSunLow,
  IconTemperature,
  IconWindow,
  type Icon,
} from "@tabler/icons-react";
import type { CommandState, Kind } from "./types";

// Bundled SVG components only: the CSP allows no external icon URLs.
// shortcut: the binding's own icon name is ignored, every resource shows its kind icon; map names here once bindings pick icons.
export const kindIcon = {
  light: IconBulb,
  climate: IconTemperature,
  cover: IconWindow,
  sensor: IconDroplet,
} satisfies Record<Kind, unknown>;

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
