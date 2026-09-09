<script setup lang="ts">
import {
  Bell,
  Flame,
  Sparkles,
  Info,
  Check,
  CheckCheck,
  Trash2,
  ExternalLink,
  Phone,
  User,
  CheckCircle2,
  Inbox,
  Filter,
} from "lucide-vue-next";
import type { NotificationItem } from "~/types/api";

definePageMeta({
  layout: "dashboard",
});

const router = useRouter();
const { t } = useI18n();
const { locale } = useUserPreferences();
const {
  notifications,
  unreadCount,
  isLoading,
  markAsRead,
  markAllAsRead,
  deleteNotification,
  deleteReadNotifications,
  fetchNotifications,
} = useNotifications();

type FilterTab = "all" | "unread" | "hot";
const currentTab = ref<FilterTab>("all");
const isDeletingRead = ref(false);

onMounted(() => {
  fetchNotifications();
});

const filteredNotifications = computed(() => {
  if (currentTab.value === "unread") {
    return notifications.value.filter((n) => !n.is_read);
  }
  if (currentTab.value === "hot") {
    return notifications.value.filter(
      (n) => n.type === "lead_hot" || n.type === "lead_warm"
    );
  }
  return notifications.value;
});

const hotCount = computed(() => {
  return notifications.value.filter(
    (n) => n.type === "lead_hot" || n.type === "lead_warm"
  ).length;
});

const readCount = computed(() => {
  return notifications.value.filter((n) => n.is_read).length;
});

async function handleClearRead() {
  if (readCount.value === 0 || isDeletingRead.value) return;
  isDeletingRead.value = true;
  try {
    await deleteReadNotifications();
  } finally {
    isDeletingRead.value = false;
  }
}

async function handleItemClick(item: NotificationItem) {
  if (!item.is_read) {
    await markAsRead(item.id);
  }
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

    return date.toLocaleDateString(
      locale.value === "uz" ? "uz-UZ" : locale.value === "ru" ? "ru-RU" : "en-US",
      {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }
    );
  } catch {
    return "";
  }
}
</script>

