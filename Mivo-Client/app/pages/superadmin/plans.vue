<script setup lang="ts">
import { Plus, Check, Pencil, Star, Tags, Trash2 } from "@lucide/vue";
import type { Plan, PlanInput } from "~/types/api";

definePageMeta({ layout: "superadmin" });

const api = useSuperadminApi();
const { t } = useI18n();

const plans = ref<Plan[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);

function errorText(err: unknown, fallbackKey: string): string {
  return err instanceof Error && err.message ? err.message : t(fallbackKey);
}

async function load() {
  loading.value = true;
  try {
    plans.value = await api.listPlans();
  } catch (err) {
    error.value = errorText(err, "superadmin.plans.loadError");
  } finally {
    loading.value = false;
  }
}
onMounted(load);

function fmtPrice(plan: Plan) {
  return `${new Intl.NumberFormat("uz-UZ", { maximumFractionDigits: 2 }).format(plan.price)} ${plan.currency}`;
}
function fmtLimit(limit: number | null) {
  return limit === null ? t("superadmin.plans.unlimited") : new Intl.NumberFormat("uz-UZ").format(limit);
}

// One dialog for create and edit; the limit field is text so "empty" can
// mean unlimited.
interface PlanForm {
  name: string;
  price: number;
  currency: string;
  limit: string;
  sort_order: number;
  is_active: boolean;
  is_default: boolean;
}
const editing = ref<Plan | null>(null);
const showForm = ref(false);
const saving = ref(false);
const form = ref<PlanForm>(blankForm());

function blankForm(): PlanForm {
  return { name: "", price: 0, currency: "USD", limit: "", sort_order: plans.value.length, is_active: true, is_default: false };
}

function openCreate() {
  editing.value = null;
  form.value = blankForm();
  showForm.value = true;
}

function openEdit(plan: Plan) {
  editing.value = plan;
  form.value = {
    name: plan.name,
    price: plan.price,
    currency: plan.currency,
    limit: plan.monthly_ai_replies === null ? "" : String(plan.monthly_ai_replies),
    sort_order: plan.sort_order,
    is_active: plan.is_active,
    is_default: plan.is_default,
  };
  showForm.value = true;
}

// --- Delete: only a plan no business is on ---
const deleteTarget = ref<Plan | null>(null);
const deleting = ref(false);

async function confirmDelete() {
  if (!deleteTarget.value) return;
  deleting.value = true;
  error.value = null;
  try {
    await api.deletePlan(deleteTarget.value.id);
    deleteTarget.value = null;
    await load();
  } catch (err) {
    error.value = errorText(err, "superadmin.plans.deleteError");
  } finally {
    deleting.value = false;
  }
}

