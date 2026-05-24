const CACHE_NAME = 'radius-manager-v3';
const ASSETS_TO_CACHE = [
  '/static/css/output.css',
  '/static/js/lucide.min.js',
  '/static/js/echarts.min.js',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css',
  'https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=Outfit:wght@300;400;500;600;700;800;900&display=swap'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS_TO_CACHE))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Network-first untuk navigasi & API, cache-first untuk static assets
  const url = new URL(event.request.url);
  const isStatic = url.pathname.startsWith('/static/') || url.hostname !== location.hostname;

  if (isStatic) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }
  // Biarkan request lain (navigasi, API) lewat ke network tanpa intercept
});
