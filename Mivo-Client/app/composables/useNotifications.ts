import { toast } from "vue-sonner";
import type { RouteLocationRaw } from "vue-router";
import type { ConversationUpdatedEvent, NotificationItem } from "~/types/api";
import { getAccessToken } from "./useApi";

const PAGE_SIZE = 50; // server max is 100
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

// Module-level (ssr:false, one app per tab): shared by every NotificationBell
// instance and the notifications page, and reset on sign-out.
const notifications = ref<NotificationItem[]>([]);
// Only ever taken from GET /notifications/unread-count — never recomputed
// from the loaded page, which is just a window onto the list.
const unreadCount = ref(0);
const isLoading = ref(false);
const isLoadingMore = ref(false);
const hasMore = ref(false);
const loadError = ref<string | null>(null);
const isConnected = ref(false);

let eventSource: EventSource | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = RECONNECT_BASE_MS;
// Bumped on every disconnect: a ticket request still in flight from an older
// connection attempt sees the mismatch and doesn't open a stream.
let connectGeneration = 0;
let connecting = false;
let hadConnection = false;
let pollInterval: ReturnType<typeof setInterval> | null = null;
let activeSubscribers = 0;

const conversationListeners = new Set<(event: ConversationUpdatedEvent) => void>();

function closeStream() {
  connectGeneration++;
  connecting = false;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  isConnected.value = false;
}

// Types that have a label in the locale files (notifications.types.*).
export const KNOWN_NOTIFICATION_TYPES = ["lead_hot", "lead_warm", "lead_updated", "handoff", "ai_limit", "delivery_failed"];

/** Where a notification leads: the chat for conversation alerts (handoff,
 * ai_limit and delivery_failed carry extra_metadata.conversation_id), the
 * lead for lead alerts, nowhere otherwise. */
export function notificationTarget(item: NotificationItem): RouteLocationRaw | null {
  const conversationId = item.extra_metadata?.conversation_id;
  if (typeof conversationId === "string" && conversationId) return { path: "/", query: { id: conversationId } };
  if (item.lead_id) return { path: "/leads", query: { id: item.lead_id } };
  if (item.type.startsWith("lead")) return "/leads";
  return null;
}

/** Sign-out: tear down the stream/timers and forget everything loaded. */
export function resetNotificationsState(): void {
  closeStream();
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
  activeSubscribers = 0;
  reconnectDelay = RECONNECT_BASE_MS;
  hadConnection = false;
  conversationListeners.clear();
  notifications.value = [];
  unreadCount.value = 0;
  hasMore.value = false;
  loadError.value = null;
}

