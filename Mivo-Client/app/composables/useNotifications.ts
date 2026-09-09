import type { NotificationItem } from "~/types/api";

let eventSource: EventSource | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let pollInterval: ReturnType<typeof setInterval> | null = null;
let activeSubscribers = 0;

export function useNotifications() {
  const api = useMivoApi();
  const config = useRuntimeConfig();
  const apiBase = config.public.apiBase as string;

  const notifications = useState<NotificationItem[]>("mivo_notifications", () => []);
  const unreadCount = useState<number>("mivo_unread_notifications_count", () => 0);
  const isLoading = useState<boolean>("mivo_notifications_loading", () => false);
  const isConnected = useState<boolean>("mivo_notifications_sse_connected", () => false);

  async function fetchNotifications() {
    isLoading.value = true;
    try {
      const items = await api.listNotifications();
      notifications.value = items;
      unreadCount.value = items.filter((n) => !n.is_read).length;
    } catch (err) {
      console.error("[useNotifications] Failed to load notifications", err);
    } finally {
      isLoading.value = false;
    }
  }

  async function fetchUnreadCount() {
    try {
      const res = await api.getUnreadCount();
      unreadCount.value = res.unread_count;
    } catch (err) {
      console.error("[useNotifications] Failed to load unread count", err);
    }
  }

  async function markAsRead(id: string) {
    const item = notifications.value.find((n) => n.id === id);
    if (item && !item.is_read) {
      item.is_read = true;
      unreadCount.value = Math.max(0, unreadCount.value - 1);
    }
    try {
      await api.markNotificationRead(id);
    } catch (err) {
      console.error("[useNotifications] Failed to mark notification as read", err);
    }
  }

  async function markAllAsRead() {
    notifications.value.forEach((n) => {
      n.is_read = true;
    });
    unreadCount.value = 0;
    try {
      await api.markAllNotificationsRead();
    } catch (err) {
      console.error("[useNotifications] Failed to mark all as read", err);
    }
  }

  const unreadNotifications = computed(() => notifications.value.filter((n) => !n.is_read));

  async function deleteNotification(id: string) {
    const idx = notifications.value.findIndex((n) => n.id === id);
    if (idx !== -1) {
      const item = notifications.value[idx];
      if (!item.is_read) {
        unreadCount.value = Math.max(0, unreadCount.value - 1);
      }
      notifications.value.splice(idx, 1);
    }
    try {
      await api.deleteNotification(id);
    } catch (err) {
      console.error("[useNotifications] Failed to delete notification", err);
    }
  }

  async function deleteReadNotifications() {
    notifications.value = notifications.value.filter((n) => !n.is_read);
    try {
      await api.deleteReadNotifications();
    } catch (err) {
      console.error("[useNotifications] Failed to delete read notifications", err);
    }
  }

  function handleIncomingNotification(payload: NotificationItem) {
    const exists = notifications.value.some((n) => n.id === payload.id);
    if (!exists) {
      notifications.value.unshift(payload);
      if (!payload.is_read) {
        unreadCount.value += 1;
      }
    }
  }

  function connectSse() {
    if (!import.meta.client) return;
    if (eventSource) return;

    const token = getAccessToken();
    if (!token) return;

    try {
      const url = `${apiBase}/notifications/stream?token=${encodeURIComponent(token)}`;
      eventSource = new EventSource(url);

      eventSource.addEventListener("connected", () => {
        isConnected.value = true;
      });

      eventSource.addEventListener("notification", (event) => {
        try {
          const data: NotificationItem = JSON.parse(event.data);
          handleIncomingNotification(data);
        } catch (e) {
          console.error("[useNotifications] Failed to parse notification SSE data", e);
        }
      });

      eventSource.addEventListener("ping", () => {
        // Heartbeat keepalive
      });

      eventSource.onerror = () => {
        isConnected.value = false;
        disconnectSse();
        if (!reconnectTimer) {
          reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            connectSse();
          }, 5000);
        }
      };
    } catch (err) {
      console.error("[useNotifications] SSE Connection failed", err);
    }
  }

  function disconnectSse() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    isConnected.value = false;
  }

  function startBackgroundSync() {
    activeSubscribers++;
    if (activeSubscribers === 1) {
      fetchUnreadCount();
      fetchNotifications();
      connectSse();

      if (import.meta.client && !pollInterval) {
        // Periodic fallback in case SSE is disconnected or sleeping
        pollInterval = setInterval(() => {
          if (!isConnected.value) {
            fetchUnreadCount();
          }
        }, 30000);
      }
    }
  }

  function stopBackgroundSync() {
    activeSubscribers = Math.max(0, activeSubscribers - 1);
    if (activeSubscribers === 0) {
      disconnectSse();
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    }
  }

  return {
    notifications,
    unreadNotifications,
    unreadCount,
    isLoading,
    isConnected,
    fetchNotifications,
    fetchUnreadCount,
    markAsRead,
    markAllAsRead,
    deleteNotification,
    deleteReadNotifications,
    startBackgroundSync,
    stopBackgroundSync,
  };
}
