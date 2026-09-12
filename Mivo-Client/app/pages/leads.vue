<script setup lang="ts">
import { toast } from "vue-sonner";
import type { Lead } from "~/types/api";
import {
  Flame,
  UserCheck,
  Users,
  Phone,
  Package,
  Sparkles,
  MessageSquare,
  Search,
  Download,
  Copy,
  Check,
  Calendar,
  Eye,
  X,
  PhoneCall,
  SlidersHorizontal,
  AlertCircle,
  RefreshCw
} from "@lucide/vue";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from "~/components/ui/select";
import MobileDrawer from "~/components/MobileDrawer.vue";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t } = useI18n();
const router = useRouter();

const leads = ref<Lead[]>([]);
const loading = ref(true);
const loadError = ref<string | null>(null);

// Filters & Controls
const searchQuery = ref("");
const selectedStatus = ref<"all" | "hot" | "warm" | "cold">("all");
const onlyWithPhone = ref(false);
const sortBy = ref<"score_desc" | "score_asc" | "date_desc" | "date_asc">("score_desc");
const showMobileFilterDrawer = ref(false);

const activeFiltersCount = computed(() => {
  let count = 0;
  if (selectedStatus.value !== "all") count++;
  if (onlyWithPhone.value) count++;
  if (sortBy.value !== "score_desc") count++;
  return count;
});

function resetFilters() {
  selectedStatus.value = "all";
  onlyWithPhone.value = false;
  sortBy.value = "score_desc";
  searchQuery.value = "";
}

// Selected Lead Modal
const selectedLead = ref<Lead | null>(null);
const copiedPhone = ref<string | null>(null);
const copiedAllPhones = ref(false);

const route = useRoute();

async function loadLeads() {
  loading.value = true;
  loadError.value = null;
  try {
    leads.value = await api.listLeads(500, 0);
    if (route.query.id) {
      const match = leads.value.find((l) => l.id === route.query.id);
      if (match) {
        selectedLead.value = match;
      }
    }
  } catch (err) {
    console.error("Failed to load leads", err);
    loadError.value = err instanceof Error && err.message ? err.message : t("leads.loadError");
  } finally {
    loading.value = false;
  }
}

onMounted(loadLeads);

watch(
  () => route.query.id,
  (newId) => {
    if (newId && leads.value.length) {
      const match = leads.value.find((l) => l.id === newId);
      if (match) {
        selectedLead.value = match;
      }
    }
  }
);

// KPI Metrics
const totalCount = computed(() => leads.value.length);
const hotCount = computed(() => leads.value.filter((l) => l.status === "hot").length);
const warmCount = computed(() => leads.value.filter((l) => l.status === "warm").length);
const coldCount = computed(() => leads.value.filter((l) => l.status === "cold").length);
const phoneCount = computed(() => leads.value.filter((l) => !!l.phone).length);

// Filtered & Sorted Leads
const filteredLeads = computed(() => {
  let result = [...leads.value];

  if (selectedStatus.value !== "all") {
    result = result.filter((l) => l.status === selectedStatus.value);
  }

  if (onlyWithPhone.value) {
    result = result.filter((l) => !!l.phone && l.phone.trim().length > 0);
  }

  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase().trim();
    result = result.filter((l) => {
      const username = (l.customer_username || "").toLowerCase();
      const phone = (l.phone || "").toLowerCase();
      const reason = (l.qualification_reason || "").toLowerCase();
      const summary = (l.summary || "").toLowerCase();
      const products = (l.interested_products || []).map((p) => (p.name || "").toLowerCase()).join(" ");
      return (
        username.includes(q) ||
        phone.includes(q) ||
        reason.includes(q) ||
        summary.includes(q) ||
        products.includes(q)
      );
    });
  }

  result.sort((a, b) => {
    if (sortBy.value === "score_desc") return b.score - a.score;
    if (sortBy.value === "score_asc") return a.score - b.score;
    if (sortBy.value === "date_desc") {
      return new Date(b.updated_at || b.created_at).getTime() - new Date(a.updated_at || a.created_at).getTime();
    }
    if (sortBy.value === "date_asc") {
      return new Date(a.updated_at || a.created_at).getTime() - new Date(b.updated_at || b.created_at).getTime();
    }
    return 0;
  });

  return result;
});

const { locale } = useI18n();

// The AI writes `summaries` only in the locales it managed to (uz always):
// the viewer's language, else Uzbek, else the plain summary.
function formatAiReason(lead: Lead | null | undefined): string {
  if (!lead) return "";
  // `||`, not `??`: an empty string is "not written" too.
  const reason =
    lead.summaries?.[locale.value] || lead.summaries?.uz || lead.summary || lead.qualification_reason;
  if (!reason) return t("leads.reasonAnalyzing");
  // Fixed English reasons the backend writes for two special cases.
  if (reason.includes("initiated conversation with a general greeting")) return t("leads.reasonGreeting");
  if (reason.includes("AI provider failed") || reason.includes("escalated to human")) return t("leads.reasonEscalated");
  return reason;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    const day = d.getDate();
    const loc = locale.value === "uz" ? "uz-UZ" : locale.value === "ru" ? "ru-RU" : "en-US";
    const m = d.toLocaleDateString(loc, { month: "short" });
    const hours = String(d.getHours()).padStart(2, "0");
    const mins = String(d.getMinutes()).padStart(2, "0");
    return `${day}-${m}, ${hours}:${mins}`;
  } catch {
    return "—";
  }
}

