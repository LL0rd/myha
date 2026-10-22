#!/usr/bin/env node
// Fails when a built bundle would load anything from another host.
// Usage: node scripts/check-no-external.mjs myha/web/dist/guest
// Only load contexts count; plain text such as React's error-decoder URL or an SVG namespace loads nothing and
// passes. Self-test: node --test scripts/check-no-external.test.mjs
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { pathToFileURL } from "node:url";

const TARGET = String.raw`["'\x60]?\s*((?:https?:|wss?:)?//[^\s"'\x60)<>,]+)`;
const CONTEXTS = [
  String.raw`(?<![\w:-])(?:src|href|action|poster)\s*=\s*`, // HTML attributes
  String.raw`(?<![\w$.-])(?:src|href|action|poster)\s*:\s*`, // JSX props in the bundle: {src:"…"}
  String.raw`"(?:src|start_url|scope|url)"\s*:\s*`, // manifest and other JSON
  String.raw`url\(\s*`,
  String.raw`@import\s+`,
  String.raw`\bimport\(\s*`,
  String.raw`\bimportScripts\(\s*`,
  String.raw`\bfetch\(\s*`,
  String.raw`\bsendBeacon\(\s*`,
  String.raw`\bnew\s+(?:WebSocket|EventSource|Worker|SharedWorker)\(\s*`,
].map((context) => new RegExp(context + TARGET, "gi"));
// srcset="a.png 1x, https://cdn/b.png 2x" lists several URLs: every candidate counts.
const SRCSET = /(?<![\w$.-])srcset\s*[=:]\s*["'\x60]([^"'\x60]*)["'\x60]/gi;
const ABSOLUTE = /^(?:https?:|wss?:)?\/\//i;
const LOCAL = /^(?:(?:https?:|wss?:)?\/\/)(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:[/?#]|$)/i;

/** Every external load target in `text`, as the matched snippet. */
export function externalTargets(text) {
  const found = [];
  for (const pattern of CONTEXTS)
    for (const match of text.matchAll(pattern)) if (!LOCAL.test(match[1])) found.push(match[0].slice(0, 120));
  for (const match of text.matchAll(SRCSET))
    for (const candidate of match[1].split(",")) {
      const url = candidate.trim().split(/\s+/)[0] ?? "";
      if (ABSOLUTE.test(url) && !LOCAL.test(url)) found.push(`srcset ${url}`.slice(0, 120));
    }
  return found;
}

const files = (dir) =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });

/** Every external load target in the bundle at `root`, as `file: snippet`. */
export function scanBundle(root) {
  return files(root)
    .filter((file) => /\.(?:html|js|mjs|css|svg|json|webmanifest)$/i.test(file)) // fonts and images are binary
    .flatMap((file) => externalTargets(readFileSync(file, "utf8")).map((hit) => `${relative(root, file)}: ${hit}`));
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const root = process.argv[2];
  if (!root) {
    console.error("usage: check-no-external.mjs <bundle dir>");
    process.exit(2);
  }
  const found = scanBundle(root);
  if (found.length) {
    console.error(`external load targets in ${root}:\n  ${found.join("\n  ")}`);
    process.exit(1);
  }
  console.log(`no external load targets in ${root}`);
}
