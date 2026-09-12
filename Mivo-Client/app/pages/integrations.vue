<script setup lang="ts">
import { ApiError } from "~/composables/useApi";
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Copy,
  Check,
  LogOut,
  Bell,
  BellRing,
  Loader2,
  RefreshCw,
} from "@lucide/vue";
import type { InstagramStatus } from "~/types/api";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t } = useI18n();
const error = ref<string | null>(null);

const push = usePushNotifications();

const loadingStatus = ref(true);
const isInstagramConnected = ref(false);
const instagramUsername = ref<string | null>(null);
const instagramExpiresAt = ref<string | null>(null);
// A status that couldn't be loaded is shown as an error on its card — never
// silently as "not connected".
const instagramStatusError = ref<string | null>(null);

const isTelegramConnected = ref(false);
const telegramUsername = ref<string | null>(null);
const telegramStatusError = ref<string | null>(null);

const route = useRoute();
const router = useRouter();
const justConnected = ref(false);
const completingInstagram = ref(false);
const instagramConnectError = ref<string | null>(null);

function errorText(err: unknown, fallbackKey: string): string {
  return err instanceof Error && err.message ? err.message : t(fallbackKey);
}

function applyInstagramStatus(status: InstagramStatus) {
  isInstagramConnected.value = status.connected;
  instagramUsername.value = status.username ?? null;
  instagramExpiresAt.value = status.expires_at ?? null;
}

async function loadInstagramStatus() {
  instagramStatusError.value = null;
  try {
    applyInstagramStatus(await api.getInstagramStatus());
  } catch (err) {
    console.error("Failed to fetch Instagram status", err);
    instagramStatusError.value = errorText(err, "integrations.statusLoadError");
  }
}

async function loadTelegramStatus() {
  telegramStatusError.value = null;
  try {
    const status = await api.getTelegramStatus();
    isTelegramConnected.value = status.connected;
    telegramUsername.value = status.username ?? null;
  } catch (err) {
    console.error("Failed to fetch Telegram status", err);
    telegramStatusError.value = errorText(err, "integrations.statusLoadError");
  }
}

// Instagram's OAuth callback lands the browser here with a one-time
// completion id (or an error reason); the logged-in dashboard finishes the
// connection. The query is consumed once and removed from the URL.
async function handleOAuthReturn(): Promise<boolean> {
  const pending = typeof route.query.instagram_pending === "string" ? route.query.instagram_pending : null;
  const oauthError = typeof route.query.instagram_error === "string" ? route.query.instagram_error : null;
  if (!pending && !oauthError) return false;

  const nextQuery = { ...route.query };
  delete nextQuery.instagram_pending;
  delete nextQuery.instagram_error;
  router.replace({ query: nextQuery });

  if (oauthError) {
    instagramConnectError.value =
      oauthError === "denied"
        ? t("integrations.oauthDenied")
        : oauthError === "invalid_state"
          ? t("integrations.oauthInvalidState")
          : t("integrations.connectionFailed", { reason: oauthError });
    return false;
  }

  completingInstagram.value = true;
  try {
    applyInstagramStatus(await api.completeInstagramConnect(pending!));
    justConnected.value = true;
    return true;
  } catch (err) {
    console.error("Failed to complete Instagram connect", err);
    instagramConnectError.value = t("integrations.connectionFailed", {
      reason: errorText(err, "integrations.instagramConnectError"),
    });
    return false;
  } finally {
    completingInstagram.value = false;
  }
}

onMounted(async () => {
  push.checkStatus();
  try {
    const completed = await handleOAuthReturn();
    await Promise.all([completed ? Promise.resolve() : loadInstagramStatus(), loadTelegramStatus()]);
  } finally {
    loadingStatus.value = false;
  }
});

async function handleEnablePush() {
  await push.enablePush();
}

async function handleDisablePush() {
  await push.disablePush();
}

