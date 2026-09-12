<script setup lang="ts">
import { toast } from "vue-sonner";
import type { AiFeedback, Business, BusinessUpdate } from "~/types/api";
import {
  Building2,
  Sparkles,
  ShieldCheck,
  ShieldAlert,
  UserCheck,
  Save,
  ChevronDown,
  ChevronUp,
  Bot,
  BrainCircuit,
  Trash2,
  RefreshCw
} from "@lucide/vue";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t } = useI18n();

// The fields this page edits — the only ones it ever sends. PATCH /business
// rejects unknown fields and read-only ones (id, ai_suspended, ...).
const TEXT_FIELDS = [
  "description",
  "target_customers",
  "tone",
  "language",
  "selling_approach",
  "rules_text",
  "discount_policy",
  "delivery_info",
  "payment_info",
  "handoff_instructions",
] as const;
type TextField = (typeof TEXT_FIELDS)[number];
type SettingsForm = Record<TextField, string> & { ai_enabled: boolean };

// Server values (read-only parts shown as-is) and the editable copy.
const business = ref<Business | null>(null);
const form = ref<SettingsForm | null>(null);
const loadError = ref<string | null>(null);
const learnedRules = ref<AiFeedback[]>([]);
const saving = ref(false);
const saved = ref(false);
const error = ref<string | null>(null);

function toForm(b: Business): SettingsForm {
  const f = { ai_enabled: b.ai_enabled } as SettingsForm;
  for (const key of TEXT_FIELDS) f[key] = b[key] ?? "";
  return f;
}

// Only what changed since the last load/save; a cleared field is sent as null.
function changedFields(): BusinessUpdate {
  const patch: BusinessUpdate = {};
  if (!business.value || !form.value) return patch;
  for (const key of TEXT_FIELDS) {
    const next = form.value[key].trim();
    if (next !== (business.value[key] ?? "").trim()) patch[key] = next === "" ? null : next;
  }
  if (form.value.ai_enabled !== business.value.ai_enabled) patch.ai_enabled = form.value.ai_enabled;
  return patch;
}

const openSections = ref<Record<string, boolean>>({
  business: true,
  persona: true,
  policies: true,
  handoff: true,
  memory: true,
});

function toggleSection(sec: string) {
  openSections.value[sec] = !openSections.value[sec];
}

function errorText(err: unknown, fallbackKey: string): string {
  return err instanceof Error && err.message ? err.message : t(fallbackKey);
}

const learnedRulesError = ref<string | null>(null);

async function loadSettings() {
  loadError.value = null;
  try {
    const b = await api.getBusiness();
    business.value = b;
    form.value = toForm(b);
  } catch (err) {
    console.error("Failed to load business settings", err);
    loadError.value = errorText(err, "aiSettings.loadError");
    return;
  }
  learnedRulesError.value = null;
  try {
    learnedRules.value = await api.getLearnedRules();
  } catch (err) {
    console.error("Failed to load learned rules", err);
    learnedRulesError.value = errorText(err, "aiSettings.learnedRulesLoadError");
  }
}

onMounted(loadSettings);

async function onDeleteRule(id: string) {
  try {
    await api.deleteLearnedRule(id);
    learnedRules.value = learnedRules.value.filter((r) => r.id !== id);
  } catch (err) {
    console.error("Failed to delete learned rule", err);
    toast.error(errorText(err, "aiSettings.deleteRuleError"));
  }
}

