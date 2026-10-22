#!/usr/bin/env node
// Fails when a built bundle would load anything from another host.
// Usage: node scripts/check-no-external.mjs myha/web/dist/guest
// Only load contexts count (src=, href=, url(, import(, fetch(, new WebSocket(, a manifest "src"):
// plain text such as React's error-decoder URL or an SVG namespace loads nothing and passes.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const root = process.argv[2];
if (!root) {
  console.error("usage: check-no-external.mjs <bundle dir>");
  process.exit(2);
}

const URL_START = String.raw`["'\x60]?\s*((?:https?:|wss?:)?//[^\s"'\x60)<>]+)`;
const CONTEXTS = [
  String.raw`(?<![\w:-])(?:src|href|srcset|action|poster)\s*=\s*`,
  String.raw`url\(\s*`,
  String.raw`\bimport\(\s*`,
  String.raw`\bimportScripts\(\s*`,
  String.raw`\bfetch\(\s*`,
  String.raw`\bnew\s+(?:WebSocket|EventSource|Worker|SharedWorker)\(\s*`,
  String.raw`"(?:src|start_url|scope|url)"\s*:\s*`,
].map((context) => new RegExp(context + URL_START, "gi"));
const LOCAL = /^(?:(?:https?:|wss?:)?\/\/)(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:[/?#]|$)/i;

const files = (dir) =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });

const found = [];
for (const file of files(root)) {
  if (!/\.(?:html|js|mjs|css|svg|json|webmanifest)$/i.test(file)) continue; // fonts and images are binary
  const text = readFileSync(file, "utf8");
  for (const pattern of CONTEXTS)
    for (const match of text.matchAll(pattern))
      if (!LOCAL.test(match[1])) found.push(`${relative(root, file)}: ${match[0].slice(0, 120)}`);
}

if (found.length) {
  console.error(`external load targets in ${root}:\n  ${found.join("\n  ")}`);
  process.exit(1);
}
console.log(`no external load targets in ${root}`);
