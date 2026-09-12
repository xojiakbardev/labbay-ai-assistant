<script setup lang="ts">
import { Plus, Building2, RefreshCw, Check, Trash2 } from "@lucide/vue";
import type { Plan, SuperadminBusiness } from "~/types/api";
import { formatFullDate } from "~/composables/useDateFormat";

definePageMeta({ layout: "superadmin" });

const api = useSuperadminApi();
const { t, locale } = useI18n();

const businesses = ref<SuperadminBusiness[]>([]);
const plans = ref<Plan[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);

// reka-ui's Select can't hold null or "", so "no plan" is a sentinel.
const NO_PLAN = "none";
const MONTH_CHOICES = [1, 3, 6, 12];
const SOON_DAYS = 7;

function errorText(err: unknown, fallbackKey: string): string {
  return err instanceof Error && err.message ? err.message : t(fallbackKey);
}

async function load() {
  loading.value = true;
  try {
    [businesses.value, plans.value] = await Promise.all([api.listBusinesses(), api.listPlans()]);
  } catch (err) {
    error.value = errorText(err, "superadmin.businesses.loadError");
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const fmtInt = (n: number) => new Intl.NumberFormat("uz-UZ").format(n);
const fmtDate = (iso: string | null) => formatFullDate(iso, locale.value);
const fmtUsd = (v: number) => `$${v.toFixed(v < 10 ? 4 : 2)}`;
const fmtMoney = (v: number, currency: string) =>
  `${new Intl.NumberFormat("uz-UZ", { maximumFractionDigits: 2 }).format(v)} ${currency}`;

const activePlans = computed(() => plans.value.filter((p) => p.is_active));
const planById = (id: string | null) => plans.value.find((p) => p.id === id) ?? null;

// --- Row indicators ---
function usagePercent(b: SuperadminBusiness) {
  if (!b.ai_replies_limit) return 0;
  return Math.min(100, Math.round((b.ai_replies_used / b.ai_replies_limit) * 100));
}
function usageTone(b: SuperadminBusiness) {
  const p = b.ai_replies_limit ? (b.ai_replies_used / b.ai_replies_limit) * 100 : 0;
  return p >= 100 ? "bg-destructive" : p >= 80 ? "bg-orange-500" : "bg-primary";
}
function daysLeft(b: SuperadminBusiness) {
  if (!b.subscription_expires_at) return null;
  return Math.ceil((new Date(b.subscription_expires_at).getTime() - Date.now()) / 86_400_000);
}
function subscriptionDot(b: SuperadminBusiness) {
  const days = daysLeft(b);
  if (!b.subscription_active || days === null) return { cls: "bg-destructive", tip: t("superadmin.businesses.expiredTip") };
  if (days <= SOON_DAYS) return { cls: "bg-orange-500", tip: t("superadmin.businesses.daysLeft", { days }) };
  return { cls: "bg-emerald-500", tip: t("superadmin.businesses.daysLeft", { days }) };
}

// --- Create business ---
const showCreate = ref(false);
const creating = ref(false);
const createForm = ref(blankCreateForm());

function blankCreateForm() {
  const def = plans.value.find((p) => p.is_default && p.is_active);
  return { business_name: "", email: "", password: "", plan: def?.id ?? NO_PLAN, days: def && def.price > 0 ? 30 : 14 };
}
function openCreate() {
  createForm.value = blankCreateForm();
  showCreate.value = true;
}
// A paid plan usually means a month; a free one, a trial.
watch(
  () => createForm.value.plan,
  (id) => {
    const plan = planById(id);
    createForm.value.days = plan && plan.price > 0 ? 30 : 14;
  }
);

async function submitCreate() {
  creating.value = true;
  error.value = null;
  const { business_name, email, password, plan, days } = createForm.value;
  try {
    await api.createBusiness({ business_name, email, password, trial_days: days, plan_id: plan === NO_PLAN ? null : plan });
    showCreate.value = false;
    await load();
  } catch (err) {
    error.value = errorText(err, "superadmin.businesses.createError");
  } finally {
    creating.value = false;
  }
}

// --- Renew plan: starts today ---
const renewTarget = ref<SuperadminBusiness | null>(null);
const renewing = ref(false);
const renewForm = ref({ plan: NO_PLAN, months: 1, payment_amount: null as number | null, payment_currency: "USD", payment_note: "" });

const renewPlanChoices = computed(() =>
  plans.value.filter((p) => p.is_active || p.id === renewTarget.value?.plan_id)
);
const renewPlan = computed(() => planById(renewForm.value.plan));
const renewEnds = computed(() => {
  const end = new Date();
  end.setMonth(end.getMonth() + renewForm.value.months);
  return fmtDate(end.toISOString());
});

function fillPayment() {
  const plan = renewPlan.value;
  renewForm.value.payment_amount = plan && plan.price > 0 ? plan.price * renewForm.value.months : null;
  if (plan) renewForm.value.payment_currency = plan.currency;
}
function openRenew(b: SuperadminBusiness) {
  renewTarget.value = b;
  renewForm.value = { plan: b.plan_id ?? NO_PLAN, months: 1, payment_amount: null, payment_currency: "USD", payment_note: "" };
  fillPayment();
}
watch(() => [renewForm.value.plan, renewForm.value.months], fillPayment);

async function submitRenew() {
  if (!renewTarget.value) return;
  renewing.value = true;
  error.value = null;
  const f = renewForm.value;
  try {
    const updated = await api.renewPlan(renewTarget.value.id, {
      plan_id: f.plan === NO_PLAN ? null : f.plan,
      months: f.months,
      payment_amount: f.payment_amount || null,
      payment_currency: f.payment_currency,
      payment_note: f.payment_note || null,
    });
    const idx = businesses.value.findIndex((b) => b.id === updated.id);
    if (idx !== -1) businesses.value[idx] = updated;
    renewTarget.value = null;
  } catch (err) {
    error.value = errorText(err, "superadmin.businesses.renewError");
  } finally {
    renewing.value = false;
  }
}

// --- Platform AI switch ---
const togglingAiId = ref<string | null>(null);

async function toggleSuspension(b: SuperadminBusiness) {
  if (togglingAiId.value) return;
  togglingAiId.value = b.id;
  error.value = null;
  try {
    const updated = await api.setAiSuspended(b.id, !b.ai_suspended);
    const idx = businesses.value.findIndex((x) => x.id === b.id);
    if (idx !== -1) businesses.value[idx] = updated;
  } catch (err) {
    error.value = errorText(err, "superadmin.businesses.toggleAiError");
  } finally {
    togglingAiId.value = null;
  }
}

// --- Hard delete: the name is typed to confirm ---
const deleteTarget = ref<SuperadminBusiness | null>(null);
const deleteTyped = ref("");
const deleting = ref(false);
const deleteConfirmed = computed(() => deleteTyped.value.trim() === deleteTarget.value?.name.trim());

function openDelete(b: SuperadminBusiness) {
  deleteTarget.value = b;
  deleteTyped.value = "";
}
async function confirmDelete() {
  if (!deleteTarget.value || !deleteConfirmed.value) return;
  deleting.value = true;
  error.value = null;
  try {
    await api.deleteBusiness(deleteTarget.value.id);
    deleteTarget.value = null;
    await load();
  } catch (err) {
    error.value = errorText(err, "superadmin.businesses.deleteError");
  } finally {
    deleting.value = false;
  }
}
</script>

<template>
  <TooltipProvider :delay-duration="200">
    <div class="superadmin-businesses-page">
      <div class="page-header mb-5 flex items-center justify-between flex-wrap gap-3">
        <h1 class="text-2xl font-bold tracking-tight">{{ t("superadmin.businesses.title") }}</h1>
        <Button class="gap-2" @click="openCreate">
          <Plus :size="16" />
          <span class="hidden sm:inline">{{ t("superadmin.businesses.addBusiness") }}</span>
        </Button>
      </div>

      <p v-if="error" class="error mb-4">{{ error }}</p>

      <div v-if="loading" class="space-y-3">
        <Skeleton v-for="i in 5" :key="i" class="h-14 w-full rounded-lg" />
      </div>

      <EmptyState v-else-if="businesses.length === 0" :title="t('superadmin.businesses.noBusinessesTitle')">
        <template #icon><Building2 :size="28" /></template>
        <Button class="gap-2" @click="openCreate">
          <Plus :size="16" />
          <span>{{ t("superadmin.businesses.addBusiness") }}</span>
        </Button>
      </EmptyState>

      <div v-else class="rounded-xl border border-border bg-card overflow-x-auto shadow-2xs">
        <Table>
          <TableHeader>
            <TableRow class="bg-muted/50">
              <TableHead>{{ t("superadmin.businesses.colName") }}</TableHead>
              <TableHead>{{ t("superadmin.businesses.colLogin") }}</TableHead>
              <TableHead>{{ t("superadmin.businesses.colPlan") }}</TableHead>
              <TableHead class="min-w-36">{{ t("superadmin.businesses.colLimit") }}</TableHead>
              <TableHead>{{ t("superadmin.businesses.colSubscription") }}</TableHead>
              <TableHead class="text-center">{{ t("superadmin.businesses.colAi") }}</TableHead>
              <TableHead class="text-center">{{ t("superadmin.businesses.colOwnerAi") }}</TableHead>
              <TableHead class="text-right">{{ t("superadmin.businesses.colCost") }}</TableHead>
              <TableHead class="w-24"><span class="sr-only">{{ t("superadmin.businesses.colActions") }}</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow v-for="b in businesses" :key="b.id" class="hover:bg-muted/40 transition-colors">
              <TableCell class="font-semibold text-foreground">{{ b.name }}</TableCell>
              <TableCell class="text-sm text-muted-foreground">{{ b.owner_email }}</TableCell>
              <TableCell class="text-sm">
                <span v-if="b.plan_name">{{ b.plan_name }}</span>
                <span v-else class="text-muted-foreground">—</span>
              </TableCell>

              <!-- This plan month's AI replies against the limit -->
              <TableCell>
                <Tooltip>
                  <TooltipTrigger as-child>
                    <div class="w-36 cursor-default">
                      <div v-if="b.ai_replies_limit !== null" class="h-1.5 w-full rounded-full bg-muted overflow-hidden">
                        <div class="h-full rounded-full" :class="usageTone(b)" :style="{ width: `${usagePercent(b)}%` }" />
                      </div>
                      <div class="mt-1 font-mono text-xs tabular-nums text-muted-foreground">
                        {{ fmtInt(b.ai_replies_used) }} / {{ b.ai_replies_limit !== null ? fmtInt(b.ai_replies_limit) : "∞" }}
                      </div>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>{{ t("superadmin.businesses.limitTip", { date: fmtDate(b.usage_period_end) }) }}</TooltipContent>
                </Tooltip>
              </TableCell>

              <TableCell class="text-sm whitespace-nowrap">
                <Tooltip>
                  <TooltipTrigger as-child>
                    <span class="inline-flex items-center gap-2 cursor-default">
                      <span class="size-2 rounded-full shrink-0" :class="subscriptionDot(b).cls" aria-hidden="true" />
                      <span>{{ fmtDate(b.subscription_expires_at) }}</span>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>{{ subscriptionDot(b).tip }}</TooltipContent>
                </Tooltip>
              </TableCell>

              <!-- Platform kill switch -->
              <TableCell class="text-center">
                <Tooltip>
                  <TooltipTrigger as-child>
                    <span class="inline-flex">
                      <Switch
                        :model-value="!b.ai_suspended"
                        :disabled="togglingAiId === b.id"
                        :aria-label="t('superadmin.businesses.colAi')"
                        @update:model-value="toggleSuspension(b)"
                      />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {{ b.ai_suspended ? t("superadmin.businesses.aiSuspended") : t("superadmin.businesses.aiAllowed") }}
                  </TooltipContent>
                </Tooltip>
              </TableCell>

              <!-- The owner's own switch, read-only -->
              <TableCell class="text-center">
                <Tooltip>
                  <TooltipTrigger as-child>
                    <span
                      class="inline-block size-2.5 rounded-full cursor-default"
                      :class="b.ai_enabled ? 'bg-emerald-500' : 'bg-muted-foreground/40'"
                      role="img"
                      :aria-label="b.ai_enabled ? t('superadmin.businesses.ownerAiOn') : t('superadmin.businesses.ownerAiOff')"
                    />
                  </TooltipTrigger>
                  <TooltipContent>
                    {{ b.ai_enabled ? t("superadmin.businesses.ownerAiOn") : t("superadmin.businesses.ownerAiOff") }}
                  </TooltipContent>
                </Tooltip>
              </TableCell>

              <TableCell class="text-right font-mono text-sm tabular-nums">{{ fmtUsd(b.cost_last_30d_usd) }}</TableCell>

              <TableCell>
                <div class="flex items-center justify-end gap-1">
                  <Tooltip>
                    <TooltipTrigger as-child>
                      <Button variant="ghost" size="icon" class="h-8 w-8" :aria-label="t('superadmin.businesses.renew')" @click="openRenew(b)">
                        <RefreshCw :size="15" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{{ t("superadmin.businesses.renew") }}</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger as-child>
                      <Button
                        variant="ghost"
                        size="icon"
                        class="h-8 w-8 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                        :aria-label="t('superadmin.businesses.delete')"
                        @click="openDelete(b)"
                      >
                        <Trash2 :size="15" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{{ t("superadmin.businesses.delete") }}</TooltipContent>
                  </Tooltip>
                </div>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>

      <!-- Create business -->
      <Dialog v-model:open="showCreate">
        <DialogContent class="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{{ t("superadmin.businesses.createModalTitle") }}</DialogTitle>
          </DialogHeader>
          <form class="space-y-4 pt-2" @submit.prevent="submitCreate">
            <div class="space-y-1.5">
              <Label for="create-biz-name">{{ t("superadmin.businesses.businessNameLabel") }}</Label>
              <Input id="create-biz-name" v-model="createForm.business_name" type="text" required />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div class="space-y-1.5">
                <Label for="create-biz-email">{{ t("superadmin.businesses.ownerEmailLabel") }}</Label>
                <Input id="create-biz-email" v-model="createForm.email" type="text" autocomplete="off" required />
              </div>
              <div class="space-y-1.5">
                <Label for="create-biz-pwd">{{ t("superadmin.businesses.passwordLabel") }}</Label>
                <Input id="create-biz-pwd" v-model="createForm.password" type="password" autocomplete="new-password" required />
              </div>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div class="space-y-1.5">
                <Label>{{ t("superadmin.businesses.planLabel") }}</Label>
                <Select v-model="createForm.plan">
                  <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem v-for="p in activePlans" :key="p.id" :value="p.id">{{ p.name }}</SelectItem>
                    <SelectItem :value="NO_PLAN">{{ t("superadmin.businesses.noPlan") }}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div class="space-y-1.5">
                <Label for="create-biz-days">{{ t("superadmin.businesses.daysLabel") }}</Label>
                <Input id="create-biz-days" v-model.number="createForm.days" type="number" min="0" max="365" required />
              </div>
            </div>
            <div class="flex items-center justify-end gap-3 pt-2">
              <Button type="button" variant="outline" @click="showCreate = false">{{ t("common.cancel") }}</Button>
              <Button type="submit" :disabled="creating" class="gap-2">
                <Check :size="16" />
                <span>{{ creating ? t("superadmin.businesses.creating") : t("superadmin.businesses.create") }}</span>
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <!-- Renew plan -->
      <Dialog :open="!!renewTarget" @update:open="(v) => { if (!v) renewTarget = null }">
        <DialogContent class="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{{ t("superadmin.businesses.renewTitle", { name: renewTarget?.name ?? "" }) }}</DialogTitle>
          </DialogHeader>
          <form class="space-y-4 pt-2" @submit.prevent="submitRenew">
            <div class="grid grid-cols-2 gap-3">
              <div class="space-y-1.5">
                <Label>{{ t("superadmin.businesses.planLabel") }}</Label>
                <Select v-model="renewForm.plan">
                  <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem v-for="p in renewPlanChoices" :key="p.id" :value="p.id">{{ p.name }}</SelectItem>
                    <SelectItem :value="NO_PLAN">{{ t("superadmin.businesses.noPlan") }}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div class="space-y-1.5">
                <Label>{{ t("superadmin.businesses.monthsLabel") }}</Label>
                <Select v-model="renewForm.months">
                  <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem v-for="m in MONTH_CHOICES" :key="m" :value="m">{{ t("superadmin.businesses.monthsOption", { n: m }) }}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <dl class="grid grid-cols-2 gap-x-3 gap-y-1 rounded-lg bg-muted/40 p-3 text-sm">
              <dt class="text-muted-foreground">{{ t("superadmin.businesses.periodLabel") }}</dt>
              <dd class="text-right">{{ t("superadmin.businesses.periodValue", { date: renewEnds }) }}</dd>
              <dt class="text-muted-foreground">{{ t("superadmin.businesses.colLimit") }}</dt>
              <dd class="text-right font-mono tabular-nums">
                {{ renewPlan?.monthly_ai_replies ? t("superadmin.businesses.perMonth", { n: fmtInt(renewPlan.monthly_ai_replies) }) : "∞" }}
              </dd>
              <dt v-if="renewPlan" class="text-muted-foreground">{{ t("superadmin.businesses.priceLabel") }}</dt>
              <dd v-if="renewPlan" class="text-right font-mono tabular-nums">{{ fmtMoney(renewPlan.price, renewPlan.currency) }}</dd>
            </dl>
            <p class="text-xs text-muted-foreground">{{ t("superadmin.businesses.renewNote") }}</p>

            <div class="grid grid-cols-2 gap-3">
              <div class="space-y-1.5">
                <Label for="renew-amount">{{ t("superadmin.businesses.paymentAmountLabel") }}</Label>
                <Input id="renew-amount" v-model.number="renewForm.payment_amount" type="number" min="0" step="any" />
              </div>
              <div class="space-y-1.5">
                <Label>{{ t("superadmin.businesses.paymentCurrencyLabel") }}</Label>
                <Select v-model="renewForm.payment_currency">
                  <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="USD">USD</SelectItem>
                    <SelectItem value="UZS">UZS</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div class="space-y-1.5">
              <Label for="renew-note">{{ t("superadmin.businesses.paymentNoteLabel") }}</Label>
              <Input id="renew-note" v-model="renewForm.payment_note" type="text" maxlength="500" />
            </div>
            <div class="flex items-center justify-end gap-3 pt-2">
              <Button type="button" variant="outline" @click="renewTarget = null">{{ t("common.cancel") }}</Button>
              <Button type="submit" :disabled="renewing" class="gap-2">
                <RefreshCw :size="16" />
                <span>{{ renewing ? t("superadmin.businesses.saving") : t("superadmin.businesses.renew") }}</span>
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>

      <!-- Hard delete -->
      <Dialog :open="!!deleteTarget" @update:open="(v) => { if (!v) deleteTarget = null }">
        <DialogContent class="sm:max-w-md">
          <DialogHeader>
            <DialogTitle class="text-destructive">{{ t("superadmin.businesses.deleteModalTitle") }}</DialogTitle>
          </DialogHeader>
          <p class="text-sm text-foreground">
            {{ t("superadmin.businesses.deleteConfirm", { name: deleteTarget?.name ?? "" }) }}
          </p>
          <div class="space-y-1.5">
            <Label for="delete-name">{{ t("superadmin.businesses.deleteTypeName") }}</Label>
            <Input id="delete-name" v-model="deleteTyped" type="text" autocomplete="off" :placeholder="deleteTarget?.name" />
          </div>
          <div class="flex items-center justify-end gap-3 mt-2">
            <Button variant="outline" @click="deleteTarget = null">{{ t("common.cancel") }}</Button>
            <Button variant="destructive" class="gap-2" :disabled="deleting || !deleteConfirmed" @click="confirmDelete">
              <Trash2 :size="16" />
              {{ deleting ? t("superadmin.businesses.deleting") : t("superadmin.businesses.delete") }}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  </TooltipProvider>
</template>
