import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ThemeProvider, useTheme } from "./ThemeProvider";
import { ThemeSwitch } from "./ThemeSwitch";

function prefersDark(dark: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: dark && query === "(prefers-color-scheme: dark)" }));
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("follows the system preference while nothing is stored", () => {
  prefersDark(true);
  expect(renderHook(() => useTheme()).result.current.theme).toBe("dark");
  prefersDark(false);
  expect(renderHook(() => useTheme()).result.current.theme).toBe("light");
  // The CSS keeps following the system live, so nothing is pinned on <html>.
  expect(document.documentElement.dataset.theme).toBeUndefined();
});

it("a stored choice beats the system preference", () => {
  prefersDark(true);
  localStorage.setItem("myha.theme", "contrast");
  expect(renderHook(() => useTheme()).result.current.theme).toBe("contrast");
  expect(document.documentElement.dataset.theme).toBe("contrast");
});

it("the switch applies and persists the choice for everyone under the provider", () => {
  prefersDark(false);
  const Shown = () => <output>{useTheme().theme}</output>;
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

it("works when storage is blocked", () => {
  prefersDark(false);
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  const { result } = renderHook(() => useTheme());
  expect(result.current.theme).toBe("light");
  act(() => result.current.setTheme("contrast"));
  expect(result.current.theme).toBe("contrast");
});
