// Service Worker for Mivo AI Web Push Notifications

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

self.addEventListener("notificationclick", function (event) {
  event.notification.close();

  const targetUrl = event.notification.data?.url || "/leads";

  event.waitUntil(
    clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then(function (clientList) {
        // If an open window exists, focus it and navigate
        for (let i = 0; i < clientList.length; i++) {
          const client = clientList[i];
          if ("focus" in client) {
            client.focus();
            if ("navigate" in client) {
              client.navigate(targetUrl);
            }
            return;
          }
        }
        // Otherwise open a new window
        if (clients.openWindow) {
          return clients.openWindow(targetUrl);
        }
      })
  );
});
