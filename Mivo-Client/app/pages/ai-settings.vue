<script setup lang="ts">
import { ApiError } from "~/composables/useApi";
import type { AiFeedback, Business } from "~/types/api";
import {
  Building2,
  Sparkles,
  ShieldCheck,
  UserCheck,
  Save,
  ChevronDown,
  ChevronUp,
  Bot,
  BrainCircuit,
  Trash2
} from "lucide-vue-next";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t } = useI18n();
const business = ref<Business | null>(null);
const learnedRules = ref<AiFeedback[]>([]);
const saving = ref(false);
const saved = ref(false);
const error = ref<string | null>(null);

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

onMounted(async () => {
  business.value = await api.getBusiness();
  try {
    learnedRules.value = await api.getLearnedRules();
  } catch (err) {
    console.error("Failed to load learned rules", err);
  }
});

async function onDeleteRule(id: string) {
  try {
    await api.deleteLearnedRule(id);
    learnedRules.value = learnedRules.value.filter((r) => r.id !== id);
  } catch (err) {
    console.error("Failed to delete learned rule", err);
  }
}

async function onSave() {
  if (!business.value) return;
  saving.value = true;
  error.value = null;
  saved.value = false;
  try {
    business.value = await api.updateBusiness(business.value);
    saved.value = true;
    setTimeout(() => { saved.value = false; }, 3000);
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : t("aiSettings.saveError");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="ai-settings-page max-w-7xl mx-auto pb-12 space-y-3">
    <div v-if="error" class="error mb-5">{{ error }}</div>
    <div v-if="saved" class="success mb-5">{{ t("aiSettings.savedSuccess") }}</div>

    <!-- Loading Skeleton State -->
    <div v-if="!business" class="space-y-3 w-full">
      <Skeleton class="h-20 w-full rounded-xl" />
      <Skeleton class="h-32 w-full rounded-xl" />
      <Skeleton class="h-32 w-full rounded-xl" />
      <Skeleton class="h-32 w-full rounded-xl" />
    </div>

    <!-- 1-Column Sequential Vertical Layout -->
    <div v-else class="flex flex-col gap-3">

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
              <div v-if="t('aiSettings.enableAiDesc')" class="text-muted-foreground text-xs sm:text-sm mt-0.5 line-clamp-2">
                {{ t("aiSettings.enableAiDesc") }}
              </div>
            </div>
          </div>
          <Switch :checked="business.ai_enabled" @update:checked="business.ai_enabled = $event" class="shrink-0" />
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
            <Textarea v-model="business.description" :rows="3" :placeholder="t('aiSettings.descPlaceholder')" class="bg-background" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.targetLabel") }}</Label>
            <Textarea v-model="business.target_customers" :rows="3" :placeholder="t('aiSettings.targetPlaceholder')" class="bg-background" />
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
              <Input v-model="business.tone" :placeholder="t('aiSettings.tonePlaceholder')" class="bg-background h-10" />
            </div>
            <div class="space-y-1.5">
              <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.langLabel") }}</Label>
              <Input v-model="business.language" :placeholder="t('aiSettings.langPlaceholder')" class="bg-background h-10" />
            </div>
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.sellingApproachLabel") }}</Label>
            <Textarea v-model="business.selling_approach" :rows="3" :placeholder="t('aiSettings.sellingApproachPlaceholder')" class="bg-background" />
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
            <Textarea v-model="business.rules_text" :rows="3" :placeholder="t('aiSettings.rulesPlaceholder')" class="bg-background" />
          </div>
          <div class="space-y-1.5">
            <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.discountLabel") }}</Label>
            <Textarea v-model="business.discount_policy" :rows="2" :placeholder="t('aiSettings.discountPlaceholder')" class="bg-background" />
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div class="space-y-1.5">
              <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.deliveryLabel") }}</Label>
              <Textarea v-model="business.delivery_info" :rows="3" :placeholder="t('aiSettings.deliveryPlaceholder')" class="bg-background" />
            </div>
            <div class="space-y-1.5">
              <Label class="text-xs font-semibold text-foreground/90">{{ t("aiSettings.paymentLabel") }}</Label>
              <Textarea v-model="business.payment_info" :rows="3" :placeholder="t('aiSettings.paymentPlaceholder')" class="bg-background" />
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
            <Textarea v-model="business.handoff_instructions" :rows="4" :placeholder="t('aiSettings.handoffPlaceholder')" class="bg-background" />
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

          <div v-if="learnedRules.length === 0" class="text-muted-foreground text-sm text-center py-6 bg-muted/40 rounded-lg border border-dashed border-border">
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
