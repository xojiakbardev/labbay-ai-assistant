<script setup lang="ts">
import {
  Send,
  Trash2,
  Image as ImageIcon,
  Phone,
  Flame,
  Zap,
  Snowflake,
  Sparkles,
  Bot,
  User,
  RefreshCw,
  X,
  CheckCircle2,
  ArrowUpRight,
  Search,
  BrainCircuit,
  MessageSquare,
  Loader2,
  AlertCircle,
} from "@lucide/vue";
import { toast } from "vue-sonner";
import type { SandboxMessage, SandboxProduct, SandboxState } from "~/types/api";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t, locale } = useI18n();

const messages = ref<SandboxMessage[]>([]);
const inputMessage = ref("");
const imageUrl = ref("");
const showImageInput = ref(false);
const simulateTelegram = ref(true); // Always on — send Telegram alert on hot lead

const isLoading = ref(false);
const isInitialLoading = ref(true);
const isResetting = ref(false);
const isResetModalOpen = ref(false);

const leadStatus = ref<"cold" | "warm" | "hot" | null>(null);
const leadScore = ref<number | null>(null);
const phoneDetected = ref<string | null>(null);
const qualificationReason = ref<string | null>(null);
// The reason in the viewer's language, else Uzbek, else the model's own.
const qualificationReasons = ref<Record<string, string>>({});
const shownReason = computed(
  () => qualificationReasons.value[locale.value] || qualificationReasons.value.uz || qualificationReason.value
);
const knownFacts = ref<{ text: string; noted_at: string }[]>([]);
const interestedProducts = ref<SandboxProduct[]>([]);
const executedTools = ref<{ name: string; arguments: Record<string, any> }[]>([]);
const lastTelegramSent = ref(false);

const activeMobileTab = ref<"chat" | "inspector">("chat");

// Follows the newest message (images loading included) unless the tester
// scrolled up — same behaviour as the real chat.
const chat = useChatScroll();
const messagesContainer = chat.container;
const messagesContent = chat.content;

// A reaction is the AI answering the customer's previous message with an
// emoji instead of words: it's drawn on that message, not as its own bubble.
interface SandboxRow {
  msg: SandboxMessage;
  reactions: SandboxMessage[];
}

const rows = computed<SandboxRow[]>(() => {
  const out: SandboxRow[] = [];
  let lastCustomer: SandboxRow | null = null;
  for (const msg of messages.value) {
    if (msg.message_type === "reaction" && lastCustomer) {
      lastCustomer.reactions.push(msg);
      continue;
    }
    const row: SandboxRow = { msg, reactions: [] };
    out.push(row);
    if (msg.sender_type === "customer") lastCustomer = row;
  }
  return out;
});

const lastAiMessageId = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    const m = messages.value[i]!;
    if (m.sender_type !== "customer" && m.message_type !== "reaction") return m.id;
  }
  return null;
});

function formatPrice(amount: number | null, currency: string = "UZS") {
  if (amount == null) return "—";
  return `${new Intl.NumberFormat("uz-UZ").format(amount)} ${currency}`;
}

const KNOWN_TOOLS = [
  "search_products",
  "browse_catalog",
  "get_similar_products",
  "get_product",
  "check_product_availability",
  "get_active_discounts",
  "request_human",
];

function toolLabel(name: string): string {
  return KNOWN_TOOLS.includes(name) ? t(`sandbox.tools.${name}`) : name;
}

// What the AI searched for, in words — ids are left out.
function toolDetails(args: Record<string, any>): string {
  return Object.values(args ?? {})
    .filter((v) => v !== null && v !== undefined && v !== "" && !(typeof v === "string" && /^[0-9a-f-]{32,36}$/i.test(v)))
    .map((v) => (typeof v === "object" ? JSON.stringify(v) : String(v)))
    .join(" · ");
}

function reactionLabel(r: SandboxMessage): string {
  const base = t("conversations.aiReacted", { emoji: r.content });
  return r.delivery_status === "failed" ? `${base} — ${t("conversations.notDelivered")}` : base;
}