export function useNotifications() {
  const api = useMivoApi();
  const { t } = useI18n();
  const config = useRuntimeConfig();
  const apiBase = config.public.apiBase as string;

  async function fetchUnreadCount() {
    try {
      const res = await api.getUnreadCount();
      unreadCount.value = res.unread_count;
    } catch (err) {
      console.error("[useNotifications] Failed to load unread count", err);
    }
  }

  async function fetchNotifications() {
    isLoading.value = true;
    loadError.value = null;
    try {
      const items = await api.listNotifications(PAGE_SIZE, 0);
      notifications.value = items;
      hasMore.value = items.length === PAGE_SIZE;
    } catch (err) {
      console.error("[useNotifications] Failed to load notifications", err);
      loadError.value = err instanceof Error && err.message ? err.message : t("notifications.loadError");
    } finally {
      isLoading.value = false;
    }
    await fetchUnreadCount();
  }

  async function loadMore() {
    if (isLoadingMore.value || !hasMore.value) return;
    isLoadingMore.value = true;
    try {
      const items = await api.listNotifications(PAGE_SIZE, notifications.value.length);
      const known = new Set(notifications.value.map((n) => n.id));
      notifications.value = [...notifications.value, ...items.filter((n) => !known.has(n.id))];
      hasMore.value = items.length === PAGE_SIZE;
    } catch (err) {
      console.error("[useNotifications] Failed to load more notifications", err);
      toast.error(err instanceof Error && err.message ? err.message : t("notifications.loadError"));
    } finally {
      isLoadingMore.value = false;
    }
  }

  function actionFailed(err: unknown) {
    console.error("[useNotifications] action failed", err);
    toast.error(err instanceof Error && err.message ? err.message : t("notifications.actionError"));
  }

  // Optimistic updates below are always rolled back when the request fails.

  async function markAsRead(id: string) {
    const item = notifications.value.find((n) => n.id === id);
    if (!item || item.is_read) return;
    item.is_read = true;
    unreadCount.value = Math.max(0, unreadCount.value - 1);
    try {
      await api.markNotificationRead(id);
    } catch (err) {
      item.is_read = false;
      actionFailed(err);
    }
    await fetchUnreadCount();
  }

  async function markAllAsRead() {
    const previouslyUnread = notifications.value.filter((n) => !n.is_read);
    const previousCount = unreadCount.value;
    previouslyUnread.forEach((n) => (n.is_read = true));
    unreadCount.value = 0;
    try {
      await api.markAllNotificationsRead();
    } catch (err) {
      previouslyUnread.forEach((n) => (n.is_read = false));
      unreadCount.value = previousCount;
      actionFailed(err);
    }
    await fetchUnreadCount();
  }

  const unreadNotifications = computed(() => notifications.value.filter((n) => !n.is_read));

  async function deleteNotification(id: string) {
    const idx = notifications.value.findIndex((n) => n.id === id);
    if (idx === -1) return;
    const removed = notifications.value[idx]!;
    notifications.value.splice(idx, 1);
    try {
      await api.deleteNotification(id);
    } catch (err) {
      notifications.value.splice(Math.min(idx, notifications.value.length), 0, removed);
      actionFailed(err);
    }
    await fetchUnreadCount();
  }

  async function deleteReadNotifications() {
    const previous = notifications.value;
    notifications.value = previous.filter((n) => !n.is_read);
    try {
      await api.deleteReadNotifications();
    } catch (err) {
      notifications.value = previous;
      actionFailed(err);
    }
  }

  /** Conversation activity arrives on the same stream; the chat page
   * subscribes to refresh itself. Returns an unsubscribe function. */
  function onConversationUpdated(listener: (event: ConversationUpdatedEvent) => void): () => void {
    conversationListeners.add(listener);
    return () => conversationListeners.delete(listener);
  }

  function handleStreamPayload(raw: string) {
    let data: any;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      console.error("[useNotifications] Unparseable SSE payload", e);
      return;
    }
    if (data?.type === "conversation_updated") {
      conversationListeners.forEach((listener) => listener(data as ConversationUpdatedEvent));
      return;
    }
    if (typeof data?.id !== "string") return;
    const payload = data as NotificationItem;
    if (!notifications.value.some((n) => n.id === payload.id)) {
      notifications.value.unshift(payload);
    }
    fetchUnreadCount();
  }

  function scheduleReconnect() {
    if (reconnectTimer || activeSubscribers === 0) return;
    const delay = reconnectDelay;
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectSse();
    }, delay);
  }

  async function connectSse() {
    if (!import.meta.client) return;
    if (eventSource || connecting || activeSubscribers === 0) return;
    if (!getAccessToken()) return;

    connecting = true;
    const generation = connectGeneration;
    let ticket: string;
    try {
      // EventSource can't send headers — a fresh one-minute ticket per
      // connection attempt, never the access token in the URL.
      ticket = (await api.getStreamTicket()).ticket;
    } catch (err) {
      if (generation !== connectGeneration) return;
      connecting = false;
      console.error("[useNotifications] Stream ticket request failed", err);
      scheduleReconnect();
      return;
    }
    if (generation !== connectGeneration) return;
    connecting = false;

    const es = new EventSource(`${apiBase}/notifications/stream?ticket=${encodeURIComponent(ticket)}`);
    eventSource = es;

    es.addEventListener("connected", () => {
      if (eventSource !== es) return;
      isConnected.value = true;
      reconnectDelay = RECONNECT_BASE_MS;
      // Anything broadcast while the stream was down was missed — re-sync.
      if (hadConnection) fetchNotifications();
      hadConnection = true;
    });
    es.addEventListener("notification", (event) => {
      if (eventSource !== es) return;
      handleStreamPayload((event as MessageEvent).data);
    });
    es.addEventListener("ping", () => {
      // Heartbeat keepalive
    });
    es.onerror = () => {
      if (eventSource !== es) return;
      // Close it ourselves: the browser's own retry would reuse the
      // (single-use, expiring) ticket in the URL.
      es.close();
      eventSource = null;
      isConnected.value = false;
      scheduleReconnect();
    };
  }

  function startBackgroundSync() {
    activeSubscribers++;
    if (activeSubscribers === 1) {
      fetchNotifications();
      connectSse();

      if (import.meta.client && !pollInterval) {
        // Periodic fallback while the stream is down
        pollInterval = setInterval(() => {
          if (!isConnected.value) fetchUnreadCount();
        }, 30000);
      }
    }
  }

  function stopBackgroundSync() {
    activeSubscribers = Math.max(0, activeSubscribers - 1);
    if (activeSubscribers === 0) {
      closeStream();
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
    isLoadingMore,
    hasMore,
    loadError,
    isConnected,
    fetchNotifications,
    fetchUnreadCount,
    loadMore,
    markAsRead,
    markAllAsRead,
    deleteNotification,
    deleteReadNotifications,
    onConversationUpdated,
    startBackgroundSync,
    stopBackgroundSync,
  };
}
