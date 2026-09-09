<script setup lang="ts">
import { Building2, CheckCircle2, AlertCircle, Zap, Coins, Wallet } from "lucide-vue-next";
import type { SuperadminStats, UsagePoint, RevenuePoint } from "~/types/api";
import { formatShortDay, formatShortMonth } from "~/composables/useDateFormat";

definePageMeta({ layout: "superadmin" });

const api = useSuperadminApi();
const { t, locale } = useI18n();

const stats = ref<SuperadminStats | null>(null);
const usage = ref<UsagePoint[]>([]);
const revenue = ref<RevenuePoint[]>([]);
const loading = ref(true);

onMounted(async () => {
  try {
    [stats.value, usage.value, revenue.value] = await Promise.all([
      api.getStats(),
      api.getUsageTimeseries(30),
      api.getRevenueTimeseries(6),
    ]);
  } finally {
    loading.value = false;
  }
});

function fmtInt(v: number) {
  return new Intl.NumberFormat("en-US").format(Math.round(v));
}
function fmtUsd(v: number) {
  return `$${v.toFixed(v < 10 ? 4 : 2)}`;
}
function fmtDay(iso: string) {
  return formatShortDay(iso, locale.value);
}
function fmtMonth(period: string) {
  return formatShortMonth(period, locale.value);
}

const tokenPoints = computed(() => usage.value.map((u) => ({ label: fmtDay(u.date), value: u.tokens })));

// Income can span multiple currencies (manual payments) — chart the dominant
// one (UZS in practice) rather than silently summing incompatible units.
const incomeCurrency = computed(() => {
  const totals: Record<string, number> = {};
  for (const r of revenue.value) {
    for (const [cur, amt] of Object.entries(r.income)) totals[cur] = (totals[cur] || 0) + amt;
  }
  const entries = Object.entries(totals).sort((a, b) => b[1] - a[1]);
  return entries[0]?.[0] ?? "UZS";
});
const incomePoints = computed(() =>
  revenue.value.map((r) => ({ label: fmtMonth(r.period), value: r.income[incomeCurrency.value] ?? 0 }))
);
const expensePoints = computed(() => revenue.value.map((r) => ({ label: fmtMonth(r.period), value: r.expense_usd })));

function fmtMoney(v: number, currency: string) {
  return new Intl.NumberFormat("uz-UZ", { maximumFractionDigits: 0 }).format(v) + " " + currency;
}
</script>

<template>
  <div class="superadmin-dashboard-page">
    <div class="page-header mb-5">
      <h1>{{ t("superadmin.dashboard.title") }}</h1>
      <p>{{ t("superadmin.dashboard.subtitle") }}</p>
    </div>

    <div v-if="loading" class="grid grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
      <Skeleton v-for="i in 6" :key="i" class="h-24 rounded-xl" />
    </div>

    <template v-else-if="stats">
      <!-- Stat tiles -->
      <div class="grid grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
        <Card class="p-4 flex items-center gap-3">
          <div class="card-icon bg-primary/10 text-primary"><Building2 :size="20" /></div>
          <div>
            <div class="text-xs text-muted-foreground font-medium">{{ t("superadmin.dashboard.totalBusinesses") }}</div>
            <div class="text-xl font-bold text-foreground">{{ stats.total_businesses }}</div>
          </div>
        </Card>

        <Card class="p-4 flex items-center gap-3">
          <div class="card-icon bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"><CheckCircle2 :size="20" /></div>
          <div>
            <div class="text-xs text-muted-foreground font-medium">{{ t("superadmin.dashboard.activeBusinesses") }}</div>
            <div class="text-xl font-bold text-foreground">{{ stats.active_businesses }}</div>
          </div>
        </Card>

        <Card class="p-4 flex items-center gap-3">
          <div class="card-icon bg-[var(--warm-soft)] text-[var(--warm)]"><AlertCircle :size="20" /></div>
          <div>
            <div class="text-xs text-muted-foreground font-medium">{{ t("superadmin.dashboard.expiredBusinesses") }}</div>
            <div class="text-xl font-bold text-foreground">{{ stats.expired_businesses }}</div>
          </div>
        </Card>

        <Card class="p-4 flex items-center gap-3">
          <div class="card-icon bg-primary/10 text-primary"><Zap :size="20" /></div>
          <div>
            <div class="text-xs text-muted-foreground font-medium">{{ t("superadmin.dashboard.tokensToday") }}</div>
            <div class="text-xl font-bold text-foreground">{{ fmtInt(stats.tokens_today) }}</div>
          </div>
        </Card>

        <Card class="p-4 flex items-center gap-3">
          <div class="card-icon bg-muted text-muted-foreground"><Coins :size="20" /></div>
          <div>
            <div class="text-xs text-muted-foreground font-medium">{{ t("superadmin.dashboard.costToday") }}</div>
            <div class="text-xl font-bold text-foreground">{{ fmtUsd(stats.cost_today_usd) }}</div>
          </div>
        </Card>

        <Card class="p-4 flex items-center gap-3">
          <div class="card-icon bg-primary/10 text-primary"><Wallet :size="20" /></div>
          <div>
            <div class="text-xs text-muted-foreground font-medium">{{ t("superadmin.dashboard.balance") }}</div>
            <div class="text-xl font-bold text-foreground">
              {{ stats.openrouter_balance_usd !== null ? fmtUsd(stats.openrouter_balance_usd) : t("superadmin.dashboard.balanceUnavailable") }}
            </div>
          </div>
        </Card>
      </div>

      <!-- Token usage over time -->
      <Card class="p-5 mb-5">
        <h3 class="text-sm font-semibold text-foreground mb-4">{{ t("superadmin.dashboard.tokenUsageChartTitle") }}</h3>
        <LineChart :data="tokenPoints" :value-formatter="(v) => `${fmtInt(v)} token`" />
      </Card>

      <!-- Income / expense — separate charts: different units (UZS vs USD),
           never a dual-axis combo (see dataviz "one axis" rule). -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card class="p-5">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-semibold text-foreground">{{ t("superadmin.dashboard.incomeChartTitle") }}</h3>
            <span class="text-xs font-semibold text-emerald-600 dark:text-emerald-400">
              {{ fmtMoney(stats.income_this_month[incomeCurrency] ?? 0, incomeCurrency) }} / {{ t('superadmin.dashboard.incomeThisMonth').toLowerCase() }}
            </span>
          </div>
          <BarChart
            :data="incomePoints"
            color="#10b981"
            :value-formatter="(v) => fmtMoney(v, incomeCurrency)"
          />
        </Card>

        <Card class="p-5">
          <div class="flex items-center justify-between mb-4">
            <h3 class="text-sm font-semibold text-foreground">{{ t("superadmin.dashboard.expenseChartTitle") }}</h3>
            <span class="text-xs font-semibold text-muted-foreground">
              {{ fmtUsd(stats.cost_this_month_usd) }} / {{ t('superadmin.dashboard.costThisMonth').toLowerCase() }}
            </span>
          </div>
          <BarChart
            :data="expensePoints"
            color="hsl(var(--muted-foreground))"
            :value-formatter="(v) => fmtUsd(v)"
          />
        </Card>
      </div>
    </template>
  </div>
</template>
