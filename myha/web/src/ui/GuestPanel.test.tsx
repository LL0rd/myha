import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { GuestPanel } from "./GuestPanel";
import { stepTarget } from "./ResourceCard";
import type { Command, Interval, Resource, Room } from "./types";

afterEach(cleanup);

const online = { availability: "online" as const, observed_at: "2026-10-22T10:00:00Z" };

const light: Resource = {
  id: "res_light",
  kind: "light",
  label: "Ceiling light",
  state: { ...online, power: "on", level_pct: 60 },
  capabilities: {
    "light.on": {},
    "light.off": {},
    "light.set_level": { intervals: [{ min: "10", max: "100", step: "1", origin: "0" }] },
  },
  rules: [{ action: "light.set_level", status: "fence", text: "capped at 100 % by the stay" }],
};
const readOnlyLight: Resource = { id: "res_bedside", kind: "light", label: "Bedside", state: { ...online, power: "off" } };
const climate = (target_c: string): Resource => ({
  id: "res_climate",
  kind: "climate",
  label: "Air conditioning",
  state: { ...online, current_c: "23.4", target_c, hvac_mode: "heat" },
  capabilities: {
    "climate.set_temperature": { intervals: [{ min: "18.0", max: "22.0", step: "0.5", origin: "0" }] },
    "climate.set_mode": { allowed: ["off", "heat"] },
  },
});
const cover: Resource = {
  id: "res_cover",
  kind: "cover",
  label: "Blind",
  state: { ...online, position_pct: 30 },
  capabilities: { "cover.open": {}, "cover.close": {} },
};
const sensor: Resource = { id: "res_hum", kind: "sensor", label: "Humidity", state: { ...online, value: "48", unit: "%" } };

const rooms = (target = "21.5"): Room[] => [
  { id: "unit_203", name: "Room 203", resources: [light, readOnlyLight, climate(target), cover, sensor] },
];

function card(label: string) {
  return within(screen.getByRole("group", { name: label }));
}

it("offers only the buttons the capabilities allow", () => {
  render(<GuestPanel title="Room 203" rooms={rooms()} status="online" onAction={() => {}} />);
  expect(card("Ceiling light").getByRole("button", { name: "Turn off" })).toBeTruthy();
  expect(card("Ceiling light").queryByRole("button", { name: "Turn on" })).toBeNull();
  expect(card("Bedside").queryAllByRole("button")).toHaveLength(0);
  expect(card("Blind").getByRole("button", { name: "Open" })).toBeTruthy();
  expect(card("Blind").getByRole("button", { name: "Close" })).toBeTruthy();
  expect(card("Blind").queryByRole("button", { name: "Stop" })).toBeNull();
  expect(card("Air conditioning").getByRole("button", { name: "Heat" }).getAttribute("aria-pressed")).toBe("true");
  expect(card("Air conditioning").queryByRole("button", { name: "Cool" })).toBeNull();
  expect(card("Humidity").getByText("48 %")).toBeTruthy();
});

it("a switch toggles like a light without a dimmer", async () => {
  const onAction = vi.fn();
  const fan: Resource = {
    id: "res_fan",
    kind: "switch",
    label: "Fan",
    state: { ...online, power: "off" },
    capabilities: { "switch.on": {}, "switch.off": {} },
  };
  render(<GuestPanel title="Room 203" rooms={[{ id: "u", name: "Room", resources: [fan] }]} status="online" onAction={onAction} />);
  expect(card("Fan").getByText("Off")).toBeTruthy();
  expect(card("Fan").queryByRole("slider")).toBeNull();
  await userEvent.click(card("Fan").getByRole("button", { name: "Turn on" }));
  expect(onAction).toHaveBeenCalledWith("res_fan", "switch.on", {});
});

