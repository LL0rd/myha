// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import de from "./de.json";
import en from "./en.json";
import { themes } from "../ui/ThemeProvider";
import { detectLang, setLang, translate, useI18n, type Key } from "./index";

// The language is page-wide state: every test starts in English with empty storage.
afterEach(() => {
  vi.restoreAllMocks();
  setLang("en");
  localStorage.clear();
});

const placeholders = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();

it("de and en have the same keys and the same placeholders", () => {
  expect(Object.keys(de).sort()).toEqual(Object.keys(en).sort());
  for (const [key, text] of Object.entries(en)) expect(placeholders(de[key as keyof typeof de]), key).toEqual(placeholders(text));
});

it("covers the eight public error codes and the five command states", () => {
  const codes = ["UNAUTHENTICATED", "NOT_FOUND", "ACTION_NOT_ALLOWED", "INVALID_PARAMETERS", "ACCESS_EXPIRED", "UPSTREAM_UNAVAILABLE", "RATE_LIMITED", "CONFLICT"];
  const states = ["accepted", "sent", "confirmed", "unknown", "failed"];
  expect(Object.keys(en)).toEqual(expect.arrayContaining([...codes.map((c) => `error.${c}`), ...states.map((s) => `command.${s}`)]));
});

it("fills placeholders and falls back to the key", () => {
  expect(translate("en", "state.on_level", { pct: 40 })).toBe("On · 40 %");
  expect(translate("de", "state.on_level", { pct: 40 })).toBe("An · 40 %");
  expect(translate("en", "no.such.key" as Key)).toBe("no.such.key");
  expect(translate("en", "toString" as Key)).toBe("toString");
  expect(translate("en", "state.on_level")).toBe("On · {pct} %");
});

it("has a label for every theme", () => {
  expect(Object.keys(en)).toEqual(expect.arrayContaining(["theme.label", ...themes.map((t) => `theme.${t}`)]));
});

it("detects the stored language first, then the browser language, then English", () => {
  const browser = vi.spyOn(navigator, "language", "get");
  browser.mockReturnValue("de-AT");
  expect(detectLang()).toBe("de");
  localStorage.setItem("myha.lang", "en");
  expect(detectLang()).toBe("en");
  localStorage.setItem("myha.lang", "fr");
  expect(detectLang()).toBe("de");
  localStorage.clear();
  browser.mockReturnValue("fr-FR");
  expect(detectLang()).toBe("en");
});

it("setLang switches every user and persists the choice", () => {
  const a = renderHook(() => useI18n());
  const b = renderHook(() => useI18n());
  act(() => a.result.current.setLang("de"));
  expect(b.result.current.lang).toBe("de");
  expect(b.result.current.t("action.turn_on")).toBe("Einschalten");
  expect(localStorage.getItem("myha.lang")).toBe("de");
  expect(document.documentElement.lang).toBe("de");
});

it("works when storage is blocked", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(navigator, "language", "get").mockReturnValue("de-DE");
  expect(detectLang()).toBe("de");
  const { result } = renderHook(() => useI18n());
  act(() => result.current.setLang("en"));
  expect(result.current.lang).toBe("en");
});
