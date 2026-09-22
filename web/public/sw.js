const CACHE_NAME = 'live2d-qa-v2';
const STATIC_ASSETS = [
  '/',
  '/characters',
  '/generate',
  '/layers',
  '/live2d',
  '/preview',
  '/chat',
  '/export',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // 不拦截 API、输出文件、WebSocket 等动态请求
  if (event.request.method !== 'GET') return;
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/output/') ||
    url.pathname.startsWith('/generated/') ||
    url.pathname === '/ws' ||
    url.pathname.startsWith('/ws/')
  ) {
    return;
  }

  event.respondWith(
    caches.match(event.request).then((response) => {
      if (response) return response;
      return fetch(event.request).catch(() => {
        return new Response('Offline', { status: 503 });
      });
    })
  );
});
