import { expect, it } from "vitest";
import css from "./tokens.css?raw";

// Text of the first rule opened by `selector {`, up to its closing brace.
function rule(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`tokens.css has no rule "${selector}"`);
  return css.slice(start + selector.length, css.indexOf("}", start));
}

it.each([":root", ':root[data-theme="dark"]', ':root[data-theme="contrast"]'])("%s defines --accent", (selector) => {
  expect(rule(selector)).toMatch(/--accent\s*:/);
});

it("system dark repeats the explicit dark tokens exactly", () => {
  const flat = (selector: string) => rule(selector).replace(/\s+/g, "");
  expect(flat(':root:not([data-theme="light"])')).toBe(flat(':root[data-theme="dark"]'));
});

it("dark and contrast set the same tokens, and :root defines all of them", () => {
  const names = (selector: string) => (rule(selector).match(/--[\w-]+(?=\s*:)/g) ?? []).sort();
  const dark = names(':root[data-theme="dark"]');
  expect(names(':root[data-theme="contrast"]')).toEqual(dark);
  expect(names(":root")).toEqual(expect.arrayContaining(dark));
});
