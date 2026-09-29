// v0.10.1 已废弃：本地桌面应用不需要 Service Worker 离线壳缓存。
// 本文件只保留「自清理」逻辑：任何仍注册了旧 SW 的浏览器加载它后，
// 会注销自身并清空全部缓存，随后页面回到同源 no-cache HTML 的正确行为。
// （旧版这里曾是 cache-first 策略，升级后会持续供应旧页面壳 —— 已移除。）
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
      await self.registration.unregister();
      const clientList = await self.clients.matchAll({ type: 'window' });
      clientList.forEach((client) => client.navigate(client.url));
    })()
  );
});