async function onSave() {
  if (!business.value || !form.value || saving.value) return;
  error.value = null;
  saved.value = false;
  const patch = changedFields();
  if (Object.keys(patch).length === 0) {
    saved.value = true;
    setTimeout(() => { saved.value = false; }, 3000);
    return;
  }
  saving.value = true;
  try {
    const updated = await api.updateBusiness(patch);
    business.value = updated;
    form.value = toForm(updated);
    saved.value = true;
    setTimeout(() => { saved.value = false; }, 3000);
  } catch (err) {
    error.value = errorText(err, "aiSettings.saveError");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="ai-settings-page max-w-7xl mx-auto pb-12 space-y-3">
    <div v-if="error" class="error mb-5">{{ error }}</div>
    <div v-if="saved" class="success mb-5">{{ t("aiSettings.savedSuccess") }}</div>

    <!-- Load Error -->
    <Card v-if="loadError" class="p-6 text-center space-y-3 border-destructive/30">
      <p class="text-sm font-semibold text-foreground">{{ t("aiSettings.loadError") }}</p>
      <p class="text-xs text-muted-foreground">{{ loadError }}</p>
      <Button variant="outline" size="sm" class="gap-1.5" @click="loadSettings">
        <RefreshCw :size="14" />
        <span>{{ t("common.retry") }}</span>
      </Button>
    </Card>

    <!-- Loading Skeleton State -->
    <div v-else-if="!business || !form" class="space-y-3 w-full">
      <Skeleton class="h-20 w-full rounded-xl" />
      <Skeleton class="h-32 w-full rounded-xl" />
      <Skeleton class="h-32 w-full rounded-xl" />
      <Skeleton class="h-32 w-full rounded-xl" />
    </div>

    <!-- 1-Column Sequential Vertical Layout -->
    <div v-else class="flex flex-col gap-3">

      <!-- Platform suspension: the owner's own switch can't override it -->
      <div
        v-if="business.ai_suspended"
        class="p-4 rounded-xl border border-destructive/30 bg-destructive/10 text-destructive flex items-start gap-3"
      >
        <ShieldAlert :size="20" class="shrink-0 mt-0.5" />
        <div class="space-y-0.5">
          <div class="text-sm font-bold">{{ t("aiSettings.suspendedTitle") }}</div>
          <p class="text-xs sm:text-sm text-destructive/90">{{ t("aiSettings.suspendedBody") }}</p>
        </div>
      </div>

      <!-- Master Switch Card -->
      <Card class="p-4 sm:p-5 border-border shadow-xs">
        <label class="flex items-center justify-between gap-3 cursor-pointer">
          <div class="flex items-center gap-3.5 min-w-0 flex-1">
            <div class="h-10 w-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0 shadow-2xs">
              <Bot :size="22" />
            </div>
            <div class="min-w-0">
              <div class="text-base font-bold text-foreground truncate">
                {{ t("aiSettings.enableAi") }}
              </div>
              <div class="text-muted-foreground text-xs sm:text-sm mt-0.5 line-clamp-2">
                {{ t("aiSettings.enableAiDesc") }}
              </div>
            </div>
          </div>
          <Switch v-model="form.ai_enabled" class="shrink-0" />
        </label>
      </Card>

      <!-- Section 1: Business Profile -->
      <div class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <div class="p-4 flex items-center justify-between cursor-pointer hover:bg-muted/40 transition-colors" @click="toggleSection('business')">
          <h3 class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <Building2 :size="19" class="text-primary" />
            <span>{{ t("aiSettings.secBusiness") }}</span>
          </h3>
          <component :is="openSections['business'] ? ChevronUp : ChevronDown" :size="18" class="text-muted-foreground" />
        </div>
        <div v-if="openSections['business']" class="p-5 pt-2 border-t border-border/60 space-y-4">
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.descLabel") }}</Label>
            <Textarea v-model="form.description" maxlength="4000" :rows="3" :placeholder="t('aiSettings.descPlaceholder')" class="bg-background" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.targetLabel") }}</Label>
            <Textarea v-model="form.target_customers" maxlength="4000" :rows="3" :placeholder="t('aiSettings.targetPlaceholder')" class="bg-background" />
          </div>
        </div>
      </div>

      <!-- Section 2: Persona & Style -->
      <div class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <div class="p-4 flex items-center justify-between cursor-pointer hover:bg-muted/40 transition-colors" @click="toggleSection('persona')">
          <h3 class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <Sparkles :size="19" class="text-primary" />
            <span>{{ t("aiSettings.secPersona") }}</span>
          </h3>
          <component :is="openSections['persona'] ? ChevronUp : ChevronDown" :size="18" class="text-muted-foreground" />
        </div>
        <div v-if="openSections['persona']" class="p-5 pt-2 border-t border-border/60 space-y-4">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div class="space-y-1.5">
              <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.toneLabel") }}</Label>
              <Input v-model="form.tone" maxlength="4000" :placeholder="t('aiSettings.tonePlaceholder')" class="bg-background h-10" />
            </div>
            <div class="space-y-1.5">
              <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.langLabel") }}</Label>
              <Input v-model="form.language" maxlength="50" :placeholder="t('aiSettings.langPlaceholder')" class="bg-background h-10" />
            </div>
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.sellingApproachLabel") }}</Label>
            <Textarea v-model="form.selling_approach" maxlength="4000" :rows="3" :placeholder="t('aiSettings.sellingApproachPlaceholder')" class="bg-background" />
          </div>
        </div>
      </div>

      <!-- Section 3: Rules & Policies -->
      <div class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <div class="p-4 flex items-center justify-between cursor-pointer hover:bg-muted/40 transition-colors" @click="toggleSection('policies')">
          <h3 class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <ShieldCheck :size="19" class="text-primary" />
            <span>{{ t("aiSettings.secPolicies") }}</span>
          </h3>
          <component :is="openSections['policies'] ? ChevronUp : ChevronDown" :size="18" class="text-muted-foreground" />
        </div>
        <div v-if="openSections['policies']" class="p-5 pt-2 border-t border-border/60 space-y-4">
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.rulesLabel") }}</Label>
            <Textarea v-model="form.rules_text" maxlength="4000" :rows="3" :placeholder="t('aiSettings.rulesPlaceholder')" class="bg-background" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.discountLabel") }}</Label>
            <Textarea v-model="form.discount_policy" maxlength="4000" :rows="2" :placeholder="t('aiSettings.discountPlaceholder')" class="bg-background" />
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div class="space-y-1.5">
              <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.deliveryLabel") }}</Label>
              <Textarea v-model="form.delivery_info" maxlength="4000" :rows="3" :placeholder="t('aiSettings.deliveryPlaceholder')" class="bg-background" />
            </div>
            <div class="space-y-1.5">
              <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.paymentLabel") }}</Label>
              <Textarea v-model="form.payment_info" maxlength="4000" :rows="3" :placeholder="t('aiSettings.paymentPlaceholder')" class="bg-background" />
            </div>
          </div>
        </div>
      </div>

      <!-- Section 4: Human Handoff -->
      <div class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <div class="p-4 flex items-center justify-between cursor-pointer hover:bg-muted/40 transition-colors" @click="toggleSection('handoff')">
          <h3 class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <UserCheck :size="19" class="text-primary" />
            <span>{{ t("aiSettings.secHandoff") }}</span>
          </h3>
          <component :is="openSections['handoff'] ? ChevronUp : ChevronDown" :size="18" class="text-muted-foreground" />
        </div>
        <div v-if="openSections['handoff']" class="p-5 pt-2 border-t border-border/60 space-y-4">
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.handoffLabel") }}</Label>
            <Textarea v-model="form.handoff_instructions" maxlength="4000" :rows="4" :placeholder="t('aiSettings.handoffPlaceholder')" class="bg-background" />
          </div>
        </div>
      </div>

      <!-- Section 5: Learned AI Rules & Memory -->
      <div class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <div class="p-4 flex items-center justify-between cursor-pointer hover:bg-muted/40 transition-colors" @click="toggleSection('memory')">
          <h3 class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <BrainCircuit :size="19" class="text-primary" />
            <span>{{ t("aiSettings.learnedRulesTitle") }}</span>
          </h3>
          <component :is="openSections['memory'] ? ChevronUp : ChevronDown" :size="18" class="text-muted-foreground" />
        </div>
        <div v-if="openSections['memory']" class="p-5 pt-2 border-t border-border/60 space-y-4">
          <p class="text-sm text-muted-foreground">
            {{ t("aiSettings.learnedRulesDesc") }}
          </p>

          <div v-if="learnedRulesError" class="text-destructive text-sm text-center py-4 bg-destructive/5 rounded-lg border border-destructive/20">
            {{ t("aiSettings.learnedRulesLoadError") }}: {{ learnedRulesError }}
          </div>

          <div v-else-if="learnedRules.length === 0" class="text-muted-foreground text-sm text-center py-6 bg-muted/40 rounded-lg border border-dashed border-border">
            {{ t("aiSettings.noLearnedRules") }}
          </div>

          <div v-else class="flex flex-col gap-3">
            <div
              v-for="rule in learnedRules"
              :key="rule.id"
              class="p-4 rounded-lg bg-muted/40 border border-border flex items-start justify-between gap-4 shadow-2xs"
            >
              <div class="flex-1">
                <div v-if="rule.customer_query" class="text-xs text-muted-foreground mb-1">
                  <strong>{{ t("aiSettings.customerAsked") }}:</strong> "{{ rule.customer_query }}"
                </div>
                <div class="text-sm font-semibold text-foreground">
                  <strong>{{ t("aiSettings.learnedRuleLabel") }}:</strong> {{ rule.correction }}
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                class="h-8 w-8 text-destructive hover:bg-destructive/10 shrink-0 cursor-pointer"
                :title="t('aiSettings.deleteRuleTitle')"
                @click="onDeleteRule(rule.id)"
              >
                <Trash2 :size="15" />
              </Button>
            </div>
          </div>
        </div>
      </div>

      <!-- Bottom Submit Button -->
      <div class="mt-1 flex justify-end">
        <Button size="lg" class="w-full sm:w-auto sm:min-w-[220px] gap-2 cursor-pointer shadow-xs" :disabled="saving" @click="onSave">
          <Save :size="18" />
          <span>{{ saving ? t("aiSettings.saving") : t("aiSettings.saveChanges") }}</span>
        </Button>
      </div>

    </div>
  </div>
</template>
