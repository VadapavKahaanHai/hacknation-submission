// sw.js: makes the app work with no internet after the first visit.
// Bump VERSION whenever the model, crop_bundle.json or app files change.
const VERSION = "leafcheck-v0.3-1";
const CORE = [
  "./", "index.html", "app.js", "logic.js", "infer.js", "crop_bundle.json", "manifest.webmanifest",
  "icon-192.png", "icon-512.png",
  "vendor/tf.min.js", "vendor/tf-backend-wasm.min.js",
  "vendor/tfjs-backend-wasm.wasm", "vendor/tfjs-backend-wasm-simd.wasm", "vendor/tfjs-backend-wasm-threaded-simd.wasm",
  "model/model.json", "model/labels.json",
];

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(VERSION);
    await cache.addAll(CORE);
    // weight shard names come from model.json, so new exports need no edit here
    const mj = await (await fetch("model/model.json")).json();
    const shards = mj.weightsManifest.flatMap(g => g.paths.map(p => `model/${p}`));
    await cache.addAll(shards);
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== VERSION) await caches.delete(k);
    await self.clients.claim();
  })());
});

// Cache first: the farmer may have no signal at all.
self.addEventListener("fetch", event => {
  if (event.request.method !== "GET") return;
  event.respondWith((async () => {
    const hit = await caches.match(event.request, { ignoreSearch: true });
    if (hit) return hit;
    try { return await fetch(event.request); }
    catch (e) { return caches.match("index.html"); }
  })());
});