async function loadSandboxState() {
  try {
    isInitialLoading.value = true;
    const state: SandboxState = await api.getSandboxState();
    messages.value = state.messages || [];
    leadStatus.value = state.lead_status;
    leadScore.value = state.lead_score;
    phoneDetected.value = state.phone;
    qualificationReason.value = state.qualification_reason;
    qualificationReasons.value = state.qualification_reasons || {};
    knownFacts.value = state.known_facts || [];
    interestedProducts.value = state.interested_products || [];
    await nextTick();
    chat.scrollToBottom();
  } catch (err: any) {
    console.error("Failed to load sandbox state:", err);
    toast.error(err?.message || t("sandbox.loadError"));
  } finally {
    isInitialLoading.value = false;
  }
}

async function onSendMessage(quickText?: string) {
  const textToSend = (quickText || inputMessage.value).trim();
  const attachment = imageUrl.value.trim() || undefined;

  if (!textToSend && !attachment) return;
  if (isLoading.value) return;

  // Optimistic customer message
  const tempCustomerMsg: SandboxMessage = {
    id: `temp_${Date.now()}`,
    sender_type: "customer",
    content: textToSend || (attachment ? t("sandbox.imageSentPlaceholder") : ""),
    message_type: attachment ? "image" : "text",
    attachment_url: attachment,
    created_at: new Date().toISOString(),
  };

  messages.value.push(tempCustomerMsg);
  inputMessage.value = "";
  imageUrl.value = "";
  showImageInput.value = false;
  isLoading.value = true;
  await nextTick();
  chat.scrollToBottom();

  try {
    const res = await api.sendSandboxMessage({
      content: textToSend || t("sandbox.imageOnlyMessage"),
      attachment_url: attachment,
      simulate_telegram: simulateTelegram.value,
    });

    messages.value = res.messages || [];
    leadStatus.value = res.lead_status;
    leadScore.value = res.lead_score;
    phoneDetected.value = res.phone_detected ?? null;
    qualificationReason.value = res.qualification_reason;
    qualificationReasons.value = res.qualification_reasons || {};
    knownFacts.value = res.known_facts || [];
    interestedProducts.value = res.interested_products || [];
    executedTools.value = res.executed_tools || [];
    lastTelegramSent.value = res.telegram_sent;

    if (res.telegram_sent) {
      toast.success(t("sandbox.telegramSentToast"));
    }
    if (res.conversation_closed) {
      toast.info(
        res.reaction
          ? t("sandbox.closedWithReaction", { reaction: res.reaction })
          : t("sandbox.closedSilently")
      );
    }
  } catch (err: any) {
    console.error("Failed to send message in sandbox:", err);
    toast.error(err?.message || t("sandbox.sendError"));
  } finally {
    isLoading.value = false;
  }
}

async function onResetSandbox() {
  try {
    isResetting.value = true;
    await api.resetSandbox();
    messages.value = [];
    leadStatus.value = null;
    leadScore.value = null;
    phoneDetected.value = null;
    qualificationReason.value = null;
    qualificationReasons.value = {};
    knownFacts.value = [];
    interestedProducts.value = [];
    executedTools.value = [];
    lastTelegramSent.value = false;
    isResetModalOpen.value = false;
    toast.success(t("sandbox.resetSuccess"));
  } catch (err: any) {
    console.error("Failed to reset sandbox:", err);
    toast.error(err?.message || t("sandbox.resetError"));
  } finally {
    isResetting.value = false;
  }
}

onMounted(() => {
  loadSandboxState();
});
</script>

