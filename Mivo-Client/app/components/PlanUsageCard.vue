<script setup lang="ts">
import { Gauge } from "@lucide/vue";
import { formatFullDate } from "~/composables/useDateFormat";

// `compact` is the sidebar version: the same facts, less padding.
const props = withDefaults(defineProps<{ compact?: boolean }>(), { compact: false });

const { usage, refresh } = useBillingUsage();
const { t, locale } = useI18n();

onMounted(refresh);

const fmt = (n: number) => new Intl.NumberFormat("uz-UZ").format(n);
const fmtDay = (iso: string) => formatFullDate(iso, locale.value);

const percent = computed(() => {
  const u = usage.value;
  if (!u || !u.ai_replies_limit) return 0;
  return Math.min(100, Math.round((u.ai_replies_used / u.ai_replies_limit) * 100));
});

// One status line, the most urgent that applies.
const status = computed<{ text: string; tone: "danger" | "warn" } | null>(() => {
  const u = usage.value;
  if (!u) return null;
  if (!u.subscription_active) return { text: t("billing.expired"), tone: "danger" };
  const { ai_replies_used: used, ai_replies_limit: limit, ai_replies_hard_limit: hard } = u;
  if (limit === null || hard === null) return null;
  if (used >= hard) return { text: t("billing.stopped"), tone: "danger" };
  if (used >= limit) return { text: t("billing.reached", { left: fmt(hard - used) }), tone: "warn" };
  if (used >= limit * 0.8) return { text: t("billing.near"), tone: "warn" };
  return null;
});

const barTone = computed(() =>
  status.value?.tone === "danger" ? "bg-destructive" : status.value?.tone === "warn" ? "bg-orange-500" : "bg-primary"
);
</script>

<template>
  <div
    v-if="usage"
    class="rounded-xl border border-border bg-card text-card-foreground"
    :class="props.compact ? 'p-3 text-xs' : 'p-4 text-sm'"
  >
    <div class="flex items-center justify-between gap-2">
      <div class="flex items-center gap-2 min-w-0">
        <Gauge :size="props.compact ? 14 : 16" class="text-muted-foreground shrink-0" aria-hidden="true" />
        <span class="text-muted-foreground">{{ t("billing.title") }}</span>
      </div>
      <span class="font-semibold text-foreground truncate">{{ usage.plan?.name ?? t("billing.noPlan") }}</span>
    </div>

    <template v-if="usage.ai_replies_limit !== null">
      <div
        class="mt-2.5 h-1.5 w-full rounded-full bg-muted overflow-hidden"
        role="progressbar"
        :aria-valuenow="usage.ai_replies_used"
        :aria-valuemin="0"
        :aria-valuemax="usage.ai_replies_limit"
      >
        <div class="h-full rounded-full transition-[width]" :class="barTone" :style="{ width: `${percent}%` }" />
      </div>
      <p class="mt-2 text-foreground">
        {{ t("billing.used", { used: fmt(usage.ai_replies_used), limit: fmt(usage.ai_replies_limit) }) }}
      </p>
    </template>
    <p v-else class="mt-2 text-foreground">
      {{ t("billing.usedUnlimited", { used: fmt(usage.ai_replies_used) }) }} · {{ t("billing.unlimited") }}
    </p>

    <p
      v-if="status"
      class="mt-1.5 font-medium"
      :class="status.tone === 'danger' ? 'text-destructive' : 'text-orange-600 dark:text-orange-400'"
    >
      {{ status.text }}
    </p>

    <p class="mt-1.5 text-muted-foreground">
      <template v-if="usage.ai_replies_limit !== null">{{ t("billing.resets", { date: fmtDay(usage.period_end) }) }}<br /></template>
      <template v-if="usage.subscription_expires_at && usage.subscription_active">
        {{ t("billing.expires", { date: formatFullDate(usage.subscription_expires_at, locale) }) }}
      </template>
    </p>

    <p v-if="usage.billing_contact && (status || !props.compact)" class="mt-1.5 text-muted-foreground break-words">
      {{ t("billing.contact", { contact: usage.billing_contact }) }}
    </p>
  </div>
</template>