// Only offered when the lead still has a conversation (deleting a
// conversation keeps the lead and clears the link).
function openLeadChat(lead: Lead) {
  if (!lead.conversation_id) return;
  router.push({ path: "/", query: { id: lead.conversation_id } });
}

async function copyPhone(phone: string) {
  try {
    await navigator.clipboard.writeText(phone);
    copiedPhone.value = phone;
    setTimeout(() => {
      copiedPhone.value = null;
    }, 2000);
  } catch (err) {
    console.error("Clipboard write failed", err);
    toast.error(t("common.copyFailed"));
  }
}

async function copyAllPhoneNumbers() {
  const phones = filteredLeads.value
    .map((l) => l.phone)
    .filter((p): p is string => !!p && p.trim().length > 0);

  if (phones.length === 0) return;

  try {
    await navigator.clipboard.writeText(phones.join("\n"));
    copiedAllPhones.value = true;
    setTimeout(() => {
      copiedAllPhones.value = false;
    }, 2500);
  } catch (err) {
    console.error("Clipboard write failed", err);
    toast.error(t("common.copyFailed"));
  }
}

// Every field quoted with inner quotes doubled. Customer-written text that
// starts like a formula (= + - @) gets a leading apostrophe so a spreadsheet
// shows it instead of evaluating it.
function csvField(value: string | number, guardFormula = true): string {
  let v = String(value ?? "");
  if (guardFormula && /^[=+\-@\t\r]/.test(v)) v = `'${v}`;
  return `"${v.replace(/"/g, '""')}"`;
}

