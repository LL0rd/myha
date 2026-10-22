#!/usr/bin/env node
// Stand-in for the Home Assistant ingress: forwards to the admin listener and asserts the identity itself.
// Identity headers a client sends are dropped, as the real Supervisor does; HTTP only (no admin WebSocket in Plan 2).
import http from "node:http";
import { INGRESS, PORTS } from "./helpers/constants.mjs";

const FORGEABLE = /^(?:x-remote-user-.*|x-ingress-path|x-forwarded-.*|x-real-ip|forwarded)$/i;

const server = http.createServer((request, response) => {
  const headers = Object.fromEntries(Object.entries(request.headers).filter(([name]) => !FORGEABLE.test(name)));
  headers.host = `127.0.0.1:${PORTS.admin}`;
  headers["x-remote-user-id"] = INGRESS.userId;
  headers["x-ingress-path"] = INGRESS.path;
  const upstream = http.request(
    { host: "127.0.0.1", port: PORTS.admin, method: request.method, path: request.url, headers },
    (answer) => {
      response.writeHead(answer.statusCode ?? 502, answer.headers);
      answer.pipe(response);
    },
  );
  upstream.on("error", (error) => {
    if (!response.headersSent) response.writeHead(502, { "content-type": "text/plain" });
    response.end(`ingress proxy: ${error.message}`);
  });
  request.pipe(upstream);
});

server.listen(PORTS.ingress, "127.0.0.1", () => console.log(`ingress proxy on ${INGRESS.url} -> 127.0.0.1:${PORTS.admin}`));
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.close(() => process.exit(0)));
