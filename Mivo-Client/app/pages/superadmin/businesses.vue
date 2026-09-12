<script setup lang="ts">
import { Plus, Building2, CalendarClock, Check, Trash2 } from "@lucide/vue";
import type { SuperadminBusiness } from "~/types/api";
import { formatFullDate } from "~/composables/useDateFormat";

definePageMeta({ layout: "superadmin" });

const api = useSuperadminApi();
const { t, locale } = useI18n();

const businesses = ref<SuperadminBusiness[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);

function errorText(err: unknown, fallbackKey: string): string {
  return err instanceof Error && err.message ? err.message : t(fallbackKey);
}

async function load() {
  loading.value = true;
  try {
    businesses.value = await api.listBusinesses();
  } catch (err) {
    error.value = errorText(err, "superadmin.businesses.loadError");
  } finally {
    loading.value = false;
  }
}
onMounted(load);

function fmtDate(iso: string | null) {
  return formatFullDate(iso, locale.value);
}
function fmtUsd(v: number) {
  return `$${v.toFixed(v < 10 ? 4 : 2)}`;
}

// --- Create business ---
const showCreateModal = ref(false);
const creating = ref(false);
const createForm = ref({ business_name: "", email: "", password: "", trial_days: 14 });

async function submitCreate() {
  creating.value = true;
  error.value = null;
  try {
    await api.createBusiness(createForm.value);
    showCreateModal.value = false;
    createForm.value = { business_name: "", email: "", password: "", trial_days: 14 };
    await load();
  } catch (err) {
    error.value = errorText(err, "superadmin.businesses.createError");
  } finally {
    creating.value = false;
  }
}

// --- Extend subscription ---
const extendTarget = ref<SuperadminBusiness | null>(null);
const extending = ref(false);
const extendForm = ref({ subscription_expires_at: "", payment_amount: null as number | null, payment_currency: "UZS", payment_note: "" });

function openExtend(business: SuperadminBusiness) {
  extendTarget.value = business;
  const base = business.subscription_expires_at && new Date(business.subscription_expires_at) > new Date()
    ? new Date(business.subscription_expires_at)
    : new Date();
  base.setDate(base.getDate() + 30);
  extendForm.value = {
    subscription_expires_at: base.toISOString().slice(0, 10),
    payment_amount: null,
    payment_currency: "UZS",
    payment_note: "",
  };
}

async function submitExtend() {
  if (!extendTarget.value) return;
  extending.value = true;
  error.value = null;
  try {
    await api.extendSubscription(extendTarget.value.id, {
      subscription_expires_at: new Date(extendForm.value.subscription_expires_at + "T23:59:59Z").toISOString(),
      payment_amount: extendForm.value.payment_amount || null,
      payment_currency: extendForm.value.payment_currency,
      payment_note: extendForm.value.payment_note || null,
    });
    extendTarget.value = null;
    await load();
  } catch (err) {
    error.value = errorText(err, "superadmin.businesses.extendError");
  } finally {
    extending.value = false;
  }
}

// The switch is the platform kill switch (ai_suspended): on = AI allowed.
// The owner's own ai_enabled is shown next to it, read-only.
const togglingAiId = ref<string | null>(null);

async function toggleSuspension(business: SuperadminBusiness) {
  if (togglingAiId.value) return;
  togglingAiId.value = business.id;
  error.value = null;
  try {
    const updated = await api.setAiSuspended(business.id, !business.ai_suspended);
    const idx = businesses.value.findIndex((b) => b.id === business.id);
    if (idx !== -1) businesses.value[idx] = updated;
  } catch (err) {
    error.value = errorText(err, "superadmin.businesses.toggleAiError");
  } finally {
    togglingAiId.value = null;
  }
}

// --- Delete (soft) ---
const deleteTarget = ref<SuperadminBusiness | null>(null);
const deleting = ref(false);