<template>
  <div class="space-y-3 max-w-7xl mx-auto">

    <!-- Mobile Tab Switcher -->
    <div class="flex lg:hidden w-full border-b border-border" role="tablist">
      <button
        type="button"
        role="tab"
        :aria-selected="activeMobileTab === 'chat'"
        class="flex-1 min-h-10 py-2 text-sm font-semibold text-center border-b-2 transition-colors flex items-center justify-center gap-1.5"
        :class="activeMobileTab === 'chat' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground'"
        @click="activeMobileTab = 'chat'"
      >
        <MessageSquare class="w-4 h-4" aria-hidden="true" />
        {{ t("sandbox.chatTitle") }}
      </button>
      <button
        type="button"
        role="tab"
        :aria-selected="activeMobileTab === 'inspector'"
        class="flex-1 min-h-10 py-2 text-sm font-semibold text-center border-b-2 transition-colors flex items-center justify-center gap-1.5"
        :class="activeMobileTab === 'inspector' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground'"
        @click="activeMobileTab = 'inspector'"
      >
        <BrainCircuit class="w-4 h-4" aria-hidden="true" />
        {{ t("sandbox.inspectorTitle") }}
        <span
          v-if="leadStatus"
          class="w-2 h-2 rounded-full"
          :class="leadStatus === 'hot' ? 'bg-rose-500' : leadStatus === 'warm' ? 'bg-amber-500' : 'bg-slate-400'"
          aria-hidden="true"
        ></span>
      </button>
    </div>

    <!-- Grid Layout -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-3 items-start">
      <!-- Chat column: exactly the space left on screen, so the composer never falls below the fold -->
      <div class="sandbox-chat lg:col-span-7 flex flex-col" :class="{ 'hidden lg:flex': activeMobileTab !== 'chat' }">
        <Card class="flex flex-col flex-1 min-h-0 gap-0 py-0 shadow-sm border border-border/80 overflow-hidden bg-card">
          <!-- Chat Card Header -->
          <div class="px-3 sm:px-4 py-2 border-b border-border/70 flex items-center justify-between gap-2 bg-muted/20 shrink-0">
            <div class="flex items-center gap-2.5 min-w-0">
              <div class="w-9 h-9 rounded-full bg-primary flex items-center justify-center text-primary-foreground shrink-0">
                <Bot class="w-5 h-5" aria-hidden="true" />
              </div>
              <div class="min-w-0">
                <div class="font-semibold text-sm text-foreground truncate">{{ t("conversations.aiAssistant") }}</div>
                <p class="text-xs text-muted-foreground truncate">{{ t("sandbox.simulationLabel") }}</p>
              </div>
            </div>

            <div class="flex items-center gap-0.5 shrink-0">
              <button
                type="button"
                class="icon-btn"
                :title="t('common.refresh')"
                :aria-label="t('common.refresh')"
                :disabled="isInitialLoading"
                @click="loadSandboxState"
              >
                <RefreshCw class="w-4 h-4" :class="{ 'animate-spin': isInitialLoading }" />
              </button>
              <button
                type="button"
                class="icon-btn hover:text-destructive"
                :title="t('sandbox.reset')"
                :aria-label="t('sandbox.reset')"
                :disabled="isResetting || messages.length === 0"
                @click="isResetModalOpen = true"
              >
                <Trash2 class="w-4 h-4" />
              </button>
            </div>
          </div>

          <!-- Messages -->
          <div
            ref="messagesContainer"
            class="flex-1 min-h-0 overflow-y-auto overscroll-contain bg-background/50 custom-scrollbar"
            role="log"
            :aria-label="t('conversations.messagesLabel')"
            @scroll.passive="chat.onScroll"
            @wheel.passive="chat.onUserIntent"
            @touchstart.passive="chat.onUserIntent"
          >
            <div ref="messagesContent" class="p-3 sm:p-4 space-y-3">
              <!-- Empty State -->
              <div v-if="!isInitialLoading && messages.length === 0" class="flex flex-col items-center justify-center text-center py-6 space-y-4">
                <div class="w-12 h-12 rounded-2xl bg-primary/10 text-primary flex items-center justify-center">
                  <Sparkles class="w-6 h-6" aria-hidden="true" />
                </div>
                <div class="space-y-1 max-w-sm">
                  <h3 class="font-semibold text-sm text-foreground">{{ t("sandbox.welcome") }}</h3>
                  <p class="text-sm text-muted-foreground">{{ t("sandbox.emptyHistory") }}</p>
                </div>

                <div class="w-full max-w-md space-y-2 pt-1 text-left">
                  <p class="text-xs font-semibold text-muted-foreground">{{ t("sandbox.quickPrompts") }}</p>
                  <div class="grid grid-cols-1 gap-1.5">
                    <button
                      v-for="key in ['prompt1', 'prompt2', 'prompt3']"
                      :key="key"
                      type="button"
                      class="w-full min-h-10 text-left text-sm px-3 py-2 rounded-lg border border-border/80 bg-card hover:bg-muted/50 hover:border-primary/40 transition-colors text-foreground flex items-center justify-between gap-2"
                      @click="onSendMessage(t(`sandbox.${key}`))"
                    >
                      <span>{{ t(`sandbox.${key}`) }}</span>
                      <ArrowUpRight class="w-4 h-4 text-muted-foreground shrink-0" aria-hidden="true" />
                    </button>
                  </div>
                </div>
              </div>

              <div v-for="row in rows" :key="row.msg.id">
                <!-- Customer (the tester) — right -->
                <div v-if="row.msg.sender_type === 'customer'" class="flex justify-end gap-2 items-end" :class="{ 'mb-3': row.reactions.length }">
                  <div class="max-w-[82%] sm:max-w-[70%] flex flex-col items-end gap-1">
                    <div v-if="row.msg.attachment_url" class="rounded-xl overflow-hidden border border-primary/20 w-[200px] max-w-full aspect-[4/5] bg-muted">
                      <img :src="row.msg.attachment_url" :alt="t('sandbox.customerImageAlt')" class="w-full h-full object-contain" />
                    </div>
                    <div class="relative rounded-2xl rounded-br-md px-3.5 py-2 bg-primary text-primary-foreground text-sm leading-relaxed whitespace-pre-wrap break-words">
                      {{ row.msg.content }}
                      <!-- The AI reacted to this message instead of replying -->
                      <span v-if="row.reactions.length" class="reaction-chips">
                        <span
                          v-for="r in row.reactions"
                          :key="r.id"
                          class="reaction-chip text-foreground"
                          :class="{ 'reaction-chip-muted': r.delivery_status === 'failed' }"
                          role="img"
                          :aria-label="reactionLabel(r)"
                          :title="reactionLabel(r)"
                        >
                          <span aria-hidden="true">{{ r.content }}</span>
                          <AlertCircle v-if="r.delivery_status === 'failed'" :size="11" class="text-destructive" aria-hidden="true" />
                        </span>
                      </span>
                    </div>
                  </div>
                  <div class="w-7 h-7 rounded-full bg-muted border border-border flex items-center justify-center text-muted-foreground shrink-0" :title="t('sandbox.customerLabel')">
                    <User class="w-3.5 h-3.5" aria-hidden="true" />
                    <span class="sr-only">{{ t("sandbox.customerLabel") }}</span>
                  </div>
                </div>

                <!-- A reaction with no customer message before it -->
                <div v-else-if="row.msg.message_type === 'reaction'" class="flex justify-center">
                  <span class="inline-flex items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground">
                    <span aria-hidden="true">{{ row.msg.content }}</span>
                    <span>{{ reactionLabel(row.msg) }}</span>
                  </span>
                </div>

                <!-- AI — left -->
                <div v-else class="flex justify-start gap-2 items-end">
                  <div class="w-7 h-7 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center text-primary shrink-0" :title="t('conversations.aiAssistant')">
                    <Bot class="w-3.5 h-3.5" aria-hidden="true" />
                    <span class="sr-only">{{ t("conversations.aiAssistant") }}</span>
                  </div>
                  <div class="max-w-[85%] sm:max-w-[78%] flex flex-col items-start gap-1.5">
                    <div v-if="row.msg.attachment_url" class="rounded-xl overflow-hidden border border-border w-[200px] max-w-full aspect-[4/5] bg-muted">
                      <img :src="row.msg.attachment_url" :alt="t('conversations.imageAlt')" class="w-full h-full object-contain" />
                    </div>
                    <div v-if="row.msg.content" class="rounded-2xl rounded-bl-md px-3.5 py-2 bg-card border border-border/80 text-foreground text-sm leading-relaxed whitespace-pre-wrap break-words">
                      {{ row.msg.content }}
                    </div>

                    <!-- Products the AI matched, under its latest reply -->
                    <div v-if="interestedProducts.length > 0 && row.msg.id === lastAiMessageId" class="w-full grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                      <div
                        v-for="prod in interestedProducts"
                        :key="prod.id"
                        class="flex items-center gap-2 p-2 rounded-lg border border-border/80 bg-card"
                      >
                        <div class="w-10 h-10 rounded-md bg-muted overflow-hidden shrink-0 flex items-center justify-center text-muted-foreground">
                          <img v-if="prod.image_url" :src="prod.image_url" :alt="prod.name" class="w-full h-full object-cover" />
                          <ImageIcon v-else class="w-4 h-4" aria-hidden="true" />
                        </div>
                        <div class="min-w-0 flex-1">
                          <h4 class="text-xs font-semibold text-foreground truncate">{{ prod.name }}</h4>
                          <p class="text-xs font-bold text-primary">{{ formatPrice(prod.price, prod.currency) }}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- AI is writing -->
              <div v-if="isLoading" class="flex justify-start gap-2 items-center" role="status">
                <div class="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center text-primary shrink-0">
                  <Bot class="w-3.5 h-3.5" aria-hidden="true" />
                </div>
                <div class="rounded-2xl px-3.5 py-2 bg-card border border-border/80 flex items-center gap-1.5">
                  <span class="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" aria-hidden="true"></span>
                  <span class="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:0.2s]" aria-hidden="true"></span>
                  <span class="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:0.4s]" aria-hidden="true"></span>
                  <span class="text-xs text-muted-foreground ml-1">{{ t("sandbox.sending") }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Image URL input -->
          <div v-if="showImageInput" class="p-2.5 border-t border-border/80 bg-muted/30 flex items-center gap-2 shrink-0">
            <ImageIcon class="w-4 h-4 text-primary shrink-0" aria-hidden="true" />
            <Input
              v-model="imageUrl"
              type="url"
              :placeholder="t('sandbox.imageUrlPlaceholder')"
              :aria-label="t('sandbox.attachImage')"
              class="h-10 text-sm flex-1 min-w-0"
              @keydown.enter="showImageInput = false"
            />
            <Button variant="secondary" class="h-10 shrink-0" @click="showImageInput = false">
              {{ t("sandbox.applyImage") }}
            </Button>
            <button type="button" class="icon-btn" :aria-label="t('sandbox.removeImage')" @click="imageUrl = ''; showImageInput = false">
              <X class="w-4 h-4" />
            </button>
          </div>

          <!-- Composer -->
          <div class="p-2.5 sm:p-3 border-t border-border/80 bg-card space-y-2 shrink-0">
            <div v-if="imageUrl" class="flex items-center gap-2 pl-2.5 rounded-md bg-muted/60 border border-border text-xs w-fit max-w-full">
              <ImageIcon class="w-3.5 h-3.5 text-primary shrink-0" aria-hidden="true" />
              <span class="truncate max-w-[200px] text-muted-foreground">{{ imageUrl }}</span>
              <button type="button" class="icon-btn h-8 w-8" :aria-label="t('sandbox.removeImage')" @click="imageUrl = ''">
                <X class="w-3.5 h-3.5" />
              </button>
            </div>

            <form class="flex items-center gap-2" @submit.prevent="onSendMessage()">
              <button
                type="button"
                class="icon-btn border border-border"
                :class="{ 'text-primary border-primary/40': imageUrl }"
                :title="t('sandbox.attachImage')"
                :aria-label="t('sandbox.attachImage')"
                :aria-expanded="showImageInput"
                @click="showImageInput = !showImageInput"
              >
                <ImageIcon class="w-4 h-4" />
              </button>

              <Input
                v-model="inputMessage"
                :placeholder="t('sandbox.inputPlaceholder')"
                :aria-label="t('sandbox.inputPlaceholder')"
                enterkeyhint="send"
                class="h-10 text-base sm:text-sm flex-1 min-w-0"
                :disabled="isLoading"
              />

              <Button
                type="submit"
                class="h-10 min-w-10 px-3 gap-1.5 shrink-0"
                :disabled="isLoading || (!inputMessage.trim() && !imageUrl.trim())"
                :aria-label="t('sandbox.send')"
              >
                <Send class="w-4 h-4" />
                <span class="hidden sm:inline">{{ t("sandbox.send") }}</span>
              </Button>
            </form>
          </div>
        </Card>
      </div>

      <!-- Inspector -->
      <div class="lg:col-span-5 space-y-4" :class="{ 'hidden lg:block': activeMobileTab !== 'inspector' }">
        <!-- Lead status & score -->
        <Card class="border border-border/80 shadow-sm overflow-hidden bg-card">
          <CardHeader class="pb-3 border-b border-border/60 bg-muted/20 flex flex-row items-center justify-between gap-2">
            <div class="min-w-0">
              <CardTitle class="text-sm font-bold flex items-center gap-1.5 text-foreground">
                <Sparkles class="w-4 h-4 text-primary" aria-hidden="true" />
                {{ t("sandbox.leadStatus") }}
              </CardTitle>
              <CardDescription class="text-xs">{{ t("sandbox.leadStatusDesc") }}</CardDescription>
            </div>

            <div class="shrink-0">
              <Badge
                v-if="leadStatus === 'hot'"
                class="bg-rose-500/15 text-rose-700 dark:text-rose-300 border-rose-500/30 gap-1 text-xs font-semibold px-2.5 py-0.5"
              >
                <Flame class="w-3.5 h-3.5" aria-hidden="true" />
                {{ t("sandbox.badgeHot") }}
              </Badge>
              <Badge
                v-else-if="leadStatus === 'warm'"
                class="bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30 gap-1 text-xs font-semibold px-2.5 py-0.5"
              >
                <Zap class="w-3.5 h-3.5" aria-hidden="true" />
                {{ t("sandbox.badgeWarm") }}
              </Badge>
              <Badge
                v-else-if="leadStatus === 'cold'"
                class="bg-slate-500/15 text-slate-700 dark:text-slate-300 border-slate-500/30 gap-1 text-xs font-semibold px-2.5 py-0.5"
              >
                <Snowflake class="w-3.5 h-3.5" aria-hidden="true" />
                {{ t("sandbox.badgeCold") }}
              </Badge>
              <span v-else class="text-xs text-muted-foreground">—</span>
            </div>
          </CardHeader>

          <CardContent class="p-4 space-y-4">
            <div class="space-y-1.5">
              <div class="flex items-center justify-between text-xs">
                <span class="font-medium text-muted-foreground">{{ t("sandbox.leadScore") }}</span>
                <span class="font-bold text-foreground font-mono">{{ leadScore != null ? `${leadScore}/100` : '—' }}</span>
              </div>
              <div class="w-full h-2 rounded-full bg-muted overflow-hidden">
                <div
                  class="h-full transition-all duration-500 rounded-full"
                  :class="(leadScore || 0) >= 70 ? 'bg-rose-500' : (leadScore || 0) >= 35 ? 'bg-amber-500' : 'bg-slate-400'"
                  :style="{ width: `${leadScore || 0}%` }"
                ></div>
              </div>
            </div>

            <div class="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-border/70 bg-muted/30 text-xs">
              <div class="flex items-center gap-2 text-muted-foreground">
                <Phone class="w-3.5 h-3.5 text-primary" aria-hidden="true" />
                <span>{{ t("sandbox.detectedPhone") }}</span>
              </div>
              <span class="font-mono font-bold" :class="phoneDetected ? 'text-emerald-700 dark:text-emerald-400' : 'text-foreground'">
                {{ phoneDetected || t("sandbox.notDetected") }}
              </span>
            </div>

            <div class="space-y-1">
              <span class="text-xs font-semibold text-muted-foreground">{{ t("sandbox.reasoning") }}</span>
              <p class="text-sm text-foreground bg-muted/20 p-2.5 rounded-lg border border-border/50 leading-relaxed">
                {{ shownReason || t("sandbox.analyzing") }}
              </p>
            </div>
          </CardContent>
        </Card>

        <!-- Customer memory -->
        <Card class="border border-border/80 shadow-sm bg-card">
          <CardHeader class="pb-2">
            <CardTitle class="text-sm font-semibold text-foreground flex items-center gap-1.5">
              <BrainCircuit class="w-4 h-4 text-primary" aria-hidden="true" />
              {{ t("sandbox.customerMemory") }}
            </CardTitle>
          </CardHeader>
          <CardContent class="p-4 pt-0">
            <div v-if="knownFacts && knownFacts.length > 0" class="flex flex-wrap gap-1.5">
              <span
                v-for="(fact, idx) in knownFacts"
                :key="idx"
                class="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-primary/10 border border-primary/20 text-primary text-xs font-medium"
              >
                <CheckCircle2 class="w-3 h-3 shrink-0" aria-hidden="true" />
                {{ fact.text }}
              </span>
            </div>
            <p v-else class="text-sm text-muted-foreground py-1">{{ t("sandbox.noFacts") }}</p>
          </CardContent>
        </Card>

        <!-- What the AI looked up -->
        <Card class="border border-border/80 shadow-sm bg-card">
          <CardHeader class="pb-2">
            <CardTitle class="text-sm font-semibold text-foreground flex items-center gap-1.5">
              <Search class="w-4 h-4 text-primary" aria-hidden="true" />
              {{ t("sandbox.toolsExecuted") }}
            </CardTitle>
          </CardHeader>
          <CardContent class="p-4 pt-0">
            <ul v-if="executedTools && executedTools.length > 0" class="space-y-1.5">
              <li
                v-for="(tool, i) in executedTools"
                :key="i"
                class="p-2.5 rounded-lg bg-muted/40 border border-border/60 text-sm"
              >
                <div class="font-medium text-foreground">{{ toolLabel(tool.name) }}</div>
                <div v-if="toolDetails(tool.arguments)" class="text-xs text-muted-foreground break-words">
                  {{ toolDetails(tool.arguments) }}
                </div>
              </li>
            </ul>
            <p v-else class="text-sm text-muted-foreground py-1">{{ t("sandbox.noTools") }}</p>
          </CardContent>
        </Card>
      </div>
    </div>

    <!-- Reset confirmation -->
    <Dialog :open="isResetModalOpen" @update:open="isResetModalOpen = $event">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{{ t("sandbox.reset") }}</DialogTitle>
          <DialogDescription>{{ t("sandbox.resetConfirm") }}</DialogDescription>
        </DialogHeader>

        <DialogFooter class="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" class="h-10" :disabled="isResetting" @click="isResetModalOpen = false">
            {{ t("common.cancel") }}
          </Button>
          <Button variant="destructive" class="h-10 gap-1.5" :disabled="isResetting" @click="onResetSandbox">
            <Loader2 v-if="isResetting" class="w-4 h-4 animate-spin" />
            <Trash2 v-else class="w-4 h-4" />
            <span>{{ t("sandbox.resetConfirmBtn") }}</span>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>

<style scoped>
/* The chat card takes exactly the height left on screen: viewport minus the
   app chrome, the page padding and (below lg) the chat/inspector tabs. */
.sandbox-chat {
  height: calc(100dvh - var(--mobile-header-h) - var(--mobile-nav-h) - 5.25rem);
  min-height: 22rem;
}

@media (min-width: 901px) {
  .sandbox-chat {
    height: calc(100dvh - 11rem);
  }
}

@media (min-width: 1024px) {
  .sandbox-chat {
    height: calc(100dvh - 7.5rem);
  }
}
</style>