function exportCsv() {
  const headers = [
    t("leads.csvCustomer"),
    t("leads.csvStatus"),
    t("leads.csvScore"),
    t("leads.csvPhone"),
    t("leads.csvProducts"),
    t("leads.csvSummary"),
    t("leads.csvDate"),
  ];
  const rows = filteredLeads.value.map((l) => [
    csvField(l.customer_username || t("leads.customer")),
    csvField(l.status),
    csvField(l.score, false),
    csvField(l.phone || "", false),
    csvField((l.interested_products || []).map((p) => p.name).join(", ")),
    csvField(formatAiReason(l)),
    csvField(l.updated_at || l.created_at, false),
  ]);

  // BOM so Excel opens the UTF-8 (Cyrillic/Uzbek) text correctly; a Blob URL
  // rather than a data: URI, which breaks on '#' and on large exports.
  const csv = [headers.map((h) => csvField(h, false)).join(","), ...rows.map((r) => r.join(","))].join("\r\n");
  const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `mivo_leads_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
</script>

<template>
  <div class="leads-page max-w-7xl mx-auto pb-12 space-y-3">

    <!-- Top Search & Actions Bar -->
    <div class="rounded-xl border border-border bg-card px-3.5 py-2.5 shadow-xs">
      <div class="flex items-center justify-between gap-3">
        <!-- Search Input -->
        <div class="relative flex-1 max-w-md">
          <Search :size="15" class="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
          <Input
            v-model="searchQuery"
            type="text"
            :placeholder="t('leads.searchPlaceholder')"
            class="w-full h-9 pl-9 pr-3 text-sm bg-background"
          />
        </div>

        <!-- Right Side: Filters Drawer Trigger + CSV Export -->
        <div class="flex items-center gap-2 shrink-0">
          <Button
            variant="outline"
            size="sm"
            class="h-9 px-3 text-xs gap-1.5 whitespace-nowrap cursor-pointer shadow-none"
            :class="{ 'border-primary text-primary bg-primary/5': activeFiltersCount > 0 }"
            @click="showMobileFilterDrawer = true"
          >
            <SlidersHorizontal :size="14" />
            <span>{{ t("leads.filters") }}</span>
            <span
              v-if="activeFiltersCount > 0"
              class="w-4 h-4 -mr-0.5 rounded-full bg-primary text-primary-foreground text-[10px] font-bold flex items-center justify-center"
            >
              {{ activeFiltersCount }}
            </span>
          </Button>

          <Button
            variant="outline"
            size="sm"
            class="h-9 px-3 text-xs gap-1.5 whitespace-nowrap cursor-pointer shadow-none"
            :disabled="filteredLeads.length === 0"
            :title="t('leads.exportCsvTitle')"
            :aria-label="t('leads.exportCsvTitle')"
            @click="exportCsv"
          >
            <Download :size="14" />
            <span class="hidden sm:inline">{{ t("leads.exportCsv") }}</span>
          </Button>
        </div>
      </div>

      <!-- Active Filter Pills (if any filter is active) -->
      <div v-if="activeFiltersCount > 0" class="flex flex-wrap items-center gap-1.5 mt-2.5 pt-2 border-t border-border/60">
        <span class="text-[11px] text-muted-foreground font-medium mr-1">{{ t("products.activeFilterLabel") }}</span>
        <span
          v-if="selectedStatus !== 'all'"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-primary/10 text-primary border border-primary/20"
        >
          <span>{{ selectedStatus === 'hot' ? '🔥 ' + t('leads.hot') : selectedStatus === 'warm' ? '👤 ' + t('leads.warm') : '👥 ' + t('leads.cold') }}</span>
          <button type="button" class="cursor-pointer" @click="selectedStatus = 'all'"><X :size="11" /></button>
        </span>
        <span
          v-if="onlyWithPhone"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-500 border border-emerald-500/20"
        >
          <span>📞 {{ t("leads.onlyWithPhone") }}</span>
          <button type="button" class="cursor-pointer" @click="onlyWithPhone = false"><X :size="11" /></button>
        </span>
        <span
          v-if="sortBy !== 'score_desc'"
          class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-muted text-foreground border border-border"
        >
          <span>{{ sortBy === 'score_asc' ? t('leads.sortScoreAsc') : sortBy === 'date_desc' ? t('leads.sortDateDesc') : t('leads.sortDateAsc') }}</span>
          <button type="button" class="cursor-pointer" @click="sortBy = 'score_desc'"><X :size="11" /></button>
        </span>
        <button
          type="button"
          class="text-[11px] text-muted-foreground hover:text-foreground underline ml-auto cursor-pointer"
          @click="resetFilters"
        >
          {{ t("common.clear") }}
        </button>
      </div>
    </div>

    <!-- KPI Summary Metrics Grid (Desktop only) -->
    <div class="hidden md:grid md:grid-cols-4 gap-3">
      <!-- Hot Leads -->
      <Card
        class="p-4 cursor-pointer hover:border-rose-500/40 transition-colors border-border shadow-xs gap-0"
        role="button"
        tabindex="0"
        :class="{ 'ring-2 ring-rose-500/30 border-rose-500/50 bg-rose-500/5': selectedStatus === 'hot' }"
        :aria-pressed="selectedStatus === 'hot'"
        @click="selectedStatus = selectedStatus === 'hot' ? 'all' : 'hot'"
        @keydown.enter.space.prevent="selectedStatus = selectedStatus === 'hot' ? 'all' : 'hot'"
      >
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{{ t("leads.hot") }}</span>
          <div class="h-7 w-7 rounded-md bg-rose-500/10 text-rose-500 flex items-center justify-center">
            <Flame :size="16" />
          </div>
        </div>
        <div class="text-2xl font-bold text-foreground">{{ hotCount }}</div>
      </Card>

      <!-- Warm Leads -->
      <Card
        class="p-4 cursor-pointer hover:border-amber-500/40 transition-colors border-border shadow-xs gap-0"
        role="button"
        tabindex="0"
        :class="{ 'ring-2 ring-amber-500/30 border-amber-500/50 bg-amber-500/5': selectedStatus === 'warm' }"
        :aria-pressed="selectedStatus === 'warm'"
        @click="selectedStatus = selectedStatus === 'warm' ? 'all' : 'warm'"
        @keydown.enter.space.prevent="selectedStatus = selectedStatus === 'warm' ? 'all' : 'warm'"
      >
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{{ t("leads.warm") }}</span>
          <div class="h-7 w-7 rounded-md bg-amber-500/10 text-amber-500 flex items-center justify-center">
            <UserCheck :size="16" />
          </div>
        </div>
        <div class="text-2xl font-bold text-foreground">{{ warmCount }}</div>
      </Card>

      <!-- Cold Leads -->
      <Card
        class="p-4 cursor-pointer hover:border-sky-500/40 transition-colors border-border shadow-xs gap-0"
        role="button"
        tabindex="0"
        :class="{ 'ring-2 ring-sky-500/30 border-sky-500/50 bg-sky-500/5': selectedStatus === 'cold' }"
        :aria-pressed="selectedStatus === 'cold'"
        @click="selectedStatus = selectedStatus === 'cold' ? 'all' : 'cold'"
        @keydown.enter.space.prevent="selectedStatus = selectedStatus === 'cold' ? 'all' : 'cold'"
      >
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{{ t("leads.cold") }}</span>
          <div class="h-7 w-7 rounded-md bg-sky-500/10 text-sky-500 flex items-center justify-center">
            <Users :size="16" />
          </div>
        </div>
        <div class="text-2xl font-bold text-foreground">{{ coldCount }}</div>
      </Card>

      <!-- Phone Numbers -->
      <Card
        class="p-4 cursor-pointer hover:border-emerald-500/40 transition-colors border-border shadow-xs gap-0"
        role="button"
        tabindex="0"
        :class="{ 'ring-2 ring-emerald-500/30 border-emerald-500/50 bg-emerald-500/5': onlyWithPhone }"
        :aria-pressed="onlyWithPhone"
        @click="onlyWithPhone = !onlyWithPhone"
        @keydown.enter.space.prevent="onlyWithPhone = !onlyWithPhone"
      >
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{{ t("leads.phoneNumbers") }}</span>
          <div class="h-7 w-7 rounded-md bg-emerald-500/10 text-emerald-500 flex items-center justify-center">
            <Phone :size="16" />
          </div>
        </div>
        <div class="text-2xl font-bold text-foreground">{{ phoneCount }}</div>
      </Card>
    </div>

    <!-- Mobile Bottom Filter Drawer -->
    <MobileDrawer
      v-model:open="showMobileFilterDrawer"
      :title="t('leads.filterDrawerTitle')"
      :description="t('leads.filterDrawerDesc')"
    >
      <template #title-icon>
        <SlidersHorizontal :size="16" class="text-primary" />
      </template>

      <!-- Status Filter -->
      <div class="space-y-2">
        <label class="text-xs font-bold text-muted-foreground uppercase tracking-wider">
          {{ t("leads.leadStatusSection") }}
        </label>
        <div class="grid grid-cols-2 gap-2">
          <button
            type="button"
            class="p-2.5 rounded-xl border text-xs font-semibold flex items-center justify-between transition-all cursor-pointer"
            :class="selectedStatus === 'all' ? 'border-primary bg-primary/10 text-primary shadow-xs' : 'border-border bg-card text-muted-foreground hover:border-border/80'"
            @click="selectedStatus = 'all'"
          >
            <span>{{ t("leads.all") }}</span>
            <span class="text-[11px] opacity-70 font-mono">{{ totalCount }}</span>
          </button>
          <button
            type="button"
            class="p-2.5 rounded-xl border text-xs font-semibold flex items-center justify-between transition-all cursor-pointer"
            :class="selectedStatus === 'hot' ? 'border-rose-500 bg-rose-500/10 text-rose-500 shadow-xs' : 'border-border bg-card text-muted-foreground hover:border-border/80'"
            @click="selectedStatus = 'hot'"
          >
            <span class="flex items-center gap-1.5"><Flame :size="13" /> {{ t("leads.hot") }}</span>
            <span class="text-[11px] opacity-70 font-mono">{{ hotCount }}</span>
          </button>
          <button
            type="button"
            class="p-2.5 rounded-xl border text-xs font-semibold flex items-center justify-between transition-all cursor-pointer"
            :class="selectedStatus === 'warm' ? 'border-amber-500 bg-amber-500/10 text-amber-500 shadow-xs' : 'border-border bg-card text-muted-foreground hover:border-border/80'"
            @click="selectedStatus = 'warm'"
          >
            <span class="flex items-center gap-1.5"><UserCheck :size="13" /> {{ t("leads.warm") }}</span>
            <span class="text-[11px] opacity-70 font-mono">{{ warmCount }}</span>
          </button>
          <button
            type="button"
            class="p-2.5 rounded-xl border text-xs font-semibold flex items-center justify-between transition-all cursor-pointer"
            :class="selectedStatus === 'cold' ? 'border-sky-500 bg-sky-500/10 text-sky-500 shadow-xs' : 'border-border bg-card text-muted-foreground hover:border-border/80'"
            @click="selectedStatus = 'cold'"
          >
            <span class="flex items-center gap-1.5"><Users :size="13" /> {{ t("leads.cold") }}</span>
            <span class="text-[11px] opacity-70 font-mono">{{ coldCount }}</span>
          </button>
        </div>
      </div>

      <!-- Phone Number Filter -->
      <div class="space-y-2">
        <label class="text-xs font-bold text-muted-foreground uppercase tracking-wider">
          {{ t("leads.phone") }}
        </label>
        <div
          class="p-3 rounded-xl border cursor-pointer transition-all flex items-center justify-between"
          :class="onlyWithPhone ? 'border-emerald-500 bg-emerald-500/10 text-foreground' : 'border-border bg-card text-muted-foreground'"
          @click="onlyWithPhone = !onlyWithPhone"
        >
          <div class="flex items-center gap-2.5">
            <div class="h-8 w-8 rounded-lg flex items-center justify-center shrink-0" :class="onlyWithPhone ? 'bg-emerald-500 text-white' : 'bg-muted text-muted-foreground'">
              <Phone :size="15" />
            </div>
            <div>
              <div class="text-sm font-semibold text-foreground">{{ t("leads.onlyWithPhone") }}</div>
              <div class="text-[11px] text-muted-foreground">{{ t("leads.phoneCountDesc", { count: phoneCount }) }}</div>
            </div>
          </div>
          <div class="w-5 h-5 rounded-md border flex items-center justify-center transition-colors shrink-0" :class="onlyWithPhone ? 'bg-emerald-500 border-emerald-500 text-white' : 'border-muted-foreground/40'">
            <Check v-if="onlyWithPhone" :size="13" />
          </div>
        </div>
      </div>

      <!-- Sorting Options -->
      <div class="space-y-2">
        <label class="text-xs font-bold text-muted-foreground uppercase tracking-wider">
          {{ t("leads.sortMethod") }}
        </label>
        <div class="space-y-1.5">
          <button
            v-for="opt in [
              { value: 'score_desc', label: t('leads.sortScoreDesc') },
              { value: 'score_asc', label: t('leads.sortScoreAsc') },
              { value: 'date_desc', label: t('leads.sortDateDesc') },
              { value: 'date_asc', label: t('leads.sortDateAsc') }
            ]"
            :key="opt.value"
            type="button"
            class="w-full min-h-11 p-3 rounded-xl border text-sm font-medium flex items-center justify-between transition-colors cursor-pointer"
            :class="sortBy === opt.value ? 'border-primary bg-primary/10 text-primary' : 'border-border bg-card text-foreground hover:border-border/80'"
            :aria-pressed="sortBy === opt.value"
            @click="sortBy = opt.value as any"
          >
            <span>{{ opt.label }}</span>
            <Check v-if="sortBy === opt.value" :size="16" class="text-primary" />
          </button>
        </div>
      </div>

      <template #footer>
        <Button
          variant="outline"
          class="flex-1 h-11 text-xs cursor-pointer shadow-none"
          @click="resetFilters"
        >
          {{ t("common.clear") }}
        </Button>
        <Button
          variant="default"
          class="flex-2 h-11 text-xs font-bold cursor-pointer shadow-xs"
          @click="showMobileFilterDrawer = false"
        >
          {{ t("leads.viewResults", { count: filteredLeads.length }) }}
        </Button>
      </template>
    </MobileDrawer>

    <!-- Loading Skeleton -->
    <div v-if="loading" class="space-y-3">
      <Skeleton v-for="i in 5" :key="i" class="h-12 rounded-lg w-full" />
    </div>

    <!-- Load Error -->
    <Card v-else-if="loadError" class="text-center py-10 px-4 border-destructive/30 shadow-xs space-y-3">
      <AlertCircle :size="28" class="mx-auto text-destructive" />
      <p class="text-sm font-semibold text-foreground">{{ t("leads.loadError") }}</p>
      <p class="text-xs text-muted-foreground">{{ loadError }}</p>
      <Button variant="outline" size="sm" class="h-8 text-xs gap-1.5 cursor-pointer" @click="loadLeads">
        <RefreshCw :size="13" />
        <span>{{ t("common.retry") }}</span>
      </Button>
    </Card>

    <!-- Empty State -->
    <Card
      v-else-if="filteredLeads.length === 0"
      class="text-center py-12 px-4 border-border shadow-xs"
    >
      <Users :size="36" class="mx-auto text-muted-foreground/60 mb-2" />
      <h3 class="text-sm font-semibold text-foreground">
        {{ leads.length > 0 ? t("leads.notFound") : t("leads.noLeadsTitle") }}
      </h3>
      <p v-if="leads.length === 0" class="mx-auto mt-1 max-w-sm text-sm text-muted-foreground">{{ t("leads.noLeadsDesc") }}</p>
      <div v-if="leads.length > 0" class="mt-3">
        <Button variant="outline" class="h-10 cursor-pointer" @click="resetFilters">
          {{ t("leads.clearFilters") }}
        </Button>
      </div>
    </Card>

    <!-- LEADS DATA DISPLAY -->
    <template v-else>
      <!-- Cards: phones, tablets and narrow desktops — the 7-column table needs a wide screen -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3 xl:hidden">
        <Card
          v-for="lead in filteredLeads"
          :key="'mobile-' + lead.id"
          class="p-4 cursor-pointer hover:border-primary/40 transition-colors gap-3 border-border shadow-xs"
          @click="selectedLead = lead"
        >
          <!-- Header: Customer + Status Badge + Score -->
          <div class="flex items-start justify-between gap-2">
            <div>
              <div class="font-bold text-foreground text-sm flex items-center gap-1.5">
                <span>{{ lead.customer_username || t('leads.customer') }}</span>
              </div>
              <div class="text-[11px] text-muted-foreground flex items-center gap-1 mt-0.5">
                <Calendar :size="11" />
                <span>{{ formatDate(lead.updated_at || lead.created_at) }}</span>
              </div>
            </div>

            <div class="flex items-center gap-1.5 shrink-0">
              <Badge
                variant="outline"
                class="text-[10px] font-bold px-1.5 py-0 gap-1"
                :class="{
                  'bg-rose-500/10 text-rose-500 border-rose-500/25': lead.status === 'hot',
                  'bg-amber-500/10 text-amber-500 border-amber-500/25': lead.status === 'warm',
                  'bg-sky-500/10 text-sky-500 border-sky-500/25': lead.status === 'cold'
                }"
              >
                <Flame v-if="lead.status === 'hot'" :size="11" />
                <UserCheck v-else-if="lead.status === 'warm'" :size="11" />
                <Users v-else :size="11" />
                <span>{{ lead.status === 'hot' ? t('leads.hot') : lead.status === 'warm' ? t('leads.warm') : t('leads.cold') }}</span>
              </Badge>
              <span class="text-xs font-bold text-foreground font-mono bg-muted px-1.5 py-0.5 rounded">
                {{ lead.score }}/100
              </span>
            </div>
          </div>

          <!-- Score progress line (same colour as the lead's status) -->
          <div class="h-1 bg-muted/60 rounded-full overflow-hidden w-full">
            <div
              class="h-full rounded-full transition-all duration-300"
              :class="lead.status === 'hot' ? 'bg-rose-500' : lead.status === 'warm' ? 'bg-amber-500' : 'bg-sky-500'"
              :style="{ width: `${Math.max(lead.score, 6)}%` }"
            />
          </div>

          <!-- Phone Section if present -->
          <div v-if="lead.phone" class="flex items-center justify-between gap-2 bg-muted/40 p-2 rounded-lg text-sm" @click.stop>
            <a :href="`tel:${lead.phone}`" class="flex min-w-0 items-center gap-2 font-semibold text-foreground font-mono hover:text-primary">
              <Phone :size="14" class="shrink-0 text-emerald-600 dark:text-emerald-400" />
              <span class="truncate">{{ lead.phone }}</span>
            </a>
            <div class="flex items-center gap-1 shrink-0">
              <button
                type="button"
                class="icon-btn"
                :title="copiedPhone === lead.phone ? t('common.copied') : t('common.copy')"
                :aria-label="copiedPhone === lead.phone ? t('common.copied') : t('common.copy')"
                @click="copyPhone(lead.phone)"
              >
                <Check v-if="copiedPhone === lead.phone" :size="16" class="text-emerald-600" />
                <Copy v-else :size="16" />
              </button>
              <a
                :href="`tel:${lead.phone}`"
                class="inline-flex h-10 items-center gap-1.5 px-3 text-sm font-semibold rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
              >
                <PhoneCall :size="14" />
                <span>{{ t("leads.call") }}</span>
              </a>
            </div>
          </div>

          <!-- Interested products -->
          <div v-if="lead.interested_products && lead.interested_products.length > 0" class="flex flex-wrap gap-1">
            <Badge
              v-for="p in lead.interested_products"
              :key="p.id"
              variant="secondary"
              class="px-2 py-0.5 text-[11px] font-normal"
            >
              {{ p.name }}
            </Badge>
          </div>

          <!-- AI Insight Summary -->
          <div class="flex items-start gap-1.5 text-xs text-muted-foreground bg-muted/20 p-2.5 rounded-lg border border-border/40">
            <Sparkles :size="13" class="text-primary shrink-0 mt-0.5" />
            <span class="line-clamp-2">
              {{ formatAiReason(lead) }}
            </span>
          </div>

          <!-- Card Footer Actions -->
          <div class="flex items-center justify-end gap-2 pt-2 mt-auto border-t border-border/40" @click.stop>
            <Button
              v-if="lead.conversation_id"
              variant="outline"
              class="h-10 text-sm gap-1.5 font-medium flex-1 cursor-pointer shadow-none"
              @click="openLeadChat(lead)"
            >
              <MessageSquare :size="15" />
              <span>{{ t("leads.openChat") }}</span>
            </Button>
            <Button
              variant="ghost"
              class="h-10 text-sm gap-1.5 text-muted-foreground hover:text-foreground cursor-pointer"
              @click="selectedLead = lead"
            >
              <Eye :size="15" />
              <span>{{ t("leads.details") }}</span>
            </Button>
          </div>
        </Card>
      </div>

      <!-- Table: wide screens only -->
      <Card class="hidden xl:block overflow-hidden border-border shadow-xs py-0 gap-0">
        <Table>
          <TableHeader class="bg-muted/40">
            <TableRow>
              <TableHead class="py-3 px-4">{{ t("leads.customer") }}</TableHead>
              <TableHead class="py-3 px-4">{{ t("leads.intentScore") }}</TableHead>
              <TableHead class="py-3 px-4">{{ t("leads.phone") }}</TableHead>
              <TableHead class="py-3 px-4">{{ t("leads.interestedProducts") }}</TableHead>
              <TableHead class="py-3 px-4 min-w-[220px]">{{ t("leads.aiInsight") }}</TableHead>
              <TableHead class="py-3 px-4 whitespace-nowrap">{{ t("leads.lastActivity") }}</TableHead>
              <TableHead class="py-3 px-4 text-right">{{ t("leads.actions") }}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow
              v-for="lead in filteredLeads"
              :key="lead.id"
              class="hover:bg-muted/30 transition-colors group cursor-pointer"
              @click="selectedLead = lead"
            >
              <!-- Customer -->
              <TableCell class="py-3 px-4 font-semibold text-foreground text-sm group-hover:text-primary transition-colors">
                {{ lead.customer_username || t('leads.customer') }}
              </TableCell>

              <!-- Intent Score & Status Badge -->
              <TableCell class="py-3 px-4 whitespace-nowrap">
                <div class="space-y-1 min-w-[110px]">
                  <div class="flex items-center justify-between gap-1.5">
                    <Badge
                      variant="outline"
                      class="text-[10px] font-bold px-1.5 py-0 gap-1"
                      :class="{
                        'bg-rose-500/10 text-rose-500 border-rose-500/25': lead.status === 'hot',
                        'bg-amber-500/10 text-amber-500 border-amber-500/25': lead.status === 'warm',
                        'bg-sky-500/10 text-sky-500 border-sky-500/25': lead.status === 'cold'
                      }"
                    >
                      <Flame v-if="lead.status === 'hot'" :size="11" />
                      <UserCheck v-else-if="lead.status === 'warm'" :size="11" />
                      <Users v-else :size="11" />
                      <span>{{ lead.status === 'hot' ? t('leads.hot') : lead.status === 'warm' ? t('leads.warm') : t('leads.cold') }}</span>
                    </Badge>
                    <span class="text-xs font-bold text-foreground font-mono">{{ lead.score }}/100</span>
                  </div>

                  <div class="h-1 bg-muted/60 rounded-full overflow-hidden w-full">
                    <div
                      class="h-full rounded-full transition-all duration-300"
                      :class="lead.status === 'hot' ? 'bg-rose-500' : lead.status === 'warm' ? 'bg-amber-500' : 'bg-sky-500'"
                      :style="{ width: `${Math.max(lead.score, 6)}%` }"
                    />
                  </div>
                </div>
              </TableCell>

              <!-- Phone Number -->
              <TableCell class="py-3 px-4 whitespace-nowrap" @click.stop>
                <div v-if="lead.phone" class="inline-flex items-center gap-1.5">
                  <a
                    :href="`tel:${lead.phone}`"
                    class="font-semibold text-xs text-foreground hover:text-primary transition-colors font-mono"
                  >
                    {{ lead.phone }}
                  </a>
                  <button
                    type="button"
                    class="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                    :title="copiedPhone === lead.phone ? t('common.copied') : t('common.copy')"
                    @click="copyPhone(lead.phone)"
                  >
                    <Check v-if="copiedPhone === lead.phone" :size="13" class="text-emerald-500" />
                    <Copy v-else :size="13" />
                  </button>
                </div>
                <span v-else class="text-xs text-muted-foreground/40 italic">
                  —
                </span>
              </TableCell>

              <!-- Interested Products -->
              <TableCell class="py-3 px-4">
                <div v-if="lead.interested_products && lead.interested_products.length > 0" class="flex flex-wrap gap-1 max-w-xs">
                  <Badge
                    v-for="p in lead.interested_products"
                    :key="p.id"
                    variant="secondary"
                    class="px-1.5 py-0 text-[11px] font-normal"
                  >
                    {{ p.name }}
                  </Badge>
                </div>
                <span v-else class="text-xs text-muted-foreground/40">—</span>
              </TableCell>

              <!-- AI Insight / Reason -->
              <TableCell class="py-3 px-4">
                <div class="flex items-start gap-1.5 text-xs text-muted-foreground max-w-sm">
                  <Sparkles :size="13" class="text-primary shrink-0 mt-0.5" />
                  <span class="line-clamp-2" :title="formatAiReason(lead)">
                    {{ formatAiReason(lead) }}
                  </span>
                </div>
              </TableCell>

              <!-- Last Updated Date -->
              <TableCell class="py-3 px-4 whitespace-nowrap text-xs text-muted-foreground">
                <div class="flex items-center gap-1.5">
                  <Calendar :size="12" class="text-muted-foreground/60" />
                  <span>{{ formatDate(lead.updated_at || lead.created_at) }}</span>
                </div>
              </TableCell>

              <!-- Actions -->
              <TableCell class="py-3 px-4 text-right whitespace-nowrap" @click.stop>
                <div class="inline-flex items-center gap-1.5">
                  <Button
                    v-if="lead.conversation_id"
                    variant="outline"
                    size="sm"
                    class="h-7 text-xs gap-1 px-2.5 font-medium cursor-pointer shadow-none"
                    :title="t('leads.openChat')"
                    @click="openLeadChat(lead)"
                  >
                    <MessageSquare :size="12" />
                    <span>{{ t("leads.openChat") }}</span>
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    class="h-7 w-7 text-muted-foreground hover:text-foreground cursor-pointer"
                    :title="t('leads.details')"
                    @click="selectedLead = lead"
                  >
                    <Eye :size="14" />
                  </Button>
                </div>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </Card>
    </template>

    <!-- LEAD DETAIL MODAL -->
    <Dialog :open="!!selectedLead" @update:open="(v) => { if (!v) selectedLead = null }">
      <DialogContent v-if="selectedLead" class="sm:max-w-lg p-0 gap-0 overflow-hidden">
        <!-- Modal Header -->
        <DialogHeader class="p-5 border-b border-border">
          <DialogTitle class="text-base font-bold text-foreground">
            {{ selectedLead.customer_username || t('leads.customer') }}
          </DialogTitle>
        </DialogHeader>

        <!-- Modal Body -->
        <div class="p-5 overflow-y-auto max-h-[70vh] space-y-4">
          <!-- Status & Score -->
          <div class="p-3.5 rounded-lg bg-muted/30 border border-border flex items-center justify-between">
            <div>
              <div class="text-[11px] text-muted-foreground uppercase font-semibold">{{ t("leads.status") }}</div>
              <Badge
                variant="outline"
                class="text-xs font-bold mt-1 gap-1"
                :class="{
                  'bg-rose-500/10 text-rose-500 border-rose-500/20': selectedLead.status === 'hot',
                  'bg-amber-500/10 text-amber-500 border-amber-500/20': selectedLead.status === 'warm',
                  'bg-sky-500/10 text-sky-500 border-sky-500/20': selectedLead.status === 'cold'
                }"
              >
                <Flame v-if="selectedLead.status === 'hot'" :size="12" />
                <UserCheck v-else-if="selectedLead.status === 'warm'" :size="12" />
                <Users v-else :size="12" />
                <span>{{ selectedLead.status === 'hot' ? t('leads.hotLead') : selectedLead.status === 'warm' ? t('leads.warmLead') : t('leads.coldLead') }}</span>
              </Badge>
            </div>

            <div class="text-right">
              <div class="text-[11px] text-muted-foreground uppercase font-semibold">{{ t("leads.intentScore") }}</div>
              <div class="text-lg font-bold text-foreground font-mono mt-0.5">{{ selectedLead.score }}/100</div>
            </div>
          </div>

          <!-- Phone Number -->
          <div class="p-3.5 rounded-lg bg-muted/30 border border-border space-y-1">
            <div class="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <Phone :size="12" />
              <span>{{ t("leads.phone") }}</span>
            </div>
            <div v-if="selectedLead.phone" class="flex items-center justify-between pt-1">
              <span class="text-sm font-bold text-foreground font-mono">{{ selectedLead.phone }}</span>
              <div class="flex items-center gap-1.5">
                <a
                  :href="`tel:${selectedLead.phone}`"
                  class="inline-flex items-center gap-1 px-2.5 py-1 text-xs font-semibold rounded-md bg-emerald-500 text-white hover:bg-emerald-600 transition"
                >
                  <PhoneCall :size="12" />
                  <span>{{ t("leads.call") }}</span>
                </a>
                <Button
                  variant="outline"
                  size="icon"
                  class="h-7 w-7 cursor-pointer"
                  @click="copyPhone(selectedLead.phone)"
                >
                  <Check v-if="copiedPhone === selectedLead.phone" :size="13" class="text-emerald-500" />
                  <Copy v-else :size="13" />
                </Button>
              </div>
            </div>
            <div v-else class="text-xs text-muted-foreground italic pt-1">
              {{ t("leads.notCollected") }}
            </div>
          </div>

          <!-- Interested Products -->
          <div v-if="selectedLead.interested_products && selectedLead.interested_products.length > 0" class="space-y-1.5">
            <div class="text-xs font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <Package :size="12" />
              <span>{{ t("leads.interestedProducts") }}</span>
            </div>
            <div class="flex flex-wrap gap-1">
              <Badge
                v-for="p in selectedLead.interested_products"
                :key="p.id"
                variant="secondary"
                class="px-2 py-0.5 text-xs font-medium"
              >
                {{ p.name }}
              </Badge>
            </div>
          </div>

          <!-- AI Insight -->
          <div class="p-3.5 rounded-lg bg-primary/5 border border-primary/20 space-y-1">
            <div class="text-xs font-bold uppercase tracking-wider text-primary flex items-center gap-1.5">
              <Sparkles :size="13" />
              <span>{{ t("leads.aiInsight") }}</span>
            </div>
            <p class="text-xs text-foreground leading-relaxed">
              "{{ formatAiReason(selectedLead) }}"
            </p>
          </div>
        </div>

        <!-- Modal Footer -->
        <DialogFooter class="p-4 bg-muted/30 border-t border-border flex items-center justify-end gap-2">
          <Button
            variant="outline"
            size="sm"
            class="h-9 px-4 cursor-pointer"
            @click="selectedLead = null"
          >
            {{ t("common.close") }}
          </Button>
          <Button
            v-if="selectedLead.conversation_id"
            variant="default"
            size="sm"
            class="h-9 px-4 gap-1.5 cursor-pointer shadow-xs"
            @click="openLeadChat(selectedLead)"
          >
            <MessageSquare :size="14" />
            <span>{{ t("leads.openChat") }}</span>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

  </div>
</template>
