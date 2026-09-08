// Minimal service worker: satisfies desktop "Install app" criteria.
// No offline caching — this app is API-driven and needs the live backend.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