it("disables climate + at the maximum of the effective range", () => {
  render(<GuestPanel title="Room 203" rooms={rooms("22.0")} status="online" onAction={() => {}} />);
  expect((screen.getByRole("button", { name: "Raise target" }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "Lower target" }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByText("Allowed 18–22 °C · 0.5° steps")).toBeTruthy();
});

it("sends the next step as a decimal string and shows it right away", async () => {
  const onAction = vi.fn();
  render(<GuestPanel title="Room 203" rooms={rooms()} status="online" onAction={onAction} />);
  await userEvent.click(screen.getByRole("button", { name: "Raise target" }));
  expect(onAction).toHaveBeenCalledWith("res_climate", "climate.set_temperature", { target_c: "22.0" });
  expect((screen.getByRole("button", { name: "Raise target" }) as HTMLButtonElement).disabled).toBe(true);
});

it("sends the brightness once the slider gesture ends", () => {
  const onAction = vi.fn();
  render(<GuestPanel title="Room 203" rooms={rooms()} status="online" onAction={onAction} />);
  const slider = screen.getByRole("slider", { name: "Brightness" }) as HTMLInputElement;
  fireEvent.input(slider, { target: { value: "40" } });
  expect(onAction).not.toHaveBeenCalled();
  fireEvent(slider, new Event("change", { bubbles: true }));
  expect(onAction).toHaveBeenCalledTimes(1);
  expect(onAction).toHaveBeenCalledWith("res_light", "light.set_level", { level_pct: 40 });
  expect(slider.value).toBe("40");
});

it("keyboard and cancelled touch gestures send only on change", () => {
  const onAction = vi.fn();
  render(<GuestPanel title="Room 203" rooms={rooms()} status="online" onAction={onAction} />);
  const slider = screen.getByRole("slider", { name: "Brightness" }) as HTMLInputElement;
  fireEvent.keyDown(slider, { key: "ArrowRight" });
  fireEvent.input(slider, { target: { value: "61" } });
  fireEvent.keyUp(slider, { key: "ArrowRight" });
  expect(onAction).not.toHaveBeenCalled();
  fireEvent(slider, new Event("change", { bubbles: true }));
  expect(onAction).toHaveBeenLastCalledWith("res_light", "light.set_level", { level_pct: 61 });
  fireEvent.pointerDown(slider);
  fireEvent.input(slider, { target: { value: "30" } });
  fireEvent.pointerCancel(slider);
  expect(onAction).toHaveBeenCalledTimes(1);
  fireEvent(slider, new Event("change", { bubbles: true }));
  expect(onAction).toHaveBeenCalledTimes(2);
  expect(onAction).toHaveBeenLastCalledWith("res_light", "light.set_level", { level_pct: 30 });
});

it("a simulated brightness change snaps back", () => {
  const onAction = vi.fn();
  render(<GuestPanel title="Room 203" rooms={rooms()} status="online" simulate onAction={onAction} />);
  const slider = screen.getByRole("slider", { name: "Brightness" }) as HTMLInputElement;
  fireEvent.input(slider, { target: { value: "40" } });
  fireEvent(slider, new Event("change", { bubbles: true }));
  expect(onAction).toHaveBeenCalledWith("res_light", "light.set_level", { level_pct: 40 });
  expect(slider.value).toBe("60");
});

it("keeps the chosen target while intermediate values arrive", async () => {
  const onAction = vi.fn();
  const view = (target: string) => <GuestPanel title="Room 203" rooms={rooms(target)} status="online" onAction={onAction} />;
  const { rerender } = render(view("20.0"));
  const raise = screen.getByRole("button", { name: "Raise target" });
  await userEvent.click(raise);
  await userEvent.click(raise);
  rerender(view("20.5")); // the first step arrives while the second is under way
  expect(card("Air conditioning").getByText("21")).toBeTruthy();
  await userEvent.click(raise);
  expect(onAction).toHaveBeenLastCalledWith("res_climate", "climate.set_temperature", { target_c: "21.5" });
  rerender(view("21.5"));
  expect(card("Air conditioning").getByText("21.5")).toBeTruthy();
});

it("drops the chosen target when the command ends without success", async () => {
  const sent = (status: Command["status"]): Command[] => [
    { id: "c1", resource_id: "res_climate", action: "climate.set_temperature", status, created_at: "2026-10-22T10:00:00Z", updated_at: "2026-10-22T10:00:01Z" },
  ];
  const view = (commands: Command[]) => <GuestPanel title="Room 203" rooms={rooms("20.0")} status="online" commands={commands} onAction={() => {}} />;
  const { rerender } = render(view([]));
  await userEvent.click(screen.getByRole("button", { name: "Raise target" }));
  rerender(view(sent("sent")));
  expect(card("Air conditioning").getByText("20.5")).toBeTruthy();
  rerender(view(sent("failed")));
  expect(card("Air conditioning").getByText("20")).toBeTruthy();
});

it("expired disables every control", () => {
  render(<GuestPanel title="Room 203" rooms={rooms()} status="expired" onAction={() => {}} />);
  expect(screen.getByText("Access ended")).toBeTruthy();
  const controls = [...screen.getAllByRole("button"), screen.getByRole("slider")] as HTMLButtonElement[];
  expect(controls.length).toBeGreaterThan(5);
  expect(controls.every((c) => c.disabled)).toBe(true);
});

it("explain shows the rules", () => {
  const { rerender } = render(<GuestPanel title="Room 203" rooms={rooms()} status="online" onAction={() => {}} />);
  expect(screen.queryByText("capped at 100 % by the stay")).toBeNull();
  rerender(<GuestPanel title="Room 203" rooms={rooms()} status="online" explain onAction={() => {}} />);
  expect(card("Ceiling light").getByText("capped at 100 % by the stay")).toBeTruthy();
});

it.each<[string, Interval[], string | null, 1 | -1, string | null]>([
  ["origin offset", [{ min: "18.25", max: "22.25", step: "0.5", origin: "0.25" }], "20.25", 1, "20.75"],
  ["gap upwards", [{ min: "18.0", max: "20.0", step: "0.5", origin: "0" }, { min: "22.0", max: "24.0", step: "0.5", origin: "0" }], "20.0", 1, "22.0"],
  ["gap downwards", [{ min: "18.0", max: "20.0", step: "0.5", origin: "0" }, { min: "22.0", max: "24.0", step: "0.5", origin: "0" }], "22.0", -1, "20.0"],
  ["below min, up", [{ min: "18.0", max: "24.0", step: "0.5", origin: "0" }], "15.0", 1, "18.0"],
  ["below min, down", [{ min: "18.0", max: "24.0", step: "0.5", origin: "0" }], "15.0", -1, null],
  ["above max, down", [{ min: "18.0", max: "24.0", step: "0.5", origin: "0" }], "30.0", -1, "24.0"],
  ["above max, up", [{ min: "18.0", max: "24.0", step: "0.5", origin: "0" }], "30.0", 1, null],
  ["unknown, up", [{ min: "18.0", max: "24.0", step: "0.5", origin: "0" }], null, 1, "18.0"],
  ["unknown, down", [{ min: "18.0", max: "24.0", step: "0.5", origin: "0" }], null, -1, "24.0"],
  ["0.1 steps", [{ min: "18.0", max: "19.0", step: "0.1", origin: "0" }], "18.7", 1, "18.8"],
  ["off-grid bounds, up", [{ min: "18.2", max: "21.9", step: "0.5", origin: "0" }], null, 1, "18.5"],
  ["off-grid bounds, down", [{ min: "18.2", max: "21.9", step: "0.5", origin: "0" }], null, -1, "21.5"],
  ["two-digit bounds", [{ min: "18.25", max: "19.0", step: "1", origin: "0" }], null, 1, "19.00"],
  ["step 0", [{ min: "18.0", max: "24.0", step: "0", origin: "0" }], "20.0", 1, null],
])("stepTarget: %s", (_, intervals, current, dir, expected) => {
  expect(stepTarget(intervals, current, dir)).toBe(expected);
});

it("announces the latest command of a resource politely", () => {
  const commands: Command[] = [
    { id: "c1", resource_id: "res_cover", action: "cover.open", status: "confirmed", created_at: "2026-10-22T10:00:00Z", updated_at: "2026-10-22T10:00:01Z" },
    { id: "c2", resource_id: "res_cover", action: "cover.close", status: "sent", created_at: "2026-10-22T10:01:00Z", updated_at: "2026-10-22T10:01:00.5Z" },
  ];
  render(<GuestPanel title="Room 203" rooms={rooms()} status="online" commands={commands} onAction={() => {}} />);
  const live = card("Blind").getByText(/waiting for the device/).closest("[aria-live]");
  expect(live?.getAttribute("aria-live")).toBe("polite");
  expect(live?.textContent).toMatch(/^Blind/);
  expect(card("Blind").queryByText(/Confirmed/)).toBeNull();
});

it("an older command timing out does not hide a newer one", () => {
  const commands: Command[] = [
    // The older command changed last; mixed fraction digits as pydantic writes them.
    { id: "new", resource_id: "res_cover", action: "cover.open", status: "confirmed", created_at: "2026-10-22T10:00:05.25Z", updated_at: "2026-10-22T10:00:06Z" },
    { id: "old", resource_id: "res_cover", action: "cover.close", status: "unknown", created_at: "2026-10-22T10:00:02Z", updated_at: "2026-10-22T10:00:12Z" },
  ];
  render(<GuestPanel title="Room 203" rooms={rooms()} status="online" commands={commands} onAction={() => {}} />);
  expect(card("Blind").getByText("Confirmed")).toBeTruthy();
  expect(card("Blind").queryByText(/Result unclear/)).toBeNull();
});

it("simulation shows denied actions and keeps the shown target", async () => {
  const onAction = vi.fn();
  render(<GuestPanel title="Room 203" rooms={rooms()} status="online" simulate onAction={onAction} />);
  expect(card("Blind").getByRole("button", { name: "Stop" })).toBeTruthy();
  expect(card("Bedside").getByRole("button", { name: "Turn on" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Raise target" }));
  expect(onAction).toHaveBeenCalledWith("res_climate", "climate.set_temperature", { target_c: "22.0" });
  expect(card("Air conditioning").getByText("21.5")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Raise target" }) as HTMLButtonElement).disabled).toBe(false);
});

it("shows the binding icon and falls back to the kind icon", () => {
  const lamp: Resource = { ...readOnlyLight, icon: "tabler:lamp" };
  const odd: Resource = { ...sensor, icon: "no-such-icon" };
  render(<GuestPanel title="Room 203" rooms={[{ id: "u", name: "Room", resources: [lamp, odd] }]} status="online" onAction={() => {}} />);
  expect(screen.getByRole("group", { name: "Bedside" }).querySelector("svg")?.getAttribute("class")).toContain("tabler-icon-lamp");
  expect(screen.getByRole("group", { name: "Humidity" }).querySelector("svg")?.getAttribute("class")).toContain("tabler-icon-droplet");
});
