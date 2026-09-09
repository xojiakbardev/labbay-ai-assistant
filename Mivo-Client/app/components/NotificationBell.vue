<script setup lang="ts">
import {
  Bell,
  Flame,
  Sparkles,
  Info,
  Check,
  CheckCheck,
  CheckCircle2,
  ExternalLink,
} from "lucide-vue-next";
import type { NotificationItem } from "~/types/api";

const router = useRouter();
const { t } = useI18n();
const { locale } = useUserPreferences();
const {
  notifications,
  unreadNotifications,
  unreadCount,
  isLoading,
  markAsRead,
  markAllAsRead,
  fetchNotifications,
  startBackgroundSync,
  stopBackgroundSync,
} = useNotifications();

const isOpen = ref(false);
const bellContainerRef = ref<HTMLElement | null>(null);

onMounted(() => {
  startBackgroundSync();
  if (import.meta.client) {
    document.addEventListener("click", handleClickOutside);
  }
});

onUnmounted(() => {
  stopBackgroundSync();
  if (import.meta.client) {
    document.removeEventListener("click", handleClickOutside);
  }
});

function toggleDropdown() {
  isOpen.value = !isOpen.value;
  if (isOpen.value) {
    fetchNotifications();
  }
}

function handleClickOutside(event: MouseEvent) {
  if (bellContainerRef.value && !bellContainerRef.value.contains(event.target as Node)) {
    isOpen.value = false;
  }
}

async function handleNotificationClick(item: NotificationItem) {
  if (!item.is_read) {
    await markAsRead(item.id);
  }
  isOpen.value = false;

  if (item.lead_id) {
    router.push({ path: "/leads", query: { id: item.lead_id } });
  } else if (item.type.startsWith("lead")) {
    router.push("/leads");
  }
}

function formatTime(isoStr: string): string {
  try {
    const date = new Date(isoStr);
    const now = new Date();
    const diffSec = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (diffSec < 60) return t("notifications.justNow");
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return t("notifications.minAgo", { min: diffMin });
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return t("notifications.hourAgo", { hour: diffHour });
    const diffDay = Math.floor(diffHour / 24);
    if (diffDay < 7) return t("notifications.dayAgo", { day: diffDay });

    return date.toLocaleDateString(locale.value === "uz" ? "uz-UZ" : locale.value === "ru" ? "ru-RU" : "en-US", {
      month: "short",
      day: "numeric",
    });
  } catch {
    return "";
  }
}
</script>