// --- Instagram: token status, shown right next to the "Ulangan" badge ------
// The backend refreshes this on its own once it's within 10 days of expiring
// (app/instagram/service.py:REFRESH_WINDOW), so it should routinely read as
// a large, calm number — it only turns into a warning color once it's
// actually inside that window, meaning the auto-refresh hasn't caught it.
const daysUntilExpiry = computed(() => {
  if (!instagramExpiresAt.value) return null;
  const ms = new Date(instagramExpiresAt.value).getTime() - Date.now();
  return Math.ceil(ms / (1000 * 60 * 60 * 24));
});
const expiryLabel = computed(() => {
  const days = daysUntilExpiry.value;
  if (days === null) return null;
  if (days <= 0) return t("integrations.expired");
  if (days === 1) return t("integrations.expiresToday");
  return t("integrations.expiresInDays", { days });
});
const expiryTone = computed<"ok" | "warm" | "hot">(() => {
  const days = daysUntilExpiry.value;
  if (days === null || days > 10) return "ok";
  return days > 3 ? "warm" : "hot";
});

// Reconnecting is a real navigation away to Instagram's own consent screen —
// not something that should fire on a stray click, so it goes through a
// confirmation dialog instead of the button doing it directly.
const showReconnectConfirm = ref(false);

async function startInstagramConnect() {
  error.value = null;
  try {
    const { oauth_url } = await api.connectInstagram();
    window.location.href = oauth_url;
  } catch (err) {
    error.value = errorText(err, "integrations.instagramConnectError");
  }
}

function onInstagramButtonClick() {
  if (isInstagramConnected.value) {
    showReconnectConfirm.value = true;
  } else {
    startInstagramConnect();
  }
}

function confirmReconnect() {
  showReconnectConfirm.value = false;
  startInstagramConnect();
}

const showDisconnectInstagramConfirm = ref(false);
const disconnectingInstagram = ref(false);

async function onDisconnectInstagram() {
  error.value = null;
  disconnectingInstagram.value = true;
  try {
    await api.disconnectInstagram();
    isInstagramConnected.value = false;
    instagramUsername.value = null;
    instagramExpiresAt.value = null;
    showDisconnectInstagramConfirm.value = false;
  } catch (err) {
    error.value = errorText(err, "integrations.instagramDisconnectError");
  } finally {
    disconnectingInstagram.value = false;
  }
}

// --- Telegram: connect (copyable link + live countdown) / disconnect -------
const telegramLink = ref<string | null>(null);
const telegramSecondsLeft = ref(0);
const telegramCopied = ref(false);
let telegramTimer: ReturnType<typeof setInterval> | null = null;

function stopTelegramCountdown() {
  if (telegramTimer) {
    clearInterval(telegramTimer);
    telegramTimer = null;
  }
}

function startTelegramCountdown(expiresAt: string) {
  stopTelegramCountdown();
  const tick = () => {
    const secs = Math.round((new Date(expiresAt).getTime() - Date.now()) / 1000);
    telegramSecondsLeft.value = Math.max(secs, 0);
    if (secs <= 0) {
      stopTelegramCountdown();
      telegramLink.value = null; // token actually expired server-side — back to a plain "connect" state
    }
  };
  tick();
  telegramTimer = setInterval(tick, 1000);
}

onUnmounted(stopTelegramCountdown);

const telegramCountdownLabel = computed(() => {
  const m = Math.floor(telegramSecondsLeft.value / 60);
  const s = telegramSecondsLeft.value % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
});

async function onConnectTelegram() {
  error.value = null;
  try {
    const { deep_link, expires_at } = await api.connectTelegram();
    telegramLink.value = deep_link;
    startTelegramCountdown(expires_at);
  } catch (err) {
    error.value =
      err instanceof ApiError && err.status === 503
        ? t("integrations.telegramNotConfigured")
        : errorText(err, "integrations.telegramConnectError");
  }
}

