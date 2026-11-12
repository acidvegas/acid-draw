// acid-draw - Developed by acidvegas in Python (https://github.com/acidvegas)
// sw.js - only here so notifications work on Android, and tapping one opens the game

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('notificationclick', (e) => {
	e.notification.close();
	const hash = (e.notification.data && e.notification.data.hash) || '#/';
	e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
		if (!list.length) return self.clients.openWindow('/' + hash);
		list[0].postMessage({ hash });
		return list[0].focus();
	}));
});
