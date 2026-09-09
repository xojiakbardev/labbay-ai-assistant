function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/\-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

export function usePushNotifications() {
  const api = useMivoApi();

  const isSupported = computed(() => {
    return (
      import.meta.client &&
      "serviceWorker" in navigator &&
      "PushManager" in window &&
      "Notification" in window
    );
  });

  const permission = ref<NotificationPermission>("default");
  const isSubscribed = ref<boolean>(false);
  const devicesCount = ref<number>(0);
  const loading = ref<boolean>(false);
  const error = ref<string | null>(null);

  async function checkStatus() {
    if (!isSupported.value) return;

    permission.value = Notification.permission;
    try {
      const status = await api.getPushStatus();
      isSubscribed.value = status.subscribed;
      devicesCount.value = status.devices_count;
    } catch (err) {
      console.error("[usePushNotifications] Failed to fetch push status", err);
    }
  }

  async function enablePush(): Promise<boolean> {
    if (!isSupported.value) {
      error.value = "Brauzeringiz push xabarnomalarni qo'llab-quvvatlamaydi.";
      return false;
    }

    loading.value = true;
    error.value = null;

    try {
      // 1. Request permission from user
      const perm = await Notification.requestPermission();
      permission.value = perm;

      if (perm !== "granted") {
        error.value = "Bildirishnomalarga ruxsat berilmadi.";
        return false;
      }

      // 2. Register service worker
      const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;

      // 3. Get VAPID public key
      const { public_key: vapidKey } = await api.getVapidPublicKey();
      if (!vapidKey) {
        throw new Error("VAPID public key mavjud emas.");
      }

      // 4. Subscribe to PushManager
      const convertedKey = urlBase64ToUint8Array(vapidKey);
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: convertedKey,
      });

      // 5. Send to backend
      const rawJson = subscription.toJSON();
      const endpoint = rawJson.endpoint;
      const p256dh = rawJson.keys?.p256dh;
      const auth = rawJson.keys?.auth;

      if (!endpoint || !p256dh || !auth) {
        throw new Error("Push kalitlarini olishda xatolik yuz berdi.");
      }

      const res = await api.subscribePush({
        endpoint,
        keys: { p256dh, auth },
        user_agent: navigator.userAgent,
      });

      isSubscribed.value = res.subscribed;
      devicesCount.value = res.devices_count;
      return true;
    } catch (err: any) {
      console.error("[usePushNotifications] Enable push error", err);
      error.value = err?.message || "Push bildirishnomalarni yoqishda xatolik yuz berdi.";
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
      const registration = await navigator.serviceWorker.getRegistration();
      if (registration) {
        const subscription = await registration.pushManager.getSubscription();
        if (subscription) {
          await api.unsubscribePush({ endpoint: subscription.endpoint });
          await subscription.unsubscribe();
        }
      }

      const status = await api.getPushStatus();
      isSubscribed.value = status.subscribed;
      devicesCount.value = status.devices_count;
      return true;
    } catch (err: any) {
      console.error("[usePushNotifications] Disable push error", err);
      error.value = err?.message || "Push bildirishnomalarni o'chirishda xatolik yuz berdi.";
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
    } catch (err: any) {
      console.error("[usePushNotifications] Test push error", err);
      error.value = err?.message || "Test xabarnomani yuborishda xatolik yuz berdi.";
      return false;
    } finally {
      loading.value = false;
    }
  }

  return {
    isSupported,
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