async function confirmDelete() {
  if (!deleteTarget.value) return;
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
  <div class="superadmin-businesses-page">
    <div class="page-header mb-5 flex items-center justify-between flex-wrap gap-3">
      <div>
        <h1 class="text-2xl font-bold tracking-tight">{{ t("superadmin.businesses.title") }}</h1>
      </div>
      <Button class="gap-2" @click="showCreateModal = true">
        <Plus :size="16" />
        <span class="hidden sm:inline">{{ t("superadmin.businesses.addBusiness") }}</span>
      </Button>
    </div>

    <p v-if="error" class="error mb-4">{{ error }}</p>

    <div v-if="loading" class="space-y-3">
      <Skeleton v-for="i in 5" :key="i" class="h-14 w-full rounded-lg" />
    </div>

    <EmptyState
      v-else-if="businesses.length === 0"
      :title="t('superadmin.businesses.noBusinessesTitle')"
    >
      <template #icon><Building2 :size="28" /></template>
      <Button class="gap-2" @click="showCreateModal = true">
        <Plus :size="16" />
        <span>{{ t("superadmin.businesses.addBusiness") }}</span>
      </Button>
    </EmptyState>

    <div v-else class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
      <Table>
        <TableHeader>
          <TableRow class="bg-muted/50">
            <TableHead>{{ t("superadmin.businesses.name") }}</TableHead>
            <TableHead>{{ t("superadmin.businesses.owner") }}</TableHead>
            <TableHead>{{ t("superadmin.businesses.status") }}</TableHead>
            <TableHead>{{ t("superadmin.businesses.expires") }}</TableHead>
            <TableHead>{{ t("superadmin.businesses.aiStatus") }}</TableHead>
            <TableHead>{{ t("superadmin.businesses.cost30d") }}</TableHead>
            <TableHead class="text-right">{{ t("superadmin.businesses.actions") }}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow v-for="b in businesses" :key="b.id" class="hover:bg-muted/40 transition-colors">
            <TableCell class="font-semibold text-foreground">{{ b.name }}</TableCell>
            <TableCell class="text-muted-foreground text-sm">{{ b.owner_email }}</TableCell>
            <TableCell>
              <Badge
                variant="outline"
                class="text-xs font-semibold"
                :class="b.subscription_active
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
                  : 'bg-[var(--hot-soft)] text-[var(--hot)] border-[color-mix(in_srgb,var(--hot)_30%,transparent)]'"
              >
                {{ b.subscription_active ? t("superadmin.businesses.active") : t("superadmin.businesses.expired") }}
              </Badge>
            </TableCell>
            <TableCell class="text-sm text-muted-foreground">{{ fmtDate(b.subscription_expires_at) }}</TableCell>
            <TableCell>
              <div class="flex flex-col gap-1">
                <label class="inline-flex items-center gap-2 cursor-pointer" :title="t('superadmin.businesses.platformSwitchTitle')">
                  <Switch
                    :model-value="!b.ai_suspended"
                    :disabled="togglingAiId === b.id"
                    @update:model-value="toggleSuspension(b)"
                  />
                  <span class="text-xs font-medium" :class="b.ai_suspended ? 'text-destructive' : 'text-muted-foreground'">
                    {{ b.ai_suspended ? t("superadmin.businesses.aiSuspended") : t("superadmin.businesses.aiAllowed") }}
                  </span>
                </label>
                <span class="text-[11px] text-muted-foreground">
                  {{ b.ai_enabled ? t("superadmin.businesses.ownerAiOn") : t("superadmin.businesses.ownerAiOff") }}
                </span>
              </div>
            </TableCell>
            <TableCell class="font-mono text-sm">{{ fmtUsd(b.cost_last_30d_usd) }}</TableCell>
            <TableCell class="text-right">
              <div class="flex items-center justify-end gap-1.5">
                <Button variant="outline" size="sm" class="gap-1.5 h-8 text-xs" @click="openExtend(b)">
                  <CalendarClock :size="14" />
                  <span>{{ t("superadmin.businesses.extend") }}</span>
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  class="h-8 w-8 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                  :title="t('superadmin.businesses.delete')"
                  @click="deleteTarget = b"
                >
                  <Trash2 :size="15" />
                </Button>
              </div>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>

    <!-- Create business modal -->
    <Dialog v-model:open="showCreateModal">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle class="flex items-center gap-2">
            <Plus :size="18" /><span>{{ t("superadmin.businesses.createModalTitle") }}</span>
          </DialogTitle>
        </DialogHeader>
        <form class="space-y-4 pt-2" @submit.prevent="submitCreate">
          <div class="space-y-1.5">
            <Label for="create-biz-name">{{ t("superadmin.businesses.businessNameLabel") }}</Label>
            <Input id="create-biz-name" v-model="createForm.business_name" type="text" required />
          </div>
          <div class="space-y-1.5">
            <Label for="create-biz-email">{{ t("superadmin.businesses.ownerEmailLabel") }}</Label>
            <Input id="create-biz-email" v-model="createForm.email" type="email" autocomplete="off" required />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div class="space-y-1.5">
              <Label for="create-biz-pwd">{{ t("superadmin.businesses.passwordLabel") }}</Label>
              <Input id="create-biz-pwd" v-model="createForm.password" type="password" autocomplete="new-password" required />
            </div>
            <div class="space-y-1.5">
              <Label for="create-biz-trial">{{ t("superadmin.businesses.trialDaysLabel") }}</Label>
              <Input id="create-biz-trial" v-model.number="createForm.trial_days" type="number" min="0" max="365" required />
            </div>
          </div>
          <div class="flex items-center justify-end gap-3 pt-2">
            <Button type="button" variant="outline" @click="showCreateModal = false">{{ t("common.cancel") }}</Button>
            <Button type="submit" :disabled="creating" class="gap-2">
              <Check :size="16" />
              <span>{{ creating ? t("superadmin.businesses.creating") : t("superadmin.businesses.create") }}</span>
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>

    <!-- Extend subscription modal -->
    <Dialog :open="!!extendTarget" @update:open="(v) => { if (!v) extendTarget = null }">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle class="flex items-center gap-2">
            <CalendarClock :size="18" /><span>{{ t("superadmin.businesses.extendModalTitle") }} — {{ extendTarget?.name }}</span>
          </DialogTitle>
        </DialogHeader>
        <form class="space-y-4 pt-2" @submit.prevent="submitExtend">
          <div class="space-y-1.5">
            <Label for="extend-expiry">{{ t("superadmin.businesses.newExpiryLabel") }}</Label>
            <Input id="extend-expiry" v-model="extendForm.subscription_expires_at" type="date" required />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div class="space-y-1.5">
              <Label for="extend-amount">{{ t("superadmin.businesses.paymentAmountLabel") }}</Label>
              <Input id="extend-amount" v-model.number="extendForm.payment_amount" type="number" min="0" step="1000" />
            </div>
            <div class="space-y-1.5">
              <Label>{{ t("superadmin.businesses.paymentCurrencyLabel") }}</Label>
              <Select v-model="extendForm.payment_currency">
                <SelectTrigger class="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="UZS">UZS</SelectItem>
                  <SelectItem value="USD">USD</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div class="space-y-1.5">
            <Label for="extend-note">{{ t("superadmin.businesses.paymentNoteLabel") }}</Label>
            <Input id="extend-note" v-model="extendForm.payment_note" type="text" />
          </div>
          <div class="flex items-center justify-end gap-3 pt-2">
            <Button type="button" variant="outline" @click="extendTarget = null">{{ t("common.cancel") }}</Button>
            <Button type="submit" :disabled="extending" class="gap-2">
              <Check :size="16" />
              <span>{{ extending ? t("superadmin.businesses.saving") : t("superadmin.businesses.save") }}</span>
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>

    <!-- Delete confirmation -->
    <Dialog :open="!!deleteTarget" @update:open="(v) => { if (!v) deleteTarget = null }">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle class="text-destructive">{{ t("superadmin.businesses.deleteModalTitle") }}</DialogTitle>
        </DialogHeader>
        <p class="text-sm text-foreground mb-2">
          {{ t("superadmin.businesses.deleteConfirm", { name: deleteTarget?.name ?? "" }) }}
        </p>
        <div class="flex items-center justify-end gap-3 mt-2">
          <Button variant="outline" @click="deleteTarget = null">{{ t("superadmin.businesses.cancel") }}</Button>
          <Button variant="destructive" class="gap-2" :disabled="deleting" @click="confirmDelete">
            <Trash2 :size="16" />
            {{ deleting ? t("superadmin.businesses.deleting") : t("superadmin.businesses.delete") }}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
