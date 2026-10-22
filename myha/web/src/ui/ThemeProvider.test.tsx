import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// The theme is page-wide module state: every test loads a fresh copy.
async function load() {
  vi.resetModules();
  return { ...(await import("./ThemeProvider")), ...(await import("./ThemeSwitch")) };
}

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

function blockStorage() {
  for (const method of ["getItem", "setItem", "removeItem"] as const)
    vi.spyOn(Storage.prototype, method).mockImplementation(() => {
      throw new Error("blocked");
    });
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("follows the system preference live while nothing is stored", async () => {
  const flip = prefersDark(true);
  const { useTheme } = await load();
  const { result } = renderHook(() => useTheme());
  expect(result.current.theme).toBe("system");
  expect(result.current.resolved).toBe("dark");
  act(() => flip(false));
  expect(result.current.resolved).toBe("light");
  // The CSS follows the system by itself, so nothing is pinned on <html>.
  expect(document.documentElement.dataset.theme).toBeUndefined();
});

it("initTheme applies the stored choice before anything renders", async () => {
  prefersDark(false);
  localStorage.setItem("myha.theme", "contrast");
  const { initTheme } = await load();
  expect(document.documentElement.dataset.theme).toBeUndefined();
  expect(initTheme()).toBe("contrast");
  expect(document.documentElement.dataset.theme).toBe("contrast");
});

it("a stored choice beats the system preference", async () => {
  prefersDark(true);
  localStorage.setItem("myha.theme", "contrast");
  const { useTheme } = await load();
  const { result } = renderHook(() => useTheme());
  expect(result.current.theme).toBe("contrast");
  expect(result.current.resolved).toBe("contrast");
  expect(document.documentElement.dataset.theme).toBe("contrast");
});

it("the switch applies and persists the choice for every user", async () => {
  prefersDark(false);
  const { useTheme, ThemeSwitch } = await load();
  const Shown = () => <output>{useTheme().resolved}</output>;
  render(
    <>
      <ThemeSwitch />
      <Shown />
    </>,
  );
  act(() => screen.getByRole("button", { name: "Dark" }).click());
  expect(screen.getByRole("status").textContent).toBe("dark");
  expect(screen.getByRole("button", { name: "Dark" }).getAttribute("aria-pressed")).toBe("true");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem("myha.theme")).toBe("dark");
});

it("choosing system clears the stored choice and data-theme", async () => {
  const flip = prefersDark(false);
  localStorage.setItem("myha.theme", "dark");
  const { useTheme } = await load();
  const { result } = renderHook(() => useTheme());
  act(() => result.current.setTheme("system"));
  expect(localStorage.getItem("myha.theme")).toBeNull();
  expect(document.documentElement.dataset.theme).toBeUndefined();
  expect(result.current.theme).toBe("system");
  expect(result.current.resolved).toBe("light");
  act(() => flip(true));
  expect(result.current.resolved).toBe("dark");
});

it("a component mounted later keeps the choice, even with storage blocked", async () => {
  prefersDark(false);
  blockStorage();
  const { useTheme } = await load();
  const first = renderHook(() => useTheme());
  act(() => first.result.current.setTheme("contrast"));
  const late = renderHook(() => useTheme());
  expect(late.result.current.theme).toBe("contrast");
  expect(first.result.current.theme).toBe("contrast");
  expect(document.documentElement.dataset.theme).toBe("contrast");
  act(() => late.result.current.setTheme("system"));
  expect(first.result.current.theme).toBe("system");
});