async function copyTelegramLink() {
  if (!telegramLink.value) return;
  try {
    await navigator.clipboard.writeText(telegramLink.value);
  } catch (err) {
    console.error("Clipboard write failed", err);
    error.value = t("common.copyFailed");
    return;
  }
  telegramCopied.value = true;
  setTimeout(() => (telegramCopied.value = false), 2000);
}

async function onDisconnectTelegram() {
  error.value = null;
  try {
    await api.disconnectTelegram();
    isTelegramConnected.value = false;
    telegramUsername.value = null;
  } catch (err) {
    error.value = errorText(err, "integrations.telegramDisconnectError");
  }
}
</script>

<template>
  <div class="integrations-page max-w-7xl mx-auto pb-12 space-y-3">
    <div v-if="error" class="error mb-5">
      <AlertCircle :size="18" />
      {{ error }}
    </div>

    <div v-if="completingInstagram" class="success mb-5">
      <Loader2 :size="18" class="animate-spin" />
      {{ t("integrations.completingInsta") }}
    </div>

    <div v-if="justConnected" class="success mb-5">
      <CheckCircle2 :size="18" />
      {{ t("integrations.successInsta") }}
    </div>

    <div v-if="instagramConnectError" class="error mb-5">
      <AlertCircle :size="18" />
      {{ instagramConnectError }}
    </div>

    <!-- Loading Skeleton State -->
    <div v-if="loadingStatus" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-3.5 items-stretch">
      <Card v-for="i in 3" :key="i" class="p-4 sm:p-5 flex flex-col justify-between space-y-4">
        <div>
          <div class="flex items-center justify-between mb-3.5">
            <Skeleton class="h-10 w-10 rounded-xl" />
            <Skeleton class="h-6 w-20 rounded-full" />
          </div>
          <Skeleton class="h-5 w-2/3 mb-2" />
          <Skeleton class="h-4 w-full mb-1" />
          <Skeleton class="h-4 w-4/5 mb-4" />
          <Skeleton class="h-[64px] w-full rounded-xl" />
        </div>
        <Skeleton class="h-9 w-full rounded-lg mt-2" />
      </Card>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-3.5 items-stretch">
      <!-- Instagram Card -->
      <Card class="p-4 sm:p-5 flex flex-col justify-between">
        <div>
          <div class="flex items-center justify-between gap-2 mb-3.5">
            <div class="w-10 h-10 rounded-xl bg-gradient-to-br from-pink-500/15 to-orange-500/15 text-pink-500 flex items-center justify-center shrink-0">
              <InstagramIcon :size="20" />
            </div>
            <span v-if="isInstagramConnected" class="connected-pill">
              <CheckIcon :size="13" /> {{ t("integrations.connected") }}
            </span>
          </div>

          <h2 class="text-base sm:text-lg font-bold text-foreground leading-snug">{{ t("integrations.instagramTitle") }}</h2>
          <p class="text-xs text-muted-foreground leading-relaxed mt-1 mb-3.5 min-h-[36px] line-clamp-2">
            {{ t("integrations.instagramDesc") }}
          </p>

          <div v-if="instagramStatusError" class="bg-destructive/10 border border-destructive/20 text-destructive p-3 rounded-xl mb-4 min-h-[66px] flex flex-col justify-center gap-1.5 text-xs">
            <div class="flex items-center gap-1.5 font-semibold">
              <AlertCircle :size="14" class="shrink-0" />
              <span>{{ t("integrations.statusLoadError") }}</span>
            </div>
            <span class="text-destructive/80">{{ instagramStatusError }}</span>
          </div>

          <div v-else-if="isInstagramConnected" class="bg-muted/50 border border-border p-3.5 rounded-xl mb-4 min-h-[66px] flex flex-col justify-center">
            <div class="text-[11px] text-muted-foreground uppercase tracking-wider font-semibold mb-0.5">{{ t("integrations.account") }}</div>
            <div class="font-bold text-sm text-foreground flex items-center gap-1 truncate">
              <span class="text-primary font-bold">@</span>{{ instagramUsername || t("integrations.connectedAccount") }}
            </div>
            <div v-if="expiryLabel" class="mt-1 text-[11px] font-medium" :class="expiryTone === 'ok' ? 'text-muted-foreground' : expiryTone === 'warm' ? 'text-amber-600 dark:text-amber-400' : 'text-destructive'">
              {{ t("integrations.tokenExpires") }}: {{ expiryLabel }}
            </div>
          </div>
        </div>

        <div class="mt-auto pt-2">
          <Button v-if="instagramStatusError" variant="outline" class="w-full h-9 gap-2 text-xs sm:text-sm font-medium" @click="loadInstagramStatus">
            <RefreshCw :size="14" />
            <span>{{ t("common.retry") }}</span>
          </Button>
          <Button v-else-if="!isInstagramConnected" class="w-full h-9 gap-2 text-xs sm:text-sm font-medium" :disabled="completingInstagram" @click="onInstagramButtonClick">
            <InstagramIcon :size="16" />
            <span>{{ t("integrations.instagramConnectBtn") }}</span>
          </Button>
          <div v-else class="flex items-center gap-2">
            <Button variant="outline" class="flex-1 h-9 gap-1.5 text-xs sm:text-sm font-medium" @click="onInstagramButtonClick">
              <RefreshCw :size="14" />
              <span>{{ t("integrations.instagramReconnectBtn") }}</span>
            </Button>
            <Button
              variant="outline"
              class="h-9 gap-1.5 text-xs sm:text-sm font-medium border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive hover:border-destructive/50 shrink-0"
              @click="showDisconnectInstagramConfirm = true"
            >
              <LogOut :size="14" />
              <span>{{ t("integrations.telegramDisconnectBtn") }}</span>
            </Button>
          </div>
        </div>
      </Card>

      <!-- Telegram Card -->
      <Card class="p-4 sm:p-5 flex flex-col justify-between">
        <div>
          <div class="flex items-center justify-between gap-2 mb-3.5">
            <div class="w-10 h-10 rounded-xl bg-sky-500/15 text-sky-500 flex items-center justify-center shrink-0">
              <TelegramIcon :size="20" />
            </div>
            <span v-if="isTelegramConnected" class="connected-pill">
              <CheckIcon :size="13" /> {{ t("integrations.connected") }}
            </span>
            <span v-else-if="telegramLink" class="connected-pill border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400">
              {{ t("integrations.actionRequired") }}
            </span>
          </div>

          <h2 class="text-base sm:text-lg font-bold text-foreground leading-snug">{{ t("integrations.telegramTitle") }}</h2>
          <p class="text-xs text-muted-foreground leading-relaxed mt-1 mb-3.5 min-h-[36px] line-clamp-2">
            {{ t("integrations.telegramDesc") }}
          </p>

          <div v-if="telegramStatusError" class="bg-destructive/10 border border-destructive/20 text-destructive p-3 rounded-xl mb-4 min-h-[66px] flex flex-col justify-center gap-1.5 text-xs">
            <div class="flex items-center gap-1.5 font-semibold">
              <AlertCircle :size="14" class="shrink-0" />
              <span>{{ t("integrations.statusLoadError") }}</span>
            </div>
            <span class="text-destructive/80">{{ telegramStatusError }}</span>
          </div>

          <div v-else-if="isTelegramConnected" class="bg-muted/50 border border-border p-3.5 rounded-xl mb-4 min-h-[66px] flex flex-col justify-center">
            <div class="text-[11px] text-muted-foreground uppercase tracking-wider font-semibold mb-0.5">{{ t("integrations.chat") }}</div>
            <div class="font-bold text-sm text-foreground flex items-center gap-1 truncate">
              <span class="text-sky-500 font-bold">@</span>{{ telegramUsername || t("integrations.telegramConnectedChat") }}
            </div>
          </div>

          <div v-else-if="telegramLink" class="bg-muted/50 border border-dashed border-primary/50 p-3 rounded-xl mb-4 min-h-[66px] flex flex-col justify-center">
            <div class="flex items-center justify-between mb-1">
              <p class="text-xs font-semibold text-foreground truncate">{{ t("integrations.stepFinal") }}</p>
              <span class="inline-flex items-center gap-1 text-[11px] font-mono font-semibold text-muted-foreground shrink-0" :title="t('integrations.linkExpiresIn')">
                <Clock :size="11" /> {{ telegramCountdownLabel }}
              </span>
            </div>
            <div class="flex items-center gap-2">
              <a :href="telegramLink" target="_blank" rel="noreferrer" class="min-w-0 flex-1 text-xs font-semibold truncate text-primary hover:underline">
                {{ telegramLink }}
              </a>
              <Button type="button" variant="outline" size="icon" class="h-7 w-7 shrink-0" :title="t('integrations.copyLink')" @click="copyTelegramLink">
                <Check v-if="telegramCopied" :size="13" class="text-emerald-600 dark:text-emerald-400" />
                <Copy v-else :size="13" />
              </Button>
            </div>
          </div>
        </div>

        <div class="mt-auto pt-2">
          <Button v-if="telegramStatusError" variant="outline" class="w-full h-9 gap-2 text-xs sm:text-sm font-medium" @click="loadTelegramStatus">
            <RefreshCw :size="14" />
            <span>{{ t("common.retry") }}</span>
          </Button>
          <Button
            v-else-if="isTelegramConnected"
            variant="outline"
            class="w-full h-9 gap-1.5 text-xs sm:text-sm font-medium border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive hover:border-destructive/50"
            @click="onDisconnectTelegram"
          >
            <LogOut :size="14" />
            <span>{{ t("integrations.telegramDisconnectBtn") }}</span>
          </Button>
          <Button
            v-else
            :variant="telegramLink ? 'outline' : 'default'"
            class="w-full h-9 gap-2 text-xs sm:text-sm font-medium"
            @click="onConnectTelegram"
          >
            <TelegramIcon :size="16" />
            <span>{{ telegramLink ? t("integrations.telegramRegenerateBtn") : t("integrations.telegramConnectBtn") }}</span>
          </Button>
        </div>
      </Card>

      <!-- Web Push Card -->
      <Card class="p-4 sm:p-5 flex flex-col justify-between">
        <div>
          <div class="flex items-center justify-between gap-2 mb-3.5">
            <div class="w-10 h-10 rounded-xl bg-amber-500/15 text-amber-500 flex items-center justify-center shrink-0">
              <BellRing :size="20" />
            </div>
            <span v-if="push.isSubscribed.value" class="connected-pill">
              <CheckIcon :size="13" /> {{ t("integrations.connected") }}
            </span>
            <span v-else-if="push.permission.value === 'denied'" class="connected-pill border-red-500/30 bg-red-500/10 text-red-500">
              {{ t("integrations.pushBlocked") }}
            </span>
          </div>

          <h2 class="text-base sm:text-lg font-bold text-foreground leading-snug">{{ t("integrations.pushTitle") }}</h2>
          <p class="text-xs text-muted-foreground leading-relaxed mt-1 mb-3.5 min-h-[36px] line-clamp-2">
            {{ t("integrations.pushDesc") }}
          </p>

          <!-- The server has no VAPID keys: push can't work, so it isn't offered. -->
          <div v-if="push.isConfigured.value === false" class="bg-muted/50 border border-border text-muted-foreground p-3 rounded-xl text-xs mb-4 min-h-[66px] flex items-center">
            {{ t("push.notConfigured") }}
          </div>

          <div v-else-if="!push.isSupported.value" class="bg-muted/50 border border-border text-muted-foreground p-3 rounded-xl text-xs mb-4 min-h-[66px] flex items-center">
            {{ t("push.unsupported") }}
          </div>

          <div v-if="push.error.value" class="bg-destructive/10 border border-destructive/20 text-destructive p-3 rounded-xl text-xs mb-4 flex items-start gap-1.5">
            <AlertCircle :size="14" class="shrink-0 mt-0.5" />
            <span>{{ push.error.value }}</span>
          </div>

          <div v-if="push.isSubscribed.value" class="bg-muted/50 border border-border p-3.5 rounded-xl mb-4 min-h-[66px] flex flex-col justify-center">
            <div class="text-[11px] text-muted-foreground uppercase tracking-wider font-semibold mb-0.5">
              {{ t("integrations.account") }}
            </div>
            <div class="font-bold text-foreground text-sm flex items-center gap-1.5 truncate">
              <span class="inline-block w-2 h-2 rounded-full bg-emerald-500 shrink-0"></span>
              <span>{{ t("integrations.pushConnectedDevices", { count: push.devicesCount.value || 1 }) }}</span>
            </div>
          </div>

          <div v-if="push.permission.value === 'denied'" class="bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 p-3 rounded-xl text-xs mb-4 min-h-[66px] flex items-center">
            {{ t("integrations.pushBlockedNotice") }}
          </div>
        </div>

        <div class="mt-auto pt-2">
          <Button
            v-if="push.isSubscribed.value"
            variant="outline"
            class="w-full h-9 gap-1.5 text-xs sm:text-sm font-medium border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive hover:border-destructive/50"
            :disabled="push.loading.value"
            @click="handleDisablePush"
          >
            <LogOut :size="14" />
            <span>{{ t("integrations.pushDisableBtn") }}</span>
          </Button>
          <Button
            v-else
            class="w-full h-9 gap-2 text-xs sm:text-sm font-medium"
            :disabled="push.loading.value || !push.isSupported.value || push.isConfigured.value === false"
            @click="handleEnablePush"
          >
            <Loader2 v-if="push.loading.value" :size="16" class="animate-spin" />
            <Bell v-else :size="16" />
            <span>{{ t("integrations.pushEnableBtn") }}</span>
          </Button>
        </div>
      </Card>
    </div>

    <!-- Reconnect confirmation — Instagram OAuth is a full-page redirect away
         from the app, so this is a deliberate speed bump rather than firing
         on the button click directly. -->
    <Dialog v-model:open="showReconnectConfirm">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle class="flex items-center gap-2">
            <InstagramIcon :size="18" />
            <span>{{ t("integrations.reconnectConfirmTitle") }}</span>
          </DialogTitle>
        </DialogHeader>
        <p class="text-sm text-muted-foreground">{{ t("integrations.reconnectConfirmBody") }}</p>
        <div class="flex items-center justify-end gap-3 mt-2">
          <Button variant="outline" @click="showReconnectConfirm = false">{{ t("integrations.cancel") }}</Button>
          <Button class="gap-2" @click="confirmReconnect">
            <InstagramIcon :size="16" />
            {{ t("integrations.reconnectConfirmProceed") }}
          </Button>
        </div>
      </DialogContent>
    </Dialog>

    <!-- Disconnect Instagram confirmation -->
    <Dialog v-model:open="showDisconnectInstagramConfirm">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle class="flex items-center gap-2 text-destructive">
            <LogOut :size="18" />
            <span>{{ t("integrations.disconnectConfirmTitle") }}</span>
          </DialogTitle>
        </DialogHeader>
        <p class="text-sm text-muted-foreground leading-relaxed">
          {{ t("integrations.disconnectConfirmBody") }}
        </p>
        <div class="flex items-center justify-end gap-3 mt-2">
          <Button variant="outline" :disabled="disconnectingInstagram" @click="showDisconnectInstagramConfirm = false">
            {{ t("integrations.cancel") }}
          </Button>
          <Button variant="destructive" class="gap-2" :disabled="disconnectingInstagram" @click="onDisconnectInstagram">
            <Loader2 v-if="disconnectingInstagram" :size="16" class="animate-spin" />
            <LogOut v-else :size="16" />
            <span>{{ t("integrations.disconnectConfirmProceed") }}</span>
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
