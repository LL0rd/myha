// Service worker of the guest app: caches only the hashed bundle files under /assets/ (design §7).
// Pages, the API and the event stream always go to the network, so nothing private is ever cached.
// Typed by hand: the WebWorker lib cannot share one tsconfig with the DOM lib.

interface ExtendableEvent extends Event {
  waitUntil(promise: Promise<unknown>): void;
}
interface FetchEvent extends ExtendableEvent {
  readonly request: Request;
  respondWith(response: Promise<Response>): void;
}
interface Worker {
  addEventListener(type: "install" | "activate", listener: (event: ExtendableEvent) => void): void;
  addEventListener(type: "fetch", listener: (event: FetchEvent) => void): void;
  skipWaiting(): Promise<void>;
  clients: { claim(): Promise<void> };
  location: Location;
}

const worker = self as unknown as Worker;
// shortcut: the cache keeps the assets of earlier releases too (a few hundred KB each); bump the name to drop them.
const CACHE = "myha-assets-v1";

worker.addEventListener("install", () => void worker.skipWaiting());

worker.addEventListener("activate", (event) => {
  const old = caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))));
  event.waitUntil(old.then(() => worker.clients.claim()));
});

worker.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== worker.location.origin || !url.pathname.startsWith("/assets/")) return;
  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const hit = await cache.match(event.request);
      if (hit) return hit;
      const response = await fetch(event.request);
      if (response.ok) await cache.put(event.request, response.clone()); // file names carry a content hash
      return response;
    }),
  );
});

export {}; // a module for TypeScript; the build emits no export
