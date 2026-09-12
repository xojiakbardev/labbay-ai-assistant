<script setup lang="ts">
import { toast } from "vue-sonner";
import type { AiFeedback, Business, BusinessUpdate, ReplyDefaults, ReplyTexts } from "~/types/api";
import {
  Building2,
  Sparkles,
  ShieldCheck,
  ShieldAlert,
  UserCheck,
  Save,
  ChevronDown,
  Bot,
  BrainCircuit,
  Trash2,
  RefreshCw,
  MessageSquareText,
  RotateCcw,
  Loader2,
} from "@lucide/vue";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t, locale } = useI18n();

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
type SettingsForm = Record<TextField, string>;

// Server values (read-only parts shown as-is) and the editable copy.
const business = ref<Business | null>(null);
const form = ref<SettingsForm | null>(null);
const loadError = ref<string | null>(null);
const learnedRules = ref<AiFeedback[]>([]);
const saving = ref(false);

function toForm(b: Business): SettingsForm {
  const f = {} as SettingsForm;
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
  return patch;
}

const isDirty = computed(() => Object.keys(changedFields()).length > 0);

const openSections = ref<Record<string, boolean>>({
  business: true,
  persona: true,
  policies: true,
  handoff: true,
  replies: true,
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
    applySavedReplies(b.reply_texts ?? {});
  } catch (err) {
    console.error("Failed to load business settings", err);
    loadError.value = errorText(err, "aiSettings.loadError");
    return;
  }
  loadReplyDefaults();
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

// --- The master switch saves on its own, right away ---------------------------

const togglingAi = ref(false);

async function onToggleAi(value: boolean) {
  if (!business.value || togglingAi.value) return;
  togglingAi.value = true;
  try {
    const updated = await api.updateBusiness({ ai_enabled: value });
    // Only the switch: unsaved edits in the form below stay as they are.
    business.value = { ...business.value, ai_enabled: updated.ai_enabled, ai_suspended: updated.ai_suspended };
    toast.success(updated.ai_enabled ? t("aiSettings.aiTurnedOn") : t("aiSettings.aiTurnedOff"));
  } catch (err) {
    toast.error(errorText(err, "aiSettings.toggleError"));
  } finally {
    togglingAi.value = false;
  }
}

// --- The text settings: one Save for all of them ------------------------------

async function onSave() {
  if (!business.value || !form.value || saving.value || !isDirty.value) return;
  saving.value = true;
  try {
    const updated = await api.updateBusiness(changedFields());
    business.value = updated;
    form.value = toForm(updated);
    toast.success(t("aiSettings.savedSuccess"));
  } catch (err) {
    toast.error(errorText(err, "aiSettings.saveError"));
  } finally {
    saving.value = false;
  }
}

function discardChanges() {
  if (business.value) form.value = toForm(business.value);
}

// --- Ready-made replies ----------------------------------------------------------

// The order the owner reads them in; any key the server adds later follows.
const REPLY_ORDER = [
  "handoff",
  "phone_received",
  "discount_unconfirmed",
  "price_unconfirmed",
  "media_unseen",
  "photo_unreadable",
  "neutral",
  "follow_up",
  "follow_up_generic",
];
const LANGUAGE_NAMES: Record<string, string> = { uz: "O'zbekcha", ru: "Русский", en: "English" };

const replyDefaults = ref<ReplyDefaults | null>(null);
const replyDefaultsError = ref<string | null>(null);
// What's saved on the server (overrides only) and the editable matrix
// key -> language -> text ("" = use the default).
const savedReplies = ref<ReplyTexts>({});
const replyDraft = ref<ReplyTexts>({});
const replyLang = ref("uz");
const openReply = ref<string | null>(null);
const savingReplies = ref(false);

const replyKeys = computed(() => {
  const keys = Object.keys(replyDefaults.value?.replies ?? {});
  return [...REPLY_ORDER.filter((k) => keys.includes(k)), ...keys.filter((k) => !REPLY_ORDER.includes(k))];
});
const replyLanguages = computed(() => replyDefaults.value?.languages ?? []);

function applySavedReplies(saved: ReplyTexts) {
  savedReplies.value = saved;
  const draft: ReplyTexts = {};
  for (const [key, texts] of Object.entries(saved)) draft[key] = { ...texts };
  replyDraft.value = draft;
}

async function loadReplyDefaults() {
  replyDefaultsError.value = null;
  try {
    const defaults = await api.getReplyDefaults();
    replyDefaults.value = defaults;
    replyLang.value = defaults.languages.includes(locale.value) ? locale.value : defaults.languages[0] ?? "uz";
  } catch (err) {
    console.error("Failed to load reply defaults", err);
    replyDefaultsError.value = errorText(err, "aiSettings.repliesLoadError");
  }
}

function draftText(key: string, lang: string): string {
  return replyDraft.value[key]?.[lang] ?? "";
}

function setDraftText(key: string, lang: string, text: string) {
  replyDraft.value = { ...replyDraft.value, [key]: { ...(replyDraft.value[key] ?? {}), [lang]: text } };
}

function defaultText(key: string, lang: string): string {
  return replyDefaults.value?.replies[key]?.[lang] ?? "";
}

/** The overrides as the server stores them: trimmed, empty ones dropped, in a fixed order. */
function normalizeReplies(texts: ReplyTexts): ReplyTexts {
  const out: ReplyTexts = {};
  for (const key of Object.keys(texts).sort()) {
    for (const lang of Object.keys(texts[key] ?? {}).sort()) {
      const text = (texts[key]![lang] ?? "").trim();
      if (text) (out[key] ??= {})[lang] = text;
    }
  }
  return out;
}

const repliesDirty = computed(
  () => JSON.stringify(normalizeReplies(replyDraft.value)) !== JSON.stringify(normalizeReplies(savedReplies.value))
);

function isCustomized(key: string, lang: string): boolean {
  return draftText(key, lang).trim() !== "";
}

async function onSaveReplies() {
  if (savingReplies.value || !repliesDirty.value) return;
  savingReplies.value = true;
  try {
    // The whole set: the server replaces every override with it.
    const updated = await api.updateBusiness({ reply_texts: normalizeReplies(replyDraft.value) });
    if (business.value) business.value = { ...business.value, reply_texts: updated.reply_texts ?? {} };
    applySavedReplies(updated.reply_texts ?? {});
    toast.success(t("aiSettings.repliesSaved"));
  } catch (err) {
    toast.error(errorText(err, "aiSettings.repliesSaveError"));
  } finally {
    savingReplies.value = false;
  }
}

// --- Don't lose unsaved edits -------------------------------------------------

const hasUnsaved = computed(() => isDirty.value || repliesDirty.value);

onBeforeRouteLeave(() => {
  if (hasUnsaved.value && !window.confirm(t("aiSettings.unsavedLeave"))) return false;
});

function onBeforeUnload(e: BeforeUnloadEvent) {
  if (!hasUnsaved.value) return;
  e.preventDefault();
  e.returnValue = "";
}

onMounted(() => window.addEventListener("beforeunload", onBeforeUnload));
onUnmounted(() => window.removeEventListener("beforeunload", onBeforeUnload));
</script>

<template>
  <div class="ai-settings-page max-w-7xl mx-auto pb-4 space-y-3">
    <!-- Load Error -->
    <Card v-if="loadError" class="p-6 text-center space-y-3 border-destructive/30">
      <p class="text-sm font-semibold text-foreground">{{ t("aiSettings.loadError") }}</p>
      <p class="text-xs text-muted-foreground">{{ loadError }}</p>
      <Button variant="outline" class="h-10 gap-1.5 mx-auto" @click="loadSettings">
        <RefreshCw :size="14" />
        <span>{{ t("common.retry") }}</span>
      </Button>
    </Card>

    <!-- Loading Skeleton State -->
    <div v-else-if="!business || !form" class="space-y-3 w-full" aria-busy="true">
      <Skeleton class="h-20 w-full rounded-xl" />
      <Skeleton class="h-32 w-full rounded-xl" />
      <Skeleton class="h-32 w-full rounded-xl" />
      <Skeleton class="h-32 w-full rounded-xl" />
    </div>

    <div v-else class="flex flex-col gap-3">
      <!-- Platform suspension: the owner's own switch can't override it -->
      <div
        v-if="business.ai_suspended"
        class="p-4 rounded-xl border border-destructive/30 bg-destructive/10 text-destructive flex items-start gap-3"
        role="alert"
      >
        <ShieldAlert :size="20" class="shrink-0 mt-0.5" />
        <div class="space-y-0.5">
          <div class="text-sm font-bold">{{ t("aiSettings.suspendedTitle") }}</div>
          <p class="text-sm">{{ t("aiSettings.suspendedBody") }}</p>
        </div>
      </div>

      <!-- Master switch — takes effect immediately -->
      <Card class="p-4 sm:p-5 border-border shadow-xs">
        <div class="flex items-center justify-between gap-3">
          <div class="flex items-center gap-3.5 min-w-0 flex-1">
            <div class="h-10 w-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0">
              <Bot :size="22" />
            </div>
            <div class="min-w-0">
              <label for="ai-enabled-switch" class="block text-base font-semibold text-foreground cursor-pointer">
                {{ t("aiSettings.enableAi") }}
              </label>
              <p class="text-muted-foreground text-sm mt-0.5">
                {{ business.ai_enabled ? t("aiSettings.enableAiDesc") : t("aiSettings.enableAiOffDesc") }}
              </p>
            </div>
          </div>
          <Loader2 v-if="togglingAi" :size="16" class="animate-spin text-muted-foreground shrink-0" />
          <Switch
            id="ai-enabled-switch"
            :model-value="business.ai_enabled"
            :disabled="togglingAi"
            class="shrink-0"
            @update:model-value="onToggleAi"
          />
        </div>
      </Card>

      <!-- Section 1: Business profile -->
      <section class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <button
          type="button"
          class="w-full min-h-14 p-4 flex items-center justify-between gap-2 text-left hover:bg-muted/40 transition-colors"
          :aria-expanded="openSections['business']"
          @click="toggleSection('business')"
        >
          <span class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <Building2 :size="19" class="text-primary" />
            <span>{{ t("aiSettings.secBusiness") }}</span>
          </span>
          <ChevronDown :size="18" class="text-muted-foreground transition-transform" :class="{ 'rotate-180': openSections['business'] }" />
        </button>
        <div v-if="openSections['business']" class="p-4 sm:p-5 pt-2 border-t border-border/60 space-y-4">
          <div class="space-y-1.5">
            <Label for="ai-description" class="text-sm font-medium text-foreground">{{ t("aiSettings.descLabel") }}</Label>
            <Textarea id="ai-description" v-model="form.description" maxlength="4000" :rows="3" :placeholder="t('aiSettings.descPlaceholder')" class="bg-background" />
          </div>
          <div class="space-y-1.5">
            <Label for="ai-target" class="text-sm font-medium text-foreground">{{ t("aiSettings.targetLabel") }}</Label>
            <Textarea id="ai-target" v-model="form.target_customers" maxlength="4000" :rows="3" :placeholder="t('aiSettings.targetPlaceholder')" class="bg-background" />
          </div>
        </div>
      </section>

      <!-- Section 2: Persona & style -->
      <section class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <button
          type="button"
          class="w-full min-h-14 p-4 flex items-center justify-between gap-2 text-left hover:bg-muted/40 transition-colors"
          :aria-expanded="openSections['persona']"
          @click="toggleSection('persona')"
        >
          <span class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <Sparkles :size="19" class="text-primary" />
            <span>{{ t("aiSettings.secPersona") }}</span>
          </span>
          <ChevronDown :size="18" class="text-muted-foreground transition-transform" :class="{ 'rotate-180': openSections['persona'] }" />
        </button>
        <div v-if="openSections['persona']" class="p-4 sm:p-5 pt-2 border-t border-border/60 space-y-4">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div class="space-y-1.5">
              <Label for="ai-tone" class="text-sm font-medium text-foreground">{{ t("aiSettings.toneLabel") }}</Label>
              <Input id="ai-tone" v-model="form.tone" maxlength="4000" :placeholder="t('aiSettings.tonePlaceholder')" class="bg-background h-10" />
            </div>
            <div class="space-y-1.5">
              <Label for="ai-language" class="text-sm font-medium text-foreground">{{ t("aiSettings.langLabel") }}</Label>
              <Input id="ai-language" v-model="form.language" maxlength="50" :placeholder="t('aiSettings.langPlaceholder')" class="bg-background h-10" />
            </div>
          </div>
          <div class="space-y-1.5">
            <Label for="ai-selling" class="text-sm font-medium text-foreground">{{ t("aiSettings.sellingApproachLabel") }}</Label>
            <Textarea id="ai-selling" v-model="form.selling_approach" maxlength="4000" :rows="3" :placeholder="t('aiSettings.sellingApproachPlaceholder')" class="bg-background" />
          </div>
        </div>
      </section>

      <!-- Section 3: Rules & policies -->
      <section class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <button
          type="button"
          class="w-full min-h-14 p-4 flex items-center justify-between gap-2 text-left hover:bg-muted/40 transition-colors"
          :aria-expanded="openSections['policies']"
          @click="toggleSection('policies')"
        >
          <span class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <ShieldCheck :size="19" class="text-primary" />
            <span>{{ t("aiSettings.secPolicies") }}</span>
          </span>
          <ChevronDown :size="18" class="text-muted-foreground transition-transform" :class="{ 'rotate-180': openSections['policies'] }" />
        </button>
        <div v-if="openSections['policies']" class="p-4 sm:p-5 pt-2 border-t border-border/60 space-y-4">
          <div class="space-y-1.5">
            <Label for="ai-rules" class="text-sm font-medium text-foreground">{{ t("aiSettings.rulesLabel") }}</Label>
            <Textarea id="ai-rules" v-model="form.rules_text" maxlength="4000" :rows="3" :placeholder="t('aiSettings.rulesPlaceholder')" class="bg-background" />
          </div>
          <div class="space-y-1.5">
            <Label for="ai-discount" class="text-sm font-medium text-foreground">{{ t("aiSettings.discountLabel") }}</Label>
            <Textarea id="ai-discount" v-model="form.discount_policy" maxlength="4000" :rows="2" :placeholder="t('aiSettings.discountPlaceholder')" class="bg-background" />
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div class="space-y-1.5">
              <Label for="ai-delivery" class="text-sm font-medium text-foreground">{{ t("aiSettings.deliveryLabel") }}</Label>
              <Textarea id="ai-delivery" v-model="form.delivery_info" maxlength="4000" :rows="3" :placeholder="t('aiSettings.deliveryPlaceholder')" class="bg-background" />
            </div>
            <div class="space-y-1.5">
              <Label for="ai-payment" class="text-sm font-medium text-foreground">{{ t("aiSettings.paymentLabel") }}</Label>
              <Textarea id="ai-payment" v-model="form.payment_info" maxlength="4000" :rows="3" :placeholder="t('aiSettings.paymentPlaceholder')" class="bg-background" />
            </div>
          </div>
        </div>
      </section>

      <!-- Section 4: Human handoff -->
      <section class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <button
          type="button"
          class="w-full min-h-14 p-4 flex items-center justify-between gap-2 text-left hover:bg-muted/40 transition-colors"
          :aria-expanded="openSections['handoff']"
          @click="toggleSection('handoff')"
        >
          <span class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <UserCheck :size="19" class="text-primary" />
            <span>{{ t("aiSettings.secHandoff") }}</span>
          </span>
          <ChevronDown :size="18" class="text-muted-foreground transition-transform" :class="{ 'rotate-180': openSections['handoff'] }" />
        </button>
        <div v-if="openSections['handoff']" class="p-4 sm:p-5 pt-2 border-t border-border/60 space-y-4">
          <div class="space-y-1.5">
            <Label for="ai-handoff" class="text-sm font-medium text-foreground">{{ t("aiSettings.handoffLabel") }}</Label>
            <Textarea id="ai-handoff" v-model="form.handoff_instructions" maxlength="4000" :rows="4" :placeholder="t('aiSettings.handoffPlaceholder')" class="bg-background" />
          </div>
        </div>
      </section>

      <!-- Unsaved text settings: the one Save for sections 1-4, always in reach -->
      <div
        v-if="isDirty"
        class="sticky bottom-[calc(var(--mobile-nav-h)+0.75rem)] min-[901px]:bottom-4 z-20 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-primary/30 bg-card/95 px-4 py-3 shadow-lg backdrop-blur"
        role="status"
      >
        <span class="text-sm font-medium text-foreground">{{ t("aiSettings.unsavedChanges") }}</span>
        <div class="flex items-center gap-2 ml-auto">
          <Button variant="ghost" class="h-10" :disabled="saving" @click="discardChanges">{{ t("common.cancel") }}</Button>
          <Button class="h-10 gap-2 px-4" :disabled="saving" @click="onSave">
            <Loader2 v-if="saving" :size="16" class="animate-spin" />
            <Save v-else :size="16" />
            <span>{{ saving ? t("aiSettings.saving") : t("aiSettings.saveChanges") }}</span>
          </Button>
        </div>
      </div>

      <!-- Section 5: Ready-made replies (saved on their own) -->
      <section class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <button
          type="button"
          class="w-full min-h-14 p-4 flex items-center justify-between gap-2 text-left hover:bg-muted/40 transition-colors"
          :aria-expanded="openSections['replies']"
          @click="toggleSection('replies')"
        >
          <span class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <MessageSquareText :size="19" class="text-primary" />
            <span>{{ t("aiSettings.repliesTitle") }}</span>
          </span>
          <ChevronDown :size="18" class="text-muted-foreground transition-transform" :class="{ 'rotate-180': openSections['replies'] }" />
        </button>
        <div v-if="openSections['replies']" class="p-4 sm:p-5 pt-2 border-t border-border/60 space-y-3">
          <p class="text-sm text-muted-foreground">{{ t("aiSettings.repliesDesc") }}</p>

          <div v-if="replyDefaultsError" class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-sm text-destructive">
            <span>{{ t("aiSettings.repliesLoadError") }}: {{ replyDefaultsError }}</span>
            <Button variant="outline" class="h-9 gap-1.5" @click="loadReplyDefaults">
              <RefreshCw :size="14" />
              <span>{{ t("common.retry") }}</span>
            </Button>
          </div>

          <div v-else-if="!replyDefaults" class="space-y-2" aria-busy="true">
            <Skeleton v-for="i in 4" :key="i" class="h-14 w-full rounded-lg" />
          </div>

          <template v-else>
            <!-- Which language is being edited (for the whole section) -->
            <div class="flex flex-wrap items-center gap-2">
              <span id="reply-lang-label" class="text-sm font-medium text-foreground">{{ t("aiSettings.repliesLanguage") }}</span>
              <div class="mode-toggle" role="radiogroup" aria-labelledby="reply-lang-label">
                <button
                  v-for="lang in replyLanguages"
                  :key="lang"
                  type="button"
                  role="radio"
                  class="mode-option"
                  :aria-checked="replyLang === lang"
                  @click="replyLang = lang"
                >
                  {{ LANGUAGE_NAMES[lang] ?? lang.toUpperCase() }}
                </button>
              </div>
            </div>

            <ul class="divide-y divide-border rounded-lg border border-border">
              <li v-for="key in replyKeys" :key="key">
                <button
                  type="button"
                  class="w-full min-h-14 px-3.5 py-2.5 flex items-start justify-between gap-3 text-left hover:bg-muted/40 transition-colors"
                  :aria-expanded="openReply === key"
                  :aria-controls="`reply-panel-${key}`"
                  @click="openReply = openReply === key ? null : key"
                >
                  <span class="min-w-0 flex-1">
                    <span class="flex flex-wrap items-center gap-2">
                      <span class="text-sm font-semibold text-foreground">{{ REPLY_ORDER.includes(key) ? t(`aiSettings.replies.${key}.title`) : key }}</span>
                      <span
                        class="rounded-full px-2 py-0.5 text-[11px] font-medium"
                        :class="isCustomized(key, replyLang) ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'"
                      >
                        {{ isCustomized(key, replyLang) ? t("aiSettings.replyCustomized") : t("aiSettings.replyDefault") }}
                      </span>
                    </span>
                    <span v-if="REPLY_ORDER.includes(key)" class="block text-xs text-muted-foreground mt-0.5">{{ t(`aiSettings.replies.${key}.hint`) }}</span>
                    <span v-if="openReply !== key" class="block text-xs text-foreground/80 mt-1 line-clamp-1">
                      {{ draftText(key, replyLang).trim() || defaultText(key, replyLang) }}
                    </span>
                  </span>
                  <ChevronDown :size="16" class="mt-1 shrink-0 text-muted-foreground transition-transform" :class="{ 'rotate-180': openReply === key }" />
                </button>

                <div v-if="openReply === key" :id="`reply-panel-${key}`" class="px-3.5 pb-3.5 space-y-2">
                  <Label :for="`reply-${key}`" class="sr-only">{{ t(`aiSettings.replies.${key}.title`) }}</Label>
                  <Textarea
                    :id="`reply-${key}`"
                    :model-value="draftText(key, replyLang)"
                    :maxlength="replyDefaults.max_chars"
                    :rows="3"
                    :placeholder="defaultText(key, replyLang)"
                    class="bg-background text-sm"
                    @update:model-value="(v) => setDraftText(key, replyLang, String(v))"
                  />
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <span class="text-xs text-muted-foreground">
                      {{ draftText(key, replyLang).trim() ? `${draftText(key, replyLang).length}/${replyDefaults.max_chars}` : t("aiSettings.replyEmptyHint") }}
                    </span>
                    <Button
                      v-if="isCustomized(key, replyLang)"
                      variant="ghost"
                      class="h-9 gap-1.5 text-sm"
                      @click="setDraftText(key, replyLang, '')"
                    >
                      <RotateCcw :size="14" />
                      <span>{{ t("aiSettings.replyReset") }}</span>
                    </Button>
                  </div>
                </div>
              </li>
            </ul>

            <div class="flex flex-wrap items-center justify-end gap-3 pt-1">
              <span v-if="repliesDirty" class="text-sm text-muted-foreground" role="status">{{ t("aiSettings.unsavedChanges") }}</span>
              <Button class="h-10 gap-2 px-4" :disabled="!repliesDirty || savingReplies" @click="onSaveReplies">
                <Loader2 v-if="savingReplies" :size="16" class="animate-spin" />
                <Save v-else :size="16" />
                <span>{{ savingReplies ? t("aiSettings.saving") : t("aiSettings.repliesSave") }}</span>
              </Button>
            </div>
          </template>
        </div>
      </section>

      <!-- Section 6: What the AI learned from corrections -->
      <section class="rounded-xl border border-border bg-card overflow-hidden shadow-2xs">
        <button
          type="button"
          class="w-full min-h-14 p-4 flex items-center justify-between gap-2 text-left hover:bg-muted/40 transition-colors"
          :aria-expanded="openSections['memory']"
          @click="toggleSection('memory')"
        >
          <span class="flex items-center gap-2.5 text-base font-semibold text-foreground">
            <BrainCircuit :size="19" class="text-primary" />
            <span>{{ t("aiSettings.learnedRulesTitle") }}</span>
          </span>
          <ChevronDown :size="18" class="text-muted-foreground transition-transform" :class="{ 'rotate-180': openSections['memory'] }" />
        </button>
        <div v-if="openSections['memory']" class="p-4 sm:p-5 pt-2 border-t border-border/60 space-y-4">
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
              class="p-4 rounded-lg bg-muted/40 border border-border flex items-start justify-between gap-4"
            >
              <div class="flex-1 min-w-0 space-y-1">
                <div v-if="rule.customer_query" class="text-xs text-muted-foreground break-words">
                  <strong>{{ t("aiSettings.customerAsked") }}</strong> "{{ rule.customer_query }}"
                </div>
                <div class="text-sm text-foreground break-words">
                  <strong>{{ t("aiSettings.learnedRuleLabel") }}</strong> {{ rule.correction }}
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                class="h-10 w-10 text-destructive hover:bg-destructive/10 shrink-0 cursor-pointer"
                :title="t('aiSettings.deleteRuleTitle')"
                :aria-label="t('aiSettings.deleteRuleTitle')"
                @click="onDeleteRule(rule.id)"
              >
                <Trash2 :size="16" />
              </Button>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
