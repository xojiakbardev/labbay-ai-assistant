<script setup lang="ts">
import {
  FlaskConical,
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
  ExternalLink,
  HelpCircle,
  RefreshCw,
  X,
  CheckCircle2,
  ArrowUpRight,
  Code2,
  BrainCircuit,
  MessageSquare
} from "@lucide/vue";
import { toast } from "vue-sonner";
import type { SandboxMessage, SandboxProduct, SandboxState } from "~/types/api";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t } = useI18n();

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
const knownFacts = ref<{ text: string; noted_at: string }[]>([]);
const interestedProducts = ref<SandboxProduct[]>([]);
const executedTools = ref<{ name: string; arguments: Record<string, any> }[]>([]);
const lastTelegramSent = ref(false);

const messagesContainer = ref<HTMLDivElement | null>(null);
const activeMobileTab = ref<"chat" | "inspector">("chat");

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
    }
  });
}

function formatPrice(amount: number | null, currency: string = "UZS") {
  if (amount == null) return "—";
  return `${new Intl.NumberFormat("uz-UZ").format(amount)} ${currency}`;
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
    knownFacts.value = state.known_facts || [];
    interestedProducts.value = state.interested_products || [];
    scrollToBottom();
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
    attachment_url: attachment,
    created_at: new Date().toISOString(),
  };

  messages.value.push(tempCustomerMsg);
  inputMessage.value = "";
  imageUrl.value = "";
  showImageInput.value = false;
  isLoading.value = true;
  scrollToBottom();

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
    scrollToBottom();
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
  <div class="space-y-3 max-w-7xl mx-auto pb-10">

    <!-- Mobile Tab Switcher -->
    <div class="flex lg:hidden w-full border-b border-border">
      <button
        type="button"
        class="flex-1 py-2 text-xs font-semibold text-center border-b-2 transition-colors flex items-center justify-center gap-1.5"
        :class="activeMobileTab === 'chat' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground'"
        @click="activeMobileTab = 'chat'"
      >
        <MessageSquare class="w-3.5 h-3.5" />
        {{ t("sandbox.chatTitle") }} ({{ messages.length }})
      </button>
      <button
        type="button"
        class="flex-1 py-2 text-xs font-semibold text-center border-b-2 transition-colors flex items-center justify-center gap-1.5"
        :class="activeMobileTab === 'inspector' ? 'border-primary text-primary' : 'border-transparent text-muted-foreground'"
        @click="activeMobileTab = 'inspector'"
      >
        <BrainCircuit class="w-3.5 h-3.5" />
        {{ t("sandbox.inspectorTitle") }}
        <span
          v-if="leadStatus"
          class="w-2 h-2 rounded-full"
          :class="leadStatus === 'hot' ? 'bg-rose-500' : leadStatus === 'warm' ? 'bg-amber-500' : 'bg-slate-400'"
        ></span>
      </button>
    </div>

    <!-- Grid Layout -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-3 items-start">
      <!-- Chat Column (7 cols on large) -->
      <div class="lg:col-span-7 flex flex-col h-[calc(100vh-14rem)] min-h-[520px] max-h-[750px]" :class="{ 'hidden lg:flex': activeMobileTab !== 'chat' }">
        <Card class="flex flex-col flex-1 h-full shadow-sm border border-border/80 overflow-hidden bg-card">
          <!-- Chat Card Header -->
          <div class="px-4 py-3 border-b border-border/70 flex items-center justify-between bg-muted/20">
            <div class="flex items-center gap-2.5">
              <div class="relative">
                <div class="w-9 h-9 rounded-full bg-gradient-to-tr from-primary to-primary/80 flex items-center justify-center text-primary-foreground shadow-sm">
                  <Bot class="w-5 h-5" />
                </div>
                <span class="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-emerald-500 ring-2 ring-background"></span>
              </div>
              <div>
                <div class="flex items-center gap-1.5">
                  <span class="font-semibold text-sm text-foreground">Mivo AI Assistant</span>
                  <Badge variant="secondary" class="text-[10px] py-0 px-1 font-mono">Gemini 2.5</Badge>
                </div>
                <p class="text-[11px] text-muted-foreground flex items-center gap-1">
                  <span>{{ t("sandbox.simulationLabel") }}</span>
                  <span v-if="simulateTelegram" class="text-sky-500 font-medium">• {{ t("sandbox.telegramTestActive") }}</span>
                </p>
              </div>
            </div>

            <div class="flex items-center gap-1">
              <!-- Reset (Trash) icon -->
              <Button
                variant="ghost"
                size="icon"
                class="h-8 w-8 text-muted-foreground hover:text-destructive"
                :title="t('sandbox.reset')"
                :disabled="isResetting"
                @click="isResetModalOpen = true"
              >
                <Trash2 class="w-3.5 h-3.5" :class="{ 'animate-spin': isResetting }" />
              </Button>
              <!-- Refresh icon -->
              <Button
                variant="ghost"
                size="icon"
                class="h-8 w-8 text-muted-foreground"
                :title="t('common.refresh')"
                @click="loadSandboxState"
              >
                <RefreshCw class="w-3.5 h-3.5" :class="{ 'animate-spin': isInitialLoading }" />
              </Button>
            </div>
          </div>

          <!-- Chat Scrollable Body -->
          <div ref="messagesContainer" class="flex-1 overflow-y-auto p-4 space-y-4 bg-background/50">
            <!-- Empty State -->
            <div v-if="!isInitialLoading && messages.length === 0" class="h-full flex flex-col items-center justify-center text-center p-6 space-y-4">
              <div class="w-14 h-14 rounded-2xl bg-primary/10 text-primary flex items-center justify-center">
                <Sparkles class="w-7 h-7 animate-pulse" />
              </div>
              <div class="space-y-1 max-w-sm">
                <h3 class="font-bold text-sm text-foreground">{{ t("sandbox.welcome") }}</h3>
                <p class="text-xs text-muted-foreground">
                  {{ t("sandbox.emptyHistory") }}
                </p>
              </div>

              <!-- Quick prompts -->
              <div class="w-full max-w-md space-y-2 pt-2 text-left">
                <p class="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {{ t("sandbox.quickPrompts") }}
                </p>
                <div class="grid grid-cols-1 gap-1.5">
                  <button
                    type="button"
                    class="w-full text-left text-xs p-2.5 rounded-lg border border-border/80 bg-card hover:bg-muted/50 hover:border-primary/40 transition-all text-foreground flex items-center justify-between group"
                    @click="onSendMessage(t('sandbox.prompt1'))"
                  >
                    <span>{{ t("sandbox.prompt1") }}</span>
                    <ArrowUpRight class="w-3.5 h-3.5 opacity-40 group-hover:opacity-100 group-hover:text-primary shrink-0 ml-1" />
                  </button>
                  <button
                    type="button"
                    class="w-full text-left text-xs p-2.5 rounded-lg border border-border/80 bg-card hover:bg-muted/50 hover:border-primary/40 transition-all text-foreground flex items-center justify-between group"
                    @click="onSendMessage(t('sandbox.prompt2'))"
                  >
                    <span>{{ t("sandbox.prompt2") }}</span>
                    <ArrowUpRight class="w-3.5 h-3.5 opacity-40 group-hover:opacity-100 group-hover:text-primary shrink-0 ml-1" />
                  </button>
                  <button
                    type="button"
                    class="w-full text-left text-xs p-2.5 rounded-lg border border-border/80 bg-card hover:bg-muted/50 hover:border-primary/40 transition-all text-foreground flex items-center justify-between group"
                    @click="onSendMessage(t('sandbox.prompt3'))"
                  >
                    <span>{{ t("sandbox.prompt3") }}</span>
                    <ArrowUpRight class="w-3.5 h-3.5 opacity-40 group-hover:opacity-100 group-hover:text-primary shrink-0 ml-1" />
                  </button>
                </div>
              </div>
            </div>

            <!-- Messages List -->
            <div v-for="msg in messages" :key="msg.id" class="flex flex-col space-y-2">
              <!-- Customer Message (Right) -->
              <div v-if="msg.sender_type === 'customer'" class="flex justify-end gap-2 items-end">
                <div class="max-w-[82%] sm:max-w-[70%] flex flex-col items-end space-y-1">
                  <!-- Image if attached -->
                  <div v-if="msg.attachment_url" class="rounded-xl overflow-hidden border border-primary/20 max-w-[240px] shadow-sm">
                    <img :src="msg.attachment_url" :alt="t('sandbox.customerImageAlt')" class="w-full max-h-48 object-cover" />
                  </div>
                  <!-- Bubble -->
                  <div class="rounded-2xl rounded-br-xs px-3.5 py-2.5 bg-primary text-primary-foreground text-xs sm:text-[13px] leading-relaxed shadow-sm">
                    {{ msg.content }}
                  </div>
                  <span class="text-[10px] text-muted-foreground pr-1">{{ t("sandbox.customerLabel") }}</span>
                </div>
                <div class="w-7 h-7 rounded-full bg-muted border border-border flex items-center justify-center text-muted-foreground shrink-0 text-xs">
                  <User class="w-3.5 h-3.5" />
                </div>
              </div>

              <!-- AI Message (Left) -->
              <div v-else class="flex justify-start gap-2 items-end">
                <div class="w-7 h-7 rounded-full bg-primary/10 border border-primary/20 flex items-center justify-center text-primary shrink-0 text-xs shadow-xs">
                  <Bot class="w-3.5 h-3.5" />
                </div>
                <div class="max-w-[85%] sm:max-w-[78%] flex flex-col items-start space-y-1.5">
                  <!-- Bubble -->
                  <div class="rounded-2xl rounded-bl-xs px-3.5 py-2.5 bg-card border border-border/80 text-foreground text-xs sm:text-[13px] leading-relaxed shadow-xs whitespace-pre-wrap">
                    {{ msg.content }}
                  </div>

                  <!-- Product Cards if recommendations exist -->
                  <div v-if="interestedProducts.length > 0 && msg === messages[messages.length - 1]" class="w-full grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                    <div
                      v-for="prod in interestedProducts"
                      :key="prod.id"
                      class="flex items-center gap-2 p-2 rounded-lg border border-border/80 bg-card hover:bg-muted/30 transition-colors shadow-xs"
                    >
                      <div class="w-10 h-10 rounded-md bg-muted overflow-hidden shrink-0 flex items-center justify-center text-muted-foreground text-xs">
                        <img v-if="prod.image_url" :src="prod.image_url" :alt="prod.name" class="w-full h-full object-cover" />
                        <ImageIcon v-else class="w-4 h-4" />
                      </div>
                      <div class="min-w-0 flex-1">
                        <h4 class="text-xs font-semibold text-foreground truncate">{{ prod.name }}</h4>
                        <p class="text-[11px] font-bold text-primary">{{ formatPrice(prod.price, prod.currency) }}</p>
                      </div>
                    </div>
                  </div>

                  <span class="text-[10px] text-muted-foreground pl-1">Mivo AI</span>
                </div>
              </div>
            </div>

            <!-- Typing indicator -->
            <div v-if="isLoading" class="flex justify-start gap-2 items-center">
              <div class="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center text-primary shrink-0">
                <Bot class="w-3.5 h-3.5 animate-pulse" />
              </div>
              <div class="rounded-2xl px-3.5 py-2 bg-card border border-border/80 shadow-xs flex items-center gap-1.5">
                <span class="w-1.5 h-1.5 rounded-full bg-primary animate-bounce"></span>
                <span class="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:0.2s]"></span>
                <span class="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:0.4s]"></span>
                <span class="text-[11px] text-muted-foreground ml-1">{{ t("sandbox.sending") }}</span>
              </div>
            </div>
          </div>

          <!-- Image Attachment Popup / Input Bar -->
          <div v-if="showImageInput" class="p-2.5 border-t border-border/80 bg-muted/30 flex items-center gap-2">
            <ImageIcon class="w-4 h-4 text-primary shrink-0" />
            <Input
              v-model="imageUrl"
              :placeholder="t('sandbox.imageUrlPlaceholder')"
              class="h-8 text-xs flex-1"
              @keydown.enter="showImageInput = false"
            />
            <Button size="sm" variant="secondary" class="h-8 text-xs" @click="showImageInput = false">
              {{ t("sandbox.applyImage") }}
            </Button>
            <Button size="icon" variant="ghost" class="h-8 w-8 text-muted-foreground" @click="imageUrl = ''; showImageInput = false">
              <X class="w-3.5 h-3.5" />
            </Button>
          </div>

          <!-- Footer Input Bar -->
          <div class="p-3 border-t border-border/80 bg-card space-y-2">
            <!-- Attached Image Chip Preview -->
            <div v-if="imageUrl" class="flex items-center gap-2 px-2.5 py-1 rounded-md bg-muted/60 border border-border text-xs w-fit">
              <ImageIcon class="w-3.5 h-3.5 text-primary" />
              <span class="truncate max-w-[200px] text-muted-foreground">{{ imageUrl }}</span>
              <button type="button" class="text-muted-foreground hover:text-foreground" @click="imageUrl = ''">
                <X class="w-3 h-3" />
              </button>
            </div>

            <div class="flex items-center gap-2">
              <Button
                variant="outline"
                size="icon"
                class="h-9 w-9 shrink-0 text-muted-foreground hover:text-foreground hover:border-primary/40"
                :title="t('sandbox.attachImage')"
                @click="showImageInput = !showImageInput"
              >
                <ImageIcon class="w-4 h-4" :class="{ 'text-primary': imageUrl }" />
              </Button>

              <Input
                v-model="inputMessage"
                :placeholder="t('sandbox.inputPlaceholder')"
                class="h-9 text-xs sm:text-sm flex-1"
                :disabled="isLoading"
                @keydown.enter.prevent="onSendMessage()"
              />

              <Button
                size="sm"
                class="h-9 px-3.5 gap-1.5 bg-primary text-primary-foreground hover:bg-primary/90 shadow-sm shrink-0"
                :disabled="isLoading || (!inputMessage.trim() && !imageUrl.trim())"
                @click="onSendMessage()"
              >
                <Send class="w-3.5 h-3.5" />
                <span class="hidden sm:inline">{{ t("sandbox.send") }}</span>
              </Button>
            </div>
          </div>
        </Card>
      </div>

      <!-- Right Inspection Column (5 cols on large) -->
      <div class="lg:col-span-5 space-y-4" :class="{ 'hidden lg:block': activeMobileTab !== 'inspector' }">
        <!-- Lead Status & Score Card -->
        <Card class="border border-border/80 shadow-sm overflow-hidden bg-card">
          <CardHeader class="pb-3 border-b border-border/60 bg-muted/20 flex flex-row items-center justify-between">
            <div>
              <CardTitle class="text-sm font-bold flex items-center gap-1.5 text-foreground">
                <Sparkles class="w-4 h-4 text-primary" />
                {{ t("sandbox.leadStatus") }}
              </CardTitle>
              <CardDescription class="text-[11px]">{{ t("sandbox.leadStatusDesc") }}</CardDescription>
            </div>

            <!-- Status Pill -->
            <div>
              <Badge
                v-if="leadStatus === 'hot'"
                class="bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/30 gap-1 text-xs font-bold px-2.5 py-0.5"
              >
                <Flame class="w-3.5 h-3.5 fill-rose-500 text-rose-500" />
                {{ t("sandbox.badgeHot") }}
              </Badge>
              <Badge
                v-else-if="leadStatus === 'warm'"
                class="bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30 gap-1 text-xs font-bold px-2.5 py-0.5"
              >
                <Zap class="w-3.5 h-3.5 fill-amber-500 text-amber-500" />
                {{ t("sandbox.badgeWarm") }}
              </Badge>
              <Badge
                v-else-if="leadStatus === 'cold'"
                class="bg-slate-500/15 text-slate-600 dark:text-slate-400 border-slate-500/30 gap-1 text-xs font-bold px-2.5 py-0.5"
              >
                <Snowflake class="w-3.5 h-3.5 text-slate-500" />
                {{ t("sandbox.badgeCold") }}
              </Badge>
              <span v-else class="text-xs text-muted-foreground italic">—</span>
            </div>
          </CardHeader>

          <CardContent class="p-4 space-y-4">
            <!-- Score Progress Bar -->
            <div class="space-y-1.5">
              <div class="flex items-center justify-between text-xs">
                <span class="font-medium text-muted-foreground">{{ t("sandbox.leadScore") }}:</span>
                <span class="font-bold text-foreground font-mono">{{ leadScore != null ? `${leadScore}/100` : '—' }}</span>
              </div>
              <div class="w-full h-2 rounded-full bg-muted overflow-hidden">
                <div
                  class="h-full transition-all duration-500 rounded-full"
                  :class="
                    (leadScore || 0) >= 70
                      ? 'bg-gradient-to-r from-amber-500 to-rose-500'
                      : (leadScore || 0) >= 35
                      ? 'bg-amber-500'
                      : 'bg-slate-400'
                  "
                  :style="{ width: `${leadScore || 0}%` }"
                ></div>
              </div>
            </div>

            <!-- Phone Detected -->
            <div class="flex items-center justify-between p-2.5 rounded-lg border border-border/70 bg-muted/30 text-xs">
              <div class="flex items-center gap-2 text-muted-foreground">
                <Phone class="w-3.5 h-3.5 text-primary" />
                <span>{{ t("sandbox.detectedPhone") }}:</span>
              </div>
              <span class="font-mono font-bold text-foreground" :class="{ 'text-emerald-500': phoneDetected }">
                {{ phoneDetected || t("sandbox.notDetected") }}
              </span>
            </div>

            <!-- AI Qualification Reason -->
            <div class="space-y-1">
              <span class="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                {{ t("sandbox.reasoning") }}:
              </span>
              <p class="text-xs text-foreground bg-muted/20 p-2.5 rounded-lg border border-border/50 italic leading-relaxed">
                {{ qualificationReason || t("sandbox.analyzing") }}
              </p>
            </div>
          </CardContent>
        </Card>

        <!-- Customer Memory (Facts) Card -->
        <Card class="border border-border/80 shadow-sm bg-card">
          <CardHeader class="pb-2">
            <CardTitle class="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <BrainCircuit class="w-3.5 h-3.5 text-primary" />
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
                <CheckCircle2 class="w-3 h-3 shrink-0" />
                {{ fact.text }}
              </span>
            </div>
            <p v-else class="text-xs text-muted-foreground italic py-1">
              {{ t("sandbox.noFacts") }}
            </p>
          </CardContent>
        </Card>

        <!-- Executed Tools / Introspection -->
        <Card class="border border-border/80 shadow-sm bg-card">
          <CardHeader class="pb-2">
            <CardTitle class="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
              <Code2 class="w-3.5 h-3.5 text-primary" />
              {{ t("sandbox.toolsExecuted") }}
            </CardTitle>
          </CardHeader>
          <CardContent class="p-4 pt-0 space-y-2">
            <div v-if="executedTools && executedTools.length > 0" class="space-y-1.5">
              <div
                v-for="(tool, i) in executedTools"
                :key="i"
                class="p-2 rounded-lg bg-muted/40 border border-border/60 font-mono text-[11px] space-y-1"
              >
                <div class="flex items-center justify-between text-primary font-bold">
                  <span>{{ tool.name }}()</span>
                  <Badge variant="outline" class="text-[9px] py-0 px-1">{{ t("sandbox.toolCalled") }}</Badge>
                </div>
                <div class="text-muted-foreground text-[10px] break-all">
                  {{ JSON.stringify(tool.arguments) }}
                </div>
              </div>
            </div>
            <p v-else class="text-xs text-muted-foreground italic py-1">
              {{ t("sandbox.noTools") }}
            </p>
          </CardContent>
        </Card>
      </div>
    </div>

    <!-- Reset Confirmation Dialog -->
    <Dialog :open="isResetModalOpen" @update:open="isResetModalOpen = $event">
      <DialogContent class="sm:max-w-md">
        <DialogHeader>
          <DialogTitle class="text-base font-bold flex items-center gap-2 text-destructive">
            <Trash2 class="w-4 h-4" />
            {{ t("sandbox.reset") }}
          </DialogTitle>
          <DialogDescription class="text-xs text-muted-foreground pt-1">
            {{ t("sandbox.resetConfirm") }}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter class="flex gap-2 sm:justify-end pt-3">
          <Button variant="outline" size="sm" :disabled="isResetting" @click="isResetModalOpen = false">
            {{ t("common.cancel") }}
          </Button>
          <Button
            variant="destructive"
            size="sm"
            class="gap-1.5"
            :disabled="isResetting"
            @click="onResetSandbox"
          >
            <Trash2 class="w-3.5 h-3.5" :class="{ 'animate-spin': isResetting }" />
            <span>{{ t("common.delete") }}</span>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>