<template>
  <div ref="bellContainerRef" class="relative inline-block">
    <!-- Bell Button -->
    <Button
      variant="outline"
      size="icon"
      class="relative h-9 w-9 rounded-xl border-border bg-card hover:bg-muted text-foreground transition-all cursor-pointer shadow-2xs"
      :title="`Bildirishnomalar (${unreadCount} o'qilmagan)`"
      @click="toggleDropdown"
    >
      <Bell :size="16" class="text-foreground" />

      <!-- Unread Badge Counter -->
      <span
        v-if="unreadCount > 0"
        class="absolute -top-1 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white shadow-sm ring-2 ring-background animate-in zoom-in"
      >
        {{ unreadCount > 99 ? "99+" : unreadCount }}
      </span>
    </Button>

    <!-- Dropdown Popover Panel -->
    <transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="transform scale-95 opacity-0"
      enter-to-class="transform scale-100 opacity-100"
      leave-active-class="transition duration-100 ease-in"
      leave-from-class="transform scale-100 opacity-100"
      leave-to-class="transform scale-95 opacity-0"
    >
      <div
        v-if="isOpen"
        class="absolute right-0 mt-2 w-[calc(100vw-2rem)] sm:w-96 max-w-sm rounded-2xl border border-border bg-card text-card-foreground shadow-2xl z-50 overflow-hidden"
      >
        <!-- Header -->
        <div class="flex items-center justify-between px-4 py-3 border-b border-border bg-muted/40">
          <div class="flex items-center gap-2">
            <span class="font-semibold text-sm">{{ t("notifications.title") }}</span>
            <span
              v-if="unreadCount > 0"
              class="px-2 py-0.5 text-[11px] font-semibold rounded-full bg-primary/15 text-primary"
            >
              {{ t("notifications.newCount", { count: unreadCount }) }}
            </span>
          </div>

          <button
            v-if="unreadCount > 0"
            type="button"
            class="text-[11px] font-medium text-muted-foreground hover:text-foreground flex items-center gap-1 transition-colors cursor-pointer"
            @click.stop="markAllAsRead"
          >
            <CheckCheck :size="13" />
            <span>{{ t("notifications.markAllRead") }}</span>
          </button>
        </div>

        <!-- Notification List (Only Unread) -->
        <div class="max-h-[380px] overflow-y-auto divide-y divide-border/60">
          <div
            v-for="item in unreadNotifications"
            :key="item.id"
            class="p-3.5 flex items-start gap-3 cursor-pointer transition-colors bg-primary/[0.08] dark:bg-primary/[0.16] hover:bg-primary/[0.12] dark:hover:bg-primary/[0.22] border-l-4 border-l-primary"
            @click="handleNotificationClick(item)"
          >
            <!-- Type Icon -->
            <div
              :class="[
                'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5',
                item.type === 'lead_hot'
                  ? 'bg-amber-500/15 text-amber-500 dark:bg-amber-500/25'
                  : item.type === 'lead_warm'
                  ? 'bg-yellow-500/15 text-yellow-600 dark:text-yellow-400'
                  : item.type === 'lead_updated'
                  ? 'bg-blue-500/15 text-blue-500'
                  : 'bg-primary/15 text-primary'
              ]"
            >
              <Flame v-if="item.type === 'lead_hot' || item.type === 'lead_warm'" :size="16" />
              <Sparkles v-else-if="item.type === 'lead_updated'" :size="16" />
              <Info v-else :size="16" />
            </div>

            <!-- Content -->
            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between gap-1 mb-1">
                <div class="flex items-center gap-1.5 min-w-0">
                  <span class="text-xs truncate font-bold text-foreground">
                    {{ item.title }}
                  </span>
                  <span
                    class="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-primary/20 text-primary shrink-0 leading-none"
                  >
                    {{ t("notifications.unread") }}
                  </span>
                </div>
                <span class="text-[10px] text-muted-foreground shrink-0">
                  {{ formatTime(item.created_at) }}
                </span>
              </div>
              <p class="text-xs line-clamp-2 leading-relaxed text-foreground/85">
                {{ item.message }}
              </p>
            </div>

            <!-- Actions & Indicator -->
            <div class="flex items-center gap-1 shrink-0 pt-0.5">
              <button
                type="button"
                class="p-1 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/15 transition-all cursor-pointer"
                :title="t('notifications.markRead')"
                @click.stop="markAsRead(item.id)"
              >
                <Check :size="14" />
              </button>
              <span class="relative flex h-2 w-2 shrink-0">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
                <span class="relative inline-flex rounded-full h-2 w-2 bg-primary"></span>
              </span>
            </div>
          </div>

          <!-- Empty State -->
          <div
            v-if="unreadNotifications.length === 0 && !isLoading"
            class="py-10 px-4 text-center text-muted-foreground flex flex-col items-center justify-center gap-2"
          >
            <CheckCircle2 :size="28" class="text-emerald-500/70" />
            <p class="text-xs font-medium">{{ t("notifications.noUnread") }}</p>
          </div>

          <!-- Loading State -->
          <div v-if="isLoading && unreadNotifications.length === 0" class="py-8 text-center text-xs text-muted-foreground">
            {{ t("common.loading") }}
          </div>
        </div>

        <!-- Footer: Link to all notifications page -->
        <div class="p-2.5 border-t border-border bg-muted/20 text-center">
          <NuxtLink
            to="/notifications"
            class="text-xs font-semibold text-primary hover:underline flex items-center justify-center gap-1.5 py-1 transition-colors"
            @click="isOpen = false"
          >
            <span>{{ t("notifications.viewAll") }}</span>
            <ExternalLink :size="13" />
          </NuxtLink>
        </div>
      </div>
    </transition>
  </div>
</template>
