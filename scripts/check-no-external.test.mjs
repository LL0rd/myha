// node --test scripts/check-no-external.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { externalTargets } from "./check-no-external.mjs";

const EXTERNAL = {
  "HTML src": `<img src='https://cdn.example/x.png'>`,
  "HTML href": `<link rel="stylesheet" href="https://fonts.example/css">`,
  "protocol-relative src": `<script src="//cdn.example/a.js"></script>`,
  "HTML action": `<form action="https://evil.example/post">`,
  "HTML poster": `<video poster="https://cdn.example/p.jpg">`,
  "JSX src prop": `createElement("img",{src:"https://cdn.example/x.png"})`,
  "JSX href prop": `jsx("a",{href:'https://evil.example/'})`,
  "JSX srcSet prop": `jsx("img",{srcSet:"/a.png 1x, https://cdn.example/b.png 2x"})`,
  "srcset second candidate": `<img srcset="/a.png 1x, https://cdn.example/b.png 2x">`,
  "manifest src": `{"icons":[{"src":"https://cdn.example/i.png"}]}`,
  "CSS url()": `.a{background:url(https://img.example/bg.png)}`,
  "CSS @import string": `@import "https://fonts.example/a.css";`,
  "CSS @import url": `@import url(https://fonts.example/a.css);`,
  "dynamic import": `import("//cdn.example/m.js")`,
  importScripts: `importScripts("https://cdn.example/w.js")`,
  fetch: `fetch("https://evil.example/x")`,
  sendBeacon: `navigator.sendBeacon("https://track.example/b",d)`,
  WebSocket: "new WebSocket(`wss://evil.example/ws`)",
  EventSource: `new EventSource("https://evil.example/s")`,
};

const ALLOWED = {
  "error decoder text": `"https://react.dev/errors/"+e`,
  "SVG namespace": `{xmlns:"http://www.w3.org/2000/svg"}`,
  "relative fetch": `fetch("/api/v1/me")`,
  "localhost socket": `new WebSocket("ws://localhost:8000/x")`,
  "loopback fetch": `fetch("http://127.0.0.1:9/x")`,
  "relative srcset": `<img srcset="/a.png 1x, /b.png 2x">`,
  "relative manifest": `{"src":"/icon.svg"}`,
  "data URI": `.a{background:url(data:image/png;base64,AAAA)}`,
};

for (const [name, text] of Object.entries(EXTERNAL))
  test(`finds ${name}`, () => assert.notDeepEqual(externalTargets(text), [], text));
for (const [name, text] of Object.entries(ALLOWED))
  test(`allows ${name}`, () => assert.deepEqual(externalTargets(text), [], text));
