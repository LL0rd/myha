import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ThemeProvider, useTheme } from "./ThemeProvider";
import { ThemeSwitch } from "./ThemeSwitch";

// A fake prefers-color-scheme query whose answer the test can flip.
function prefersDark(dark: boolean) {
  const listeners = new Set<() => void>();
  const mq = {
    matches: dark,
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  };
  vi.stubGlobal("matchMedia", () => mq);
  return (next: boolean) => {
    mq.matches = next;
    listeners.forEach((fn) => fn());
  };
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("follows the system preference live while nothing is stored", () => {
  const flip = prefersDark(true);
  const { result } = renderHook(() => useTheme());
  expect(result.current.theme).toBe("system");
  expect(result.current.resolved).toBe("dark");
  act(() => flip(false));
  expect(result.current.resolved).toBe("light");
  // The CSS follows the system by itself, so nothing is pinned on <html>.
  expect(document.documentElement.dataset.theme).toBeUndefined();
});

it("a stored choice beats the system preference", () => {
  prefersDark(true);
  localStorage.setItem("myha.theme", "contrast");
  const { result } = renderHook(() => useTheme());
  expect(result.current.theme).toBe("contrast");
  expect(result.current.resolved).toBe("contrast");
  expect(document.documentElement.dataset.theme).toBe("contrast");
});

it("the switch applies and persists the choice for everyone under the provider", () => {
  prefersDark(false);
  const Shown = () => <output>{useTheme().resolved}</output>;
  render(
    <ThemeProvider>
      <ThemeSwitch />
      <Shown />
    </ThemeProvider>,
  );
  act(() => screen.getByRole("button", { name: "dark" }).click());
  expect(screen.getByRole("status").textContent).toBe("dark");
  expect(screen.getByRole("button", { name: "dark" }).getAttribute("aria-pressed")).toBe("true");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem("myha.theme")).toBe("dark");
});

it("choosing system clears the stored choice and data-theme", () => {
  const flip = prefersDark(false);
  localStorage.setItem("myha.theme", "dark");
  const { result } = renderHook(() => useTheme());
  act(() => result.current.setTheme("system"));
  expect(localStorage.getItem("myha.theme")).toBeNull();
  expect(document.documentElement.dataset.theme).toBeUndefined();
  expect(result.current.theme).toBe("system");
  expect(result.current.resolved).toBe("light");
  act(() => flip(true));
  expect(result.current.resolved).toBe("dark");
});

it("works when storage is blocked", () => {
  prefersDark(false);
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  const { result } = renderHook(() => useTheme());
  expect(result.current.resolved).toBe("light");
  act(() => result.current.setTheme("contrast"));
  expect(result.current.resolved).toBe("contrast");
  act(() => result.current.setTheme("system"));
  expect(result.current.theme).toBe("system");
});
