// Service Worker for Mivo AI Web Push Notifications

// A new version takes over right away instead of waiting for every open tab
// to close, and claims already-open tabs so notification clicks can navigate
// them (client.navigate only works on controlled clients).
self.addEventListener("install", function () {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", function (event) {
  if (!event.data) {
    return;
  }

  let data = {};
  try {
    data = event.data.json();
  } catch (e) {
    data = { title: "Mivo AI", body: event.data.text() };
  }

  const title = data.title || "Yangi Xabarnoma 🔥";
  const options = {
    body: data.body || "Mivo AI yangi hodisa yuz berdi.",
    icon: "/favicon.png",
    badge: "/favicon.png",
    tag: data.tag || "mivo-notification",
    data: {
      url: data.url || "/leads",
      lead_id: data.data?.lead_id || null,
    },
    vibrate: [200, 100, 200],
    requireInteraction: true,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// Only same-origin targets: a push payload must never be able to send the
// owner to another site.
function sameOriginUrl(raw) {
  try {
    const url = new URL(raw || "/", self.location.origin);
    return url.origin === self.location.origin ? url.href : null;
  } catch (e) {
    return null;
  }
}

self.addEventListener("notificationclick", function (event) {
  event.notification.close();

  const targetUrl = sameOriginUrl(event.notification.data?.url) || new URL("/", self.location.origin).href;

  event.waitUntil(
    (async function () {
      // Without includeUncontrolled: only tabs this worker controls, the
      // only ones navigate() works on.
      const clientList = await self.clients.matchAll({ type: "window" });
      const client = clientList.find(function (c) {
        return new URL(c.url).origin === self.location.origin;
      });
      if (client) {
        try {
          await client.focus();
          await client.navigate(targetUrl);
          return;
        } catch (e) {
          // navigate() rejected — open the target in a new window below.
        }
      }
      await self.clients.openWindow(targetUrl);
    })()
  );
});
