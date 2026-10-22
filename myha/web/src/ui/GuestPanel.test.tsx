import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { GuestPanel } from "./GuestPanel";
import type { Command, Resource, Room } from "./types";

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

it("sends the brightness once the slider is released", () => {
  const onAction = vi.fn();
  render(<GuestPanel title="Room 203" rooms={rooms()} status="online" onAction={onAction} />);
  const slider = screen.getByRole("slider", { name: "Brightness" });
  fireEvent.change(slider, { target: { value: "40" } });
  expect(onAction).not.toHaveBeenCalled();
  fireEvent.pointerUp(slider);
  expect(onAction).toHaveBeenCalledWith("res_light", "light.set_level", { level_pct: 40 });
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

it("announces the latest command of a resource politely", () => {
  const commands: Command[] = [
    { id: "c1", resource_id: "res_cover", action: "cover.open", status: "confirmed", updated_at: "2026-10-22T10:00:00Z" },
    { id: "c2", resource_id: "res_cover", action: "cover.close", status: "sent", updated_at: "2026-10-22T10:01:00Z" },
  ];
  render(<GuestPanel title="Room 203" rooms={rooms()} status="online" commands={commands} onAction={() => {}} />);
  const live = card("Blind").getByText(/waiting for the device/).closest("[aria-live]");
  expect(live?.getAttribute("aria-live")).toBe("polite");
  expect(card("Blind").queryByText(/Confirmed/)).toBeNull();
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
