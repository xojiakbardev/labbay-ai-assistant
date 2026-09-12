import { ApiError } from "./useApi";

type MivoApi = ReturnType<typeof useMivoApi>;

function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/\-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(new ArrayBuffer(rawData.length));
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

function pushSupported(): boolean {
  return import.meta.client && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/**
 * Removes this browser's push subscription: server first (it needs the
 * endpoint while the session is still valid), then the browser side. Used by
 * "disable push" and by sign-out. Resolves to false when this browser had no
 * subscription.
 */
export async function unsubscribeDevicePush(api: MivoApi): Promise<boolean> {
  if (!pushSupported()) return false;
  const registration = await navigator.serviceWorker.getRegistration();
  if (!registration) return false;
  const subscription = await registration.pushManager.getSubscription();
  if (!subscription) return false;
  try {
    await api.unsubscribePush({ endpoint: subscription.endpoint });
  } finally {
    // The browser side always goes: after this nothing reaches this device,
    // even if the server call failed (the error still propagates).
    await subscription.unsubscribe();
  }
  return true;
}

/** Whether THIS browser holds a push subscription. The server's status is per
 * business (any device), so it can't answer this. */
async function deviceIsSubscribed(): Promise<boolean> {
  const registration = await navigator.serviceWorker.getRegistration();
  if (!registration) return false;
  return (await registration.pushManager.getSubscription()) !== null;
}

export function usePushNotifications() {
  const api = useMivoApi();
  const { t } = useI18n();

  const isSupported = computed(() => pushSupported());

  const permission = ref<NotificationPermission>("default");
  const isSubscribed = ref<boolean>(false);
  const devicesCount = ref<number>(0);
  // null = not known yet; false = the server has no VAPID keys (push UI hidden).
  const isConfigured = ref<boolean | null>(null);
  const loading = ref<boolean>(false);
  const error = ref<string | null>(null);

  function describe(err: unknown, fallbackKey: string): string {
    if (err instanceof ApiError) {
      if (err.status === 503) return t("push.notConfigured");
      if (err.status === 422) return t("push.invalidEndpoint");
      return err.message;
    }
    return err instanceof Error && err.message ? err.message : t(fallbackKey);
  }

  async function checkStatus() {
    if (!isSupported.value) return;
    permission.value = Notification.permission;
    error.value = null;
    try {
      const [{ public_key }, status, onDevice] = await Promise.all([
        api.getVapidPublicKey(),
        api.getPushStatus(),
        deviceIsSubscribed(),
      ]);
      isConfigured.value = public_key !== "";
      isSubscribed.value = onDevice;
      devicesCount.value = status.devices_count;
    } catch (err) {
      console.error("[usePushNotifications] Failed to fetch push status", err);
      error.value = describe(err, "push.statusError");
    }
  }

  async function enablePush(): Promise<boolean> {
    if (!isSupported.value) {
      error.value = t("push.unsupported");
      return false;
    }

    loading.value = true;
    error.value = null;

    try {
      // 1. The server must have VAPID keys — checked before prompting the
      //    user for a permission we couldn't use.
      const { public_key: vapidKey } = await api.getVapidPublicKey();
      isConfigured.value = vapidKey !== "";
      if (!vapidKey) {
        error.value = t("push.notConfigured");
        return false;
      }

      // 2. Request permission from user
      const perm = await Notification.requestPermission();
      permission.value = perm;
      if (perm !== "granted") {
        error.value = t("push.permissionDenied");
        return false;
      }

      // 3. Register service worker
      const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;

      // 4. Subscribe to PushManager
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidKey),
      });

      const rawJson = subscription.toJSON();
      const endpoint = rawJson.endpoint;
      const p256dh = rawJson.keys?.p256dh;
      const auth = rawJson.keys?.auth;
      if (!endpoint || !p256dh || !auth) {
        await subscription.unsubscribe();
        error.value = t("push.keysMissing");
        return false;
      }

      // 5. Send to backend. If it refuses, the browser subscription is
      //    rolled back so the two sides never disagree.
      let res: { subscribed: boolean; devices_count: number };
      try {
        res = await api.subscribePush({
          endpoint,
          keys: { p256dh, auth },
          user_agent: navigator.userAgent.slice(0, 255),
        });
      } catch (err) {
        await subscription.unsubscribe();
        throw err;
      }

      isSubscribed.value = true;
      devicesCount.value = res.devices_count;
      return true;
    } catch (err) {
      console.error("[usePushNotifications] Enable push error", err);
      error.value = describe(err, "push.enableError");
      return false;
    } finally {
      loading.value = false;
    }
  }

  async function disablePush(): Promise<boolean> {
    if (!isSupported.value) return false;

    loading.value = true;
    error.value = null;

    try {
      await unsubscribeDevicePush(api);
      isSubscribed.value = false;
      devicesCount.value = (await api.getPushStatus()).devices_count;
      return true;
    } catch (err) {
      console.error("[usePushNotifications] Disable push error", err);
      error.value = describe(err, "push.disableError");
      return false;
    } finally {
      loading.value = false;
    }
  }

  async function sendTestPush(): Promise<boolean> {
    loading.value = true;
    error.value = null;
    try {
      await api.sendTestPush();
      return true;
    } catch (err) {
      console.error("[usePushNotifications] Test push error", err);
      error.value = describe(err, "push.testError");
      return false;
    } finally {
      loading.value = false;
    }
  }

  return {
    isSupported,
    isConfigured,
    permission,
    isSubscribed,
    devicesCount,
    loading,
    error,
    checkStatus,
    enablePush,
    disablePush,
    sendTestPush,
  };
}
