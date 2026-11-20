'use strict';
// No fetch handler or offline cache: screen images and control traffic must
// always come directly from the authenticated Mac server.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