async function submit() {
  saving.value = true;
  error.value = null;
  const limit = String(form.value.limit ?? "").trim();
  const body: PlanInput = {
    name: form.value.name.trim(),
    price: Number(form.value.price) || 0,
    currency: form.value.currency,
    monthly_ai_replies: limit === "" ? null : Math.max(1, Math.round(Number(limit))),
    sort_order: Number(form.value.sort_order) || 0,
    is_active: form.value.is_active,
    is_default: form.value.is_default,
  };
  try {
    if (editing.value) await api.updatePlan(editing.value.id, body);
    else await api.createPlan(body);
    showForm.value = false;
    await load();
  } catch (err) {
    error.value = errorText(err, "superadmin.plans.saveError");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <TooltipProvider :delay-duration="200">
  <div class="superadmin-plans-page">
    <div class="page-header mb-5 flex items-start justify-between flex-wrap gap-3">
      <div class="max-w-2xl">
        <h1 class="text-2xl font-bold tracking-tight">{{ t("superadmin.plans.title") }}</h1>
        <p class="text-sm text-muted-foreground mt-1">{{ t("superadmin.plans.subtitle") }}</p>
      </div>
      <Button class="gap-2" @click="openCreate">
        <Plus :size="16" />
        <span class="hidden sm:inline">{{ t("superadmin.plans.addPlan") }}</span>
      </Button>
    </div>

    <p v-if="error" class="error mb-4">{{ error }}</p>

    <div v-if="loading" class="space-y-3">
      <Skeleton v-for="i in 4" :key="i" class="h-14 w-full rounded-lg" />
    </div>

    <EmptyState v-else-if="plans.length === 0" :title="t('superadmin.plans.empty')">
      <template #icon><Tags :size="28" /></template>
      <Button class="gap-2" @click="openCreate">
        <Plus :size="16" />
        <span>{{ t("superadmin.plans.addPlan") }}</span>
      </Button>
    </EmptyState>

    <div v-else class="rounded-xl border border-border bg-card overflow-x-auto shadow-2xs">
      <Table>
        <TableHeader>
          <TableRow class="bg-muted/50">
            <TableHead>{{ t("superadmin.plans.name") }}</TableHead>
            <TableHead>{{ t("superadmin.plans.price") }}</TableHead>
            <TableHead>{{ t("superadmin.plans.limit") }}</TableHead>
            <TableHead>{{ t("superadmin.plans.businesses") }}</TableHead>
            <TableHead class="text-center">{{ t("superadmin.plans.status") }}</TableHead>
            <TableHead class="w-24"><span class="sr-only">{{ t("superadmin.plans.actions") }}</span></TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow v-for="p in plans" :key="p.id" :class="{ 'opacity-60': !p.is_active }">
            <TableCell>
              <div class="flex items-center gap-2">
                <span class="font-semibold text-foreground">{{ p.name }}</span>
                <Tooltip v-if="p.is_default">
                  <TooltipTrigger as-child>
                    <Star :size="14" class="text-amber-500 fill-amber-500 shrink-0" :aria-label="t('superadmin.plans.defaultBadge')" />
                  </TooltipTrigger>
                  <TooltipContent>{{ t("superadmin.plans.defaultBadge") }}</TooltipContent>
                </Tooltip>
              </div>
            </TableCell>
            <TableCell class="font-mono text-sm tabular-nums">{{ fmtPrice(p) }}</TableCell>
            <TableCell class="font-mono text-sm tabular-nums">{{ fmtLimit(p.monthly_ai_replies) }}</TableCell>
            <TableCell class="text-sm tabular-nums">{{ p.businesses_count }}</TableCell>
            <TableCell class="text-center">
              <Tooltip>
                <TooltipTrigger as-child>
                  <span
                    class="inline-block size-2.5 rounded-full cursor-default"
                    :class="p.is_active ? 'bg-emerald-500' : 'bg-muted-foreground/40'"
                    role="img"
                    :aria-label="p.is_active ? t('superadmin.plans.active') : t('superadmin.plans.inactive')"
                  />
                </TooltipTrigger>
                <TooltipContent>{{ p.is_active ? t("superadmin.plans.active") : t("superadmin.plans.inactive") }}</TooltipContent>
              </Tooltip>
            </TableCell>
            <TableCell>
              <div class="flex items-center justify-end gap-1">
                <Tooltip>
                  <TooltipTrigger as-child>
                    <Button variant="ghost" size="icon" class="h-8 w-8" :aria-label="t('superadmin.plans.edit')" @click="openEdit(p)">
                      <Pencil :size="15" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>{{ t("superadmin.plans.edit") }}</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger as-child>
                    <!-- A disabled button gets no hover events; the span carries the tooltip. -->
                    <span class="inline-flex">
                      <Button
                        variant="ghost"
                        size="icon"
                        class="h-8 w-8 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                        :disabled="p.businesses_count > 0"
                        :aria-label="t('superadmin.plans.delete')"
                        @click="deleteTarget = p"
                      >
                        <Trash2 :size="15" />
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {{ p.businesses_count > 0 ? t("superadmin.plans.deleteBlocked") : t("superadmin.plans.delete") }}
                  </TooltipContent>
                </Tooltip>
              </div>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>

    <Dialog v-model:open="showForm">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{{ editing ? t("superadmin.plans.editTitle") : t("superadmin.plans.createTitle") }}</DialogTitle>
        </DialogHeader>
        <form class="space-y-4 pt-2" @submit.prevent="submit">
          <div class="space-y-1.5">
            <Label for="plan-name">{{ t("superadmin.plans.nameLabel") }}</Label>
            <Input id="plan-name" v-model="form.name" type="text" maxlength="100" required />
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div class="space-y-1.5">
              <Label for="plan-price">{{ t("superadmin.plans.priceLabel") }}</Label>
              <Input id="plan-price" v-model.number="form.price" type="number" min="0" step="any" required />
            </div>
            <div class="space-y-1.5">
              <Label>{{ t("superadmin.plans.currencyLabel") }}</Label>
              <Select v-model="form.currency">
                <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="USD">USD</SelectItem>
                  <SelectItem value="UZS">UZS</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div class="grid grid-cols-[1fr_auto] gap-3">
            <div class="space-y-1.5">
              <Label for="plan-limit">{{ t("superadmin.plans.limitLabel") }}</Label>
              <Input id="plan-limit" v-model="form.limit" type="number" min="1" step="1" inputmode="numeric" />
              <p class="text-xs text-muted-foreground">{{ t("superadmin.plans.limitHint") }}</p>
            </div>
            <div class="space-y-1.5 w-24">
              <Label for="plan-order">{{ t("superadmin.plans.orderLabel") }}</Label>
              <Input id="plan-order" v-model.number="form.sort_order" type="number" min="0" max="1000" />
            </div>
          </div>
          <label class="flex items-center justify-between gap-3 cursor-pointer">
            <span class="text-sm">{{ t("superadmin.plans.activeLabel") }}</span>
            <Switch v-model="form.is_active" />
          </label>
          <label class="flex items-center justify-between gap-3 cursor-pointer">
            <span class="text-sm">{{ t("superadmin.plans.defaultLabel") }}</span>
            <Switch v-model="form.is_default" />
          </label>
          <div class="flex items-center justify-end gap-3 pt-2">
            <Button type="button" variant="outline" @click="showForm = false">{{ t("common.cancel") }}</Button>
            <Button type="submit" :disabled="saving" class="gap-2">
              <Check :size="16" />
              <span>{{ saving ? t("superadmin.plans.saving") : t("superadmin.plans.save") }}</span>
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>

    <Dialog :open="!!deleteTarget" @update:open="(v) => { if (!v) deleteTarget = null }">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle class="text-destructive">{{ t("superadmin.plans.deleteTitle") }}</DialogTitle>
        </DialogHeader>
        <p class="text-sm text-foreground">{{ t("superadmin.plans.deleteConfirm", { name: deleteTarget?.name ?? "" }) }}</p>
        <div class="flex items-center justify-end gap-3 mt-2">
          <Button variant="outline" @click="deleteTarget = null">{{ t("common.cancel") }}</Button>
          <Button variant="destructive" class="gap-2" :disabled="deleting" @click="confirmDelete">
            <Trash2 :size="16" />
            {{ deleting ? t("superadmin.plans.deleting") : t("superadmin.plans.delete") }}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  </div>
  </TooltipProvider>
</template>