<template>
  <div class="notifications-page max-w-7xl mx-auto pb-12 space-y-3">
    <!-- Filter Tabs & Actions Bar -->
    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-border pb-3">
      <!-- Left: Filter Tabs -->
      <div class="flex items-center gap-2 overflow-x-auto">
        <button
          type="button"
          class="px-3.5 py-1.5 rounded-lg text-xs font-medium flex items-center gap-2 transition-colors cursor-pointer shrink-0"
          :class="[
            currentTab === 'all'
              ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
          ]"
          @click="currentTab = 'all'"
        >
          <span>{{ t("notifications.allTab") }}</span>
          <span
            class="px-1.5 py-0.2 rounded-full text-[10px]"
            :class="currentTab === 'all' ? 'bg-primary-foreground/20 text-primary-foreground' : 'bg-muted text-muted-foreground'"
          >
            {{ notifications.length }}
          </span>
        </button>

        <button
          type="button"
          class="px-3.5 py-1.5 rounded-lg text-xs font-medium flex items-center gap-2 transition-colors cursor-pointer shrink-0"
          :class="[
            currentTab === 'unread'
              ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
          ]"
          @click="currentTab = 'unread'"
        >
          <span>{{ t("notifications.unreadTab") }}</span>
          <span
            v-if="unreadCount > 0"
            class="px-1.5 py-0.2 rounded-full text-[10px] font-bold"
            :class="currentTab === 'unread' ? 'bg-primary-foreground/20 text-primary-foreground' : 'bg-red-500/20 text-red-600 dark:text-red-400'"
          >
            {{ unreadCount }}
          </span>
          <span
            v-else
            class="px-1.5 py-0.2 rounded-full text-[10px]"
            :class="currentTab === 'unread' ? 'bg-primary-foreground/20 text-primary-foreground' : 'bg-muted text-muted-foreground'"
          >
            0
          </span>
        </button>

        <button
          type="button"
          class="px-3.5 py-1.5 rounded-lg text-xs font-medium flex items-center gap-2 transition-colors cursor-pointer shrink-0"
          :class="[
            currentTab === 'hot'
              ? 'bg-primary text-primary-foreground font-semibold shadow-xs'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
          ]"
          @click="currentTab = 'hot'"
        >
          <span>{{ t("notifications.hotLeadsTab") }}</span>
          <span
            class="px-1.5 py-0.2 rounded-full text-[10px]"
            :class="currentTab === 'hot' ? 'bg-primary-foreground/20 text-primary-foreground' : 'bg-muted text-muted-foreground'"
          >
            {{ hotCount }}
          </span>
        </button>
      </div>

      <!-- Right: Action Buttons (Tozalash & Barchasini o'qish) -->
      <div class="flex items-center gap-2 shrink-0 self-end sm:self-center">
        <Button
          v-if="unreadCount > 0"
          variant="outline"
          size="sm"
          class="h-8 gap-1.5 text-xs font-medium cursor-pointer"
          @click="markAllAsRead"
        >
          <CheckCheck :size="14" class="text-primary" />
          <span>{{ t("notifications.markAllRead") }}</span>
        </Button>

        <Button
          v-if="readCount > 0"
          variant="ghost"
          size="sm"
          class="h-8 gap-1.5 text-xs text-muted-foreground hover:text-destructive hover:bg-destructive/10 cursor-pointer"
          :disabled="isDeletingRead"
          @click="handleClearRead"
        >
          <Trash2 :size="14" />
          <span>{{ t("notifications.clearRead") }}</span>
        </Button>
      </div>
    </div>

    <!-- Notification Feed Cards -->
    <div v-if="filteredNotifications.length > 0" class="space-y-3">
      <div
        v-for="item in filteredNotifications"
        :key="item.id"
        class="group p-4 rounded-xl border transition-all flex flex-col sm:flex-row sm:items-start justify-between gap-4"
        :class="[
          !item.is_read
            ? 'bg-card border-primary/40 shadow-xs ring-1 ring-primary/20'
            : 'bg-card/70 border-border hover:bg-card hover:border-border/80'
        ]"
      >
        <!-- Main Info Left -->
        <div class="flex items-start gap-3.5 min-w-0 flex-1">
          <!-- Icon -->
          <div
            :class="[
              'w-9 h-9 rounded-xl flex items-center justify-center shrink-0 mt-0.5',
              item.type === 'lead_hot'
                ? 'bg-amber-500/15 text-amber-500 dark:bg-amber-500/25'
                : item.type === 'lead_warm'
                ? 'bg-yellow-500/15 text-yellow-600 dark:text-yellow-400'
                : item.type === 'lead_updated'
                ? 'bg-blue-500/15 text-blue-500'
                : 'bg-primary/15 text-primary'
            ]"
          >
            <Flame v-if="item.type === 'lead_hot' || item.type === 'lead_warm'" :size="18" />
            <Sparkles v-else-if="item.type === 'lead_updated'" :size="18" />
            <Info v-else :size="18" />
          </div>

          <!-- Content -->
          <div class="min-w-0 flex-1 space-y-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-bold text-sm text-foreground">
                {{ item.title }}
              </span>
              <span
                v-if="!item.is_read"
                class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-primary/20 text-primary uppercase tracking-wider"
              >
                {{ t("notifications.unread") }}
              </span>
              <span class="text-xs text-muted-foreground">
                {{ formatTime(item.created_at) }}
              </span>
            </div>

            <p class="text-xs sm:text-sm text-foreground/90 leading-relaxed break-words">
              {{ item.message }}
            </p>

            <!-- Metadata Pills if available -->
            <div
              v-if="item.extra_metadata && Object.keys(item.extra_metadata).length > 0"
              class="flex flex-wrap items-center gap-2 pt-1"
            >
              <span
                v-if="item.extra_metadata.username"
                class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-muted text-[11px] text-muted-foreground font-mono"
              >
                <User :size="12" />
                @{{ item.extra_metadata.username }}
              </span>
              <span
                v-if="item.extra_metadata.phone"
                class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-500/10 text-[11px] text-emerald-600 dark:text-emerald-400 font-mono font-medium"
              >
                <Phone :size="12" />
                {{ item.extra_metadata.phone }}
              </span>
              <span
                v-if="item.extra_metadata.score"
                class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-500/10 text-[11px] text-amber-600 dark:text-amber-400 font-medium"
              >
                Score: {{ item.extra_metadata.score }}
              </span>
            </div>
          </div>
        </div>

        <!-- Action Buttons Right -->
        <div class="flex items-center gap-1.5 self-end sm:self-center shrink-0 pt-2 sm:pt-0">
          <Button
            v-if="item.lead_id || item.type.startsWith('lead')"
            variant="outline"
            size="sm"
            class="h-8 gap-1 text-xs cursor-pointer"
            @click="handleItemClick(item)"
          >
            <span>{{ t("notifications.viewAllLeads") }}</span>
            <ExternalLink :size="12" />
          </Button>

          <Button
            v-if="!item.is_read"
            variant="ghost"
            size="icon"
            class="h-8 w-8 text-muted-foreground hover:text-primary hover:bg-primary/10 cursor-pointer"
            :title="t('notifications.markRead')"
            @click="markAsRead(item.id)"
          >
            <Check :size="15" />
          </Button>

          <Button
            variant="ghost"
            size="icon"
            class="h-8 w-8 text-muted-foreground hover:text-destructive hover:bg-destructive/10 cursor-pointer opacity-70 group-hover:opacity-100 transition-opacity"
            :title="t('notifications.delete')"
            @click="deleteNotification(item.id)"
          >
            <Trash2 :size="14" />
          </Button>
        </div>
      </div>
    </div>

    <!-- Empty State -->
    <div
      v-else-if="!isLoading"
      class="py-16 text-center border border-dashed border-border rounded-2xl bg-card/40 flex flex-col items-center justify-center gap-3"
    >
      <div class="w-12 h-12 rounded-full bg-muted flex items-center justify-center text-muted-foreground">
        <CheckCircle2 v-if="currentTab === 'unread'" :size="24" class="text-emerald-500" />
        <Inbox v-else :size="24" />
      </div>
      <div class="space-y-1">
        <h3 class="font-semibold text-sm text-foreground">
          {{ currentTab === "unread" ? t("notifications.allCaughtUp") : t("notifications.noItems") }}
        </h3>
        <p class="text-xs text-muted-foreground max-w-sm">
          {{ currentTab === "unread" ? t("notifications.noUnread") : t("notifications.pageSubtitle") }}
        </p>
      </div>
    </div>

    <!-- Loading State -->
    <div v-if="isLoading && notifications.length === 0" class="py-16 text-center text-xs text-muted-foreground">
      {{ t("common.loading") }}
    </div>
  </div>
</template>
