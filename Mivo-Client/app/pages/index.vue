<script setup lang="ts">
import type { ConversationDetail, ConversationSummary, Message } from "~/types/api";
import {
  MessageSquare,
  Bot,
  ArrowLeft,
  User,
  ThumbsUp,
  ThumbsDown,
  CheckCircle2,
  Sparkles,
  Trash2,
  RefreshCw,
  Send,
  UserCheck,
  Loader2,
  Clock,
  AlertCircle,
  Film,
  ExternalLink,
  ChevronUp,
  ArrowDown,
} from "lucide-vue-next";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t } = useI18n();
const route = useRoute();
const router = useRouter();

const conversations = ref<ConversationSummary[]>([]);
const selected = ref<ConversationDetail | null>(null);
const loading = ref(true);
const messagesContainerRef = ref<HTMLElement | null>(null);

// Mobile specific active view state
const isMobileThreadActive = useState<boolean>("isMobileThreadActive", () => false);

// AI Feedback Modal state
const isFeedbackModalOpen = ref(false);
const feedbackTargetMessage = ref<Message | null>(null);
const feedbackCustomerQuery = ref("");
const feedbackCorrectionText = ref("");
const feedbackSubmitting = ref(false);
const toastMessage = ref<string | null>(null);

// Conversation Delete Confirm Modal
const isDeleteConfirmModalOpen = ref(false);
const deletingConversation = ref(false);

// Media Lightbox Preview State
const previewMediaUrl = ref<string | null>(null);
const previewMediaType = ref<"image" | "video">("image");

function openMediaPreview(url: string, type: "image" | "video" = "image") {
  previewMediaUrl.value = url;
  previewMediaType.value = type;
}

function closeMediaPreview() {
  previewMediaUrl.value = null;
}

// Infinite Scroll / Message Windowing State
const PAGE_SIZE = 35;
const displayedCount = ref(PAGE_SIZE);
const isLoadingOlder = ref(false);
const shouldStickToBottom = ref(true);
const showScrollDownBtn = ref(false);
let messagesResizeObserver: ResizeObserver | null = null;
let messagesMutationObserver: MutationObserver | null = null;

const visibleMessages = computed(() => {
  if (!selected.value) return [];
  const msgs = selected.value.messages;
  if (msgs.length <= displayedCount.value) return msgs;
  return msgs.slice(-displayedCount.value);
});

const hasOlderMessages = computed(() => {
  if (!selected.value) return false;
  return selected.value.messages.length > displayedCount.value;
});

const olderMessagesCount = computed(() => {
  if (!selected.value) return 0;
  return Math.max(0, selected.value.messages.length - displayedCount.value);
});

function loadOlderMessages() {
  if (!messagesContainerRef.value || !hasOlderMessages.value || isLoadingOlder.value) return;
  isLoadingOlder.value = true;
  const container = messagesContainerRef.value;
  const prevScrollHeight = container.scrollHeight;
  const prevScrollTop = container.scrollTop;

  displayedCount.value = Math.min(
    displayedCount.value + PAGE_SIZE,
    selected.value?.messages.length || 0
  );

  nextTick(() => {
    const newScrollHeight = container.scrollHeight;
    container.scrollTop = newScrollHeight - prevScrollHeight + prevScrollTop;
    isLoadingOlder.value = false;
  });
}

function handleMessagesScroll() {
  const el = messagesContainerRef.value;
  if (!el) return;

  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;

  // If user is within 90px from bottom, keep locked to bottom
  if (distanceFromBottom <= 90) {
    shouldStickToBottom.value = true;
    showScrollDownBtn.value = false;
  } else {
    // User intentionally scrolled up to read past history
    shouldStickToBottom.value = false;
    showScrollDownBtn.value = true;
  }

  // Infinite scroll check: if near top (scrollTop <= 60px) and has older messages
  if (el.scrollTop <= 60 && hasOlderMessages.value && !isLoadingOlder.value) {
    loadOlderMessages();
  }
}

// Media Parser
interface ParsedMessagePart {
  type: "text" | "image" | "video" | "reel" | "audio" | "placeholder";
  url?: string;
  text?: string;
  badge?: string;
}

function parseMessage(content: string): ParsedMessagePart[] {
  if (!content) return [];
  const trimmed = content.trim();

  // Known Instagram placeholders
  if (trimmed === "[Rasm yuborildi]") {
    return [{ type: "placeholder", badge: "📷 Rasm", text: "Instagram rasmi yuborildi" }];
  }
  if (trimmed === "[Video yuborildi]") {
    return [{ type: "placeholder", badge: "🎥 Video", text: "Instagram videosi yuborildi" }];
  }
  if (trimmed === "[Ig_reel yuborildi]" || trimmed === "[Reels / Story ulashildi]") {
    return [{ type: "placeholder", badge: "🎬 Reels / Story", text: "Instagram Reels ulashildi" }];
  }

  const parts: ParsedMessagePart[] = [];
  const tagRegex = /\[(image|video|reel|audio):\s*([^\s\]]+)\]/gi;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = tagRegex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      const chunk = content.slice(lastIndex, match.index).trim();
      if (chunk) parts.push({ type: "text", text: chunk });
    }
    const mediaType = match[1].toLowerCase() as "image" | "video" | "reel" | "audio";
    const mediaUrl = match[2].trim();
    parts.push({ type: mediaType, url: mediaUrl });
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < content.length) {
    const remaining = content.slice(lastIndex).trim();
    if (remaining) {
      if (remaining === "[Rasm yuborildi]") {
        parts.push({ type: "placeholder", badge: "📷 Rasm", text: "Instagram rasmi yuborildi" });
      } else if (remaining === "[Video yuborildi]") {
        parts.push({ type: "placeholder", badge: "🎥 Video", text: "Instagram videosi yuborildi" });
      } else if (remaining === "[Ig_reel yuborildi]" || remaining === "[Reels / Story ulashildi]") {
        parts.push({ type: "placeholder", badge: "🎬 Reels / Story", text: "Instagram Reels ulashildi" });
      } else {
        parts.push({ type: "text", text: remaining });
      }
    }
  }

  if (parts.length === 0) {
    parts.push({ type: "text", text: content });
  }

  return parts;
}

function formatTime(isoStr?: string) {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

// Operator Reply state
const replyText = ref("");
const sendingReply = ref(false);

async function handleSendReply() {
  if (!selected.value || !replyText.value.trim()) return;
  const content = replyText.value.trim();
  const convId = selected.value.id;

  // Clear input immediately so operator can type next message without waiting
  replyText.value = "";

  // Create optimistic message
  const tempId = `temp-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const optimisticMsg: Message = {
    id: tempId,
    sender_type: "human",
    content: content,
    message_type: "text",
    created_at: new Date().toISOString(),
    status: "pending",
  };

  selected.value.messages.push(optimisticMsg);
  scrollToBottom(true);

  // Update conversation last_message_at in sidebar
  const idx = conversations.value.findIndex((c) => c.id === convId);
  if (idx !== -1) {
    conversations.value[idx].last_message_at = optimisticMsg.created_at;
  }

  // Send reply in background
  try {
    const newMsg = await api.sendConversationReply(convId, content);
    if (selected.value && selected.value.id === convId) {
      const msgIdx = selected.value.messages.findIndex((m) => m.id === tempId);
      if (msgIdx !== -1) {
        selected.value.messages[msgIdx] = { ...newMsg, status: "sent" };
      }
    }
  } catch (err: any) {
    console.error("Failed to send reply", err);
    if (selected.value && selected.value.id === convId) {
      const msg = selected.value.messages.find((m) => m.id === tempId);
      if (msg) {
        msg.status = "failed";
      }
    }
    showToast(err?.message || t("conversations.sendError"));
  }
}

async function retrySendMessage(msg: Message) {
  if (!selected.value || msg.status !== "failed") return;
  msg.status = "pending";
  try {
    const newMsg = await api.sendConversationReply(selected.value.id, msg.content);
    if (selected.value) {
      const idx = selected.value.messages.findIndex((m) => m.id === msg.id);
      if (idx !== -1) {
        selected.value.messages[idx] = { ...newMsg, status: "sent" };
      }
    }
  } catch (err: any) {
    msg.status = "failed";
    showToast(t("conversations.retryError"));
  }
}

function handleReplyKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSendReply();
  }
}

function showToast(msg: string) {
  toastMessage.value = msg;
  setTimeout(() => {
    toastMessage.value = null;
  }, 3500);
}

function scrollToBottomDirect() {
  const el = messagesContainerRef.value;
  if (!el) return;
  el.scrollTop = el.scrollHeight;
}

function scrollToBottom(smooth = false) {
  shouldStickToBottom.value = true;
  nextTick(() => {
    const el = messagesContainerRef.value;
    if (!el) return;
    el.scrollTo({
      top: el.scrollHeight,
      behavior: smooth ? "smooth" : "auto",
    });

    setTimeout(scrollToBottomDirect, 50);
    setTimeout(scrollToBottomDirect, 150);
    setTimeout(scrollToBottomDirect, 300);
  });
}

function scrollToBottomSmooth() {
  shouldStickToBottom.value = true;
  showScrollDownBtn.value = false;
  scrollToBottom(true);
}

function attachObservers() {
  const el = messagesContainerRef.value;
  if (!el) return;

  if (messagesResizeObserver) messagesResizeObserver.disconnect();
  if (messagesMutationObserver) messagesMutationObserver.disconnect();

  messagesResizeObserver = new ResizeObserver(() => {
    if (shouldStickToBottom.value) {
      scrollToBottomDirect();
    }
  });
  messagesResizeObserver.observe(el);

  messagesMutationObserver = new MutationObserver(() => {
    if (shouldStickToBottom.value) {
      scrollToBottomDirect();
    }
  });
  messagesMutationObserver.observe(el, { childList: true, subtree: true });
}

async function openConversation(id: string, updateQuery = true) {
  try {
    selected.value = await api.getConversation(id);
    displayedCount.value = PAGE_SIZE;
    isMobileThreadActive.value = true;
    shouldStickToBottom.value = true;
    showScrollDownBtn.value = false;
    scrollToBottom(false);
    nextTick(() => {
      attachObservers();
    });
    if (updateQuery && route.query.id !== id) {
      router.replace({ query: { ...route.query, id } });
    }
  } catch (err) {
    console.error("Failed to load conversation", err);
  }
}

function closeMobileThread() {
  isMobileThreadActive.value = false;
  if (route.query.id) {
    const nextQuery = { ...route.query };
    delete nextQuery.id;
    router.replace({ query: nextQuery });
  }
}

async function toggleConversationStatus() {
  if (!selected.value) return;
  const newStatus = selected.value.status === "ai_active" || selected.value.status === "active" ? "human_needed" : "ai_active";
  try {
    const updated = await api.updateConversationStatus(selected.value.id, newStatus);
    selected.value.status = updated.status;
    const idx = conversations.value.findIndex((c) => c.id === selected.value?.id);
    if (idx !== -1) conversations.value[idx].status = updated.status;
    showToast(newStatus === "ai_active" ? "Suhbat AI rejimiga o'tkazildi" : "AI bu suhbatda endi javob yozmaydi");
  } catch (err) {
    console.error("Failed to update status", err);
  }
}

// Polling: DMs arrive from real customers at any moment, not just while this
// tab is focused — a manual refresh button plus a quiet background poll
// means the owner doesn't have to reload the page to see a new message land.
const refreshing = ref(false);
const POLL_INTERVAL_MS = 12000;
let pollTimer: ReturnType<typeof setInterval> | null = null;

async function refreshData(showSpinner = false) {
  if (showSpinner) refreshing.value = true;
  try {
    const updatedList = await api.listConversations();
    conversations.value = updatedList;

    if (selected.value) {
      const stillExists = updatedList.some((c) => c.id === selected.value!.id);
      if (stillExists) {
        const previousCount = selected.value.messages.length;
        const updatedDetail = await api.getConversation(selected.value.id);
        selected.value = updatedDetail;
        // Only jump the scroll position on an actual new message — a quiet
        // background refresh shouldn't yank the view while someone's
        // reading back through older messages.
        if (updatedDetail.messages.length > previousCount) scrollToBottom();
      }
    }
  } catch (err) {
    console.error("Failed to refresh conversations", err);
  } finally {
    if (showSpinner) refreshing.value = false;
  }
}

function openDeleteConfirmModal() {
  isDeleteConfirmModalOpen.value = true;
}

async function confirmDeleteConversation() {
  if (!selected.value) return;
  deletingConversation.value = true;
  try {
    await api.deleteConversation(selected.value.id);
    conversations.value = conversations.value.filter((c) => c.id !== selected.value?.id);
    selected.value = null;
    isDeleteConfirmModalOpen.value = false;
    showToast("Suhbat o'chirildi");
    router.replace({ query: {} });
  } catch (err) {
    console.error("Failed to delete conversation", err);
  } finally {
    deletingConversation.value = false;
  }
}

async function handlePositiveFeedback(msg: Message) {
  try {
    await api.submitAiFeedback({
      conversation_id: selected.value?.id,
      message_id: msg.id,
      rating: "thumb_up",
      ai_response: msg.content,
    });
    showToast("Rahmat! Mivo AI ushbu javobni ijobiy baholadi 👍");
  } catch (err) {
    console.error("Failed to submit feedback", err);
  }
}

function openCorrectionModal(msg: Message) {
  feedbackTargetMessage.value = msg;
  if (selected.value) {
    const msgIndex = selected.value.messages.findIndex((m) => m.id === msg.id);
    if (msgIndex > 0 && selected.value.messages[msgIndex - 1].sender_type === "customer") {
      feedbackCustomerQuery.value = selected.value.messages[msgIndex - 1].content;
    } else {
      feedbackCustomerQuery.value = "";
    }
  }
  feedbackCorrectionText.value = "";
  isFeedbackModalOpen.value = true;
}

function closeCorrectionModal() {
  isFeedbackModalOpen.value = false;
  feedbackTargetMessage.value = null;
}

async function submitCorrection() {
  if (!feedbackTargetMessage.value || !feedbackCorrectionText.value.trim()) return;
  feedbackSubmitting.value = true;
  try {
    await api.submitAiFeedback({
      conversation_id: selected.value?.id,
      message_id: feedbackTargetMessage.value.id,
      rating: "thumb_down",
      customer_query: feedbackCustomerQuery.value,
      ai_response: feedbackTargetMessage.value.content,
      correction: feedbackCorrectionText.value.trim(),
    });
    closeCorrectionModal();
    showToast("Mivo AI ushbu tuzatishni o'rgandi ✨");
  } catch (err) {
    console.error("Failed to submit correction", err);
  } finally {
    feedbackSubmitting.value = false;
  }
}

onMounted(async () => {
  try {
    conversations.value = await api.listConversations();
    const queryId = route.query.id as string | undefined;
    if (queryId && conversations.value.some((c) => c.id === queryId)) {
      await openConversation(queryId, false);
    } else if (conversations.value.length > 0 && window.innerWidth > 768) {
      await openConversation(conversations.value[0].id, false);
    }
  } finally {
    loading.value = false;
  }
  pollTimer = setInterval(() => refreshData(false), POLL_INTERVAL_MS);
});

onUnmounted(() => {
  isMobileThreadActive.value = false;
  if (pollTimer) clearInterval(pollTimer);
  if (messagesResizeObserver) messagesResizeObserver.disconnect();
  if (messagesMutationObserver) messagesMutationObserver.disconnect();
});

watch(
  () => route.query.id,
  async (newId) => {
    if (newId && typeof newId === "string" && selected.value?.id !== newId) {
      await openConversation(newId, false);
    } else if (!newId) {
      isMobileThreadActive.value = false;
    }
  }
);

function cleanCustomerName(username?: string | null) {
  if (!username) return t("conversations.customer");
  const str = username.trim();
  if (str.toLowerCase().startsWith("@user_") || str.toLowerCase().startsWith("user_")) {
    return t("conversations.customer");
  }
  return str;
}

function getAvatarLetter(username?: string | null) {
  if (!username) return "M";
  const clean = cleanCustomerName(username).replace("@", "").trim();
  return clean.charAt(0).toUpperCase();
}

function getStatusBadgeClass(status: string) {
  if (status === "ai_active" || status === "active") return "lead-badge-warm";
  if (status === "human_needed") return "lead-badge-cold";
  return "lead-badge-cold";
}
</script>

<template>
  <div class="conversations-page" :class="{ 'mobile-thread-active': isMobileThreadActive }">
    <!-- Toast Notification -->
    <div v-if="toastMessage" class="feedback-toast">
      <Sparkles :size="16" class="text-primary" />
      <span>{{ toastMessage }}</span>
    </div>

    <!-- Loading State with Smooth Animated Messenger Skeleton -->
    <div v-if="loading" class="split-view">
      <div class="conversation-sidebar p-4 space-y-3">
        <Skeleton class="h-5 w-32 mb-4" />
        <div v-for="i in 5" :key="i" class="flex items-center gap-3 p-2 rounded-lg">
          <Skeleton class="w-10 h-10 rounded-full shrink-0" />
          <div class="space-y-2 flex-1">
            <Skeleton class="h-4 w-28" />
            <Skeleton class="h-3 w-16" />
          </div>
        </div>
      </div>
      <div class="thread-area p-6 space-y-6 flex flex-col justify-between">
        <div class="flex items-center justify-between pb-4 border-b border-border">
          <div class="flex items-center gap-3">
            <Skeleton class="w-10 h-10 rounded-full" />
            <Skeleton class="h-5 w-32" />
          </div>
          <Skeleton class="h-6 w-20 rounded-full" />
        </div>
        <div class="space-y-4 flex-1">
          <Skeleton class="h-16 w-2/3 rounded-xl ml-auto" />
          <Skeleton class="h-14 w-1/2 rounded-xl" />
          <Skeleton class="h-16 w-3/4 rounded-xl ml-auto" />
        </div>
      </div>
    </div>

    <!-- Split View Messenger Container -->
    <div v-else class="split-view" :class="{ 'mobile-thread-open': isMobileThreadActive }">
      <!-- Conversation List Sidebar -->
      <div class="conversation-sidebar" :class="{ 'mobile-hidden': isMobileThreadActive }">
        <div class="px-4 py-3.5 border-b border-border text-xs font-bold text-muted-foreground uppercase tracking-wide shrink-0">
          {{ t("conversations.activeChats") }} ({{ conversations.length }})
        </div>

        <ul v-if="conversations.length > 0" class="flex-1 min-h-0 overflow-y-auto overscroll-contain m-0 p-0 list-none custom-scrollbar">
          <li
            v-for="c in conversations"
            :key="c.id"
            class="conversation-item"
            :class="{ active: selected?.id === c.id }"
            @click="openConversation(c.id)"
          >
            <div class="avatar">
              {{ getAvatarLetter(c.customer_username) }}
            </div>
            <div class="overflow-hidden flex-1">
              <div class="flex items-center justify-between gap-2">
                <span class="conversation-item-title truncate">
                  {{ cleanCustomerName(c.customer_username) }}
                </span>
              </div>
              <div class="flex items-center justify-between mt-1">
                <span
                  class="inline-flex items-center justify-center h-5 w-5 rounded-md"
                  :class="c.status === 'ai_active' || c.status === 'active' ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'"
                  :title="c.status === 'ai_active' || c.status === 'active' ? 'AI' : 'Operator'"
                >
                  <Bot v-if="c.status === 'ai_active' || c.status === 'active'" :size="12" />
                  <User v-else :size="12" />
                </span>
                <span class="text-xs text-muted-foreground">Instagram</span>
              </div>
            </div>
          </li>
        </ul>
        <div v-else class="muted p-8 text-center text-sm">
          {{ t("conversations.noActiveChats") }}
        </div>
      </div>

      <!-- Thread Details Area (Chat Messages or Empty State) -->
      <div class="thread-area" :class="{ 'mobile-visible': isMobileThreadActive }">
        <template v-if="selected">
          <!-- Thread Header with DM Management Actions -->
          <div class="px-3 py-2.5 sm:px-5 sm:py-3.5 border-b border-border flex items-center justify-between shrink-0 bg-card gap-2 pt-[max(0.625rem,env(safe-area-inset-top))]">
            <div class="flex items-center gap-1.5 sm:gap-2.5 min-w-0 flex-1">
              <!-- Mobile Back Button -->
              <button
                type="button"
                class="mobile-back-btn inline-flex items-center justify-center h-8 w-8 sm:h-9 sm:w-9 rounded-xl border border-border/80 bg-muted/50 hover:bg-muted text-foreground transition-all shadow-2xs active:scale-95 shrink-0 cursor-pointer mr-0.5"
                :title="t('conversations.backToList')"
                :aria-label="t('common.back')"
                @click="closeMobileThread"
              >
                <ArrowLeft :size="18" class="shrink-0" />
              </button>

              <div class="overflow-hidden flex items-center min-w-0">
                <div class="font-bold text-sm sm:text-base text-foreground truncate">
                  {{ cleanCustomerName(selected.customer_username) }}
                </div>
              </div>
            </div>

            <!-- DM Management Controls -->
            <div class="flex items-center gap-1 sm:gap-2 shrink-0">
              <!-- Mode Toggle Button (Robot / Odam icon) -->
              <button
                type="button"
                class="inline-flex items-center justify-center h-8 w-8 sm:h-9 sm:w-9 rounded-xl border transition-all shadow-2xs active:scale-95 cursor-pointer"
                :class="selected.status === 'ai_active' || selected.status === 'active'
                  ? 'border-primary/40 bg-primary/10 text-primary hover:bg-primary/15'
                  : 'border-border/80 bg-card text-muted-foreground hover:bg-muted hover:text-foreground'"
                :title="selected.status === 'ai_active' || selected.status === 'active'
                  ? t('conversations.aiModeActiveTooltip')
                  : t('conversations.operatorModeActiveTooltip')"
                @click="toggleConversationStatus"
              >
                <Bot v-if="selected.status === 'ai_active' || selected.status === 'active'" :size="16" class="shrink-0" />
                <User v-else :size="16" class="shrink-0" />
              </button>

              <!-- Manual Refresh -->
              <button
                type="button"
                class="inline-flex items-center justify-center h-8 w-8 sm:h-9 sm:w-9 rounded-xl border border-border/80 bg-card hover:bg-muted text-foreground transition-all shadow-2xs active:scale-95 disabled:opacity-50 cursor-pointer"
                :title="t('common.refresh')"
                :disabled="refreshing"
                @click="refreshData(true)"
              >
                <RefreshCw :size="14" :class="{ 'animate-spin': refreshing }" />
              </button>

              <!-- Delete Conversation Button -->
              <button
                type="button"
                class="inline-flex items-center justify-center h-8 w-8 sm:h-9 sm:w-9 rounded-xl border border-destructive/30 bg-card hover:bg-destructive/10 text-destructive transition-all shadow-2xs active:scale-95 cursor-pointer"
                :title="t('conversations.deleteChatTitle')"
                @click="openDeleteConfirmModal"
              >
                <Trash2 :size="14" />
              </button>
            </div>
          </div>

          <!-- Thread Message Bubbles Container -->
          <div
            ref="messagesContainerRef"
            class="flex-1 min-h-0 overflow-y-auto overscroll-contain p-4 sm:p-5 flex flex-col gap-3.5 custom-scrollbar"
            @scroll="handleMessagesScroll"
          >
            <!-- Load Older Messages Trigger -->
            <div v-if="hasOlderMessages" class="flex justify-center py-1 shrink-0">
              <button
                type="button"
                class="text-xs px-3.5 py-1.5 rounded-full bg-muted/80 hover:bg-muted text-muted-foreground hover:text-foreground transition-all flex items-center gap-1.5 shadow-2xs border border-border/60 cursor-pointer"
                :disabled="isLoadingOlder"
                @click="loadOlderMessages"
              >
                <Loader2 v-if="isLoadingOlder" :size="12" class="animate-spin" />
                <ChevronUp v-else :size="12" />
                <span>{{ t("conversations.loadOlder", { count: olderMessagesCount }) }}</span>
              </button>
            </div>

            <!-- Message Bubbles -->
            <div
              v-for="m in visibleMessages"
              :key="m.id"
              class="bubble max-w-[85%] sm:max-w-[80%] break-words relative transition-all duration-150"
              :class="[
                m.sender_type === 'customer' ? 'bubble-customer' : m.sender_type === 'human' ? 'bubble-human' : 'bubble-ai',
                m.status === 'pending' ? 'opacity-70' : '',
                m.status === 'failed' ? 'border-destructive/60 bg-destructive/5' : ''
              ]"
            >
              <!-- Bubble Header -->
              <div class="flex items-center justify-between text-xs mb-1.5 gap-2 opacity-75">
                <div class="flex items-center gap-1.5">
                  <User v-if="m.sender_type === 'customer'" :size="12" />
                  <UserCheck v-else-if="m.sender_type === 'human'" :size="12" class="text-blue-500" />
                  <Bot v-else :size="12" class="text-primary" />
                  <span class="font-semibold text-xs text-foreground/90">
                    {{ m.sender_type === 'customer' ? cleanCustomerName(selected.customer_username) : m.sender_type === 'human' ? t('conversations.youOperator') : 'Mivo AI' }}
                  </span>
                </div>

                <!-- Status & Time Badge -->
                <div class="flex items-center gap-1 text-[11px]">
                  <span v-if="m.status === 'pending'" class="text-muted-foreground flex items-center gap-1">
                    <Clock :size="11" class="animate-pulse" />
                    <span class="hidden sm:inline">{{ t("conversations.sending") }}</span>
                  </span>
                  <span
                    v-else-if="m.status === 'failed'"
                    class="text-destructive flex items-center gap-1 cursor-pointer font-medium hover:underline"
                    :title="t('conversations.retryTitle')"
                    @click="retrySendMessage(m)"
                  >
                    <AlertCircle :size="11" />
                    <span>{{ t("conversations.retry") }}</span>
                  </span>
                  <span v-else class="opacity-70">{{ formatTime(m.created_at) }}</span>
                </div>
              </div>

              <!-- Message Content & Media Rendering -->
              <div class="text-sm leading-relaxed">
                <div v-for="(part, pIdx) in parseMessage(m.content)" :key="pIdx" class="my-0.5">
                  <!-- Text Block -->
                  <div v-if="part.type === 'text'" class="whitespace-pre-wrap">
                    {{ part.text }}
                  </div>

                  <!-- Image Attachment -->
                  <div
                    v-else-if="part.type === 'image' && part.url"
                    class="mt-1.5 rounded-xl overflow-hidden border border-border/50 max-w-[280px] sm:max-w-[340px] bg-black/5 dark:bg-white/5 cursor-pointer hover:opacity-95 transition-opacity shadow-2xs group relative"
                    @click="openMediaPreview(part.url, 'image')"
                  >
                    <img
                      :src="part.url"
                      alt="Rasm"
                      class="w-full h-auto max-h-[340px] object-cover rounded-xl group-hover:scale-[1.01] transition-transform duration-200"
                      loading="lazy"
                    />
                    <div class="absolute inset-0 bg-black/20 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center text-white text-xs font-medium">
                      {{ t("common.zoom") }}
                    </div>
                  </div>

                  <!-- Reel / Share Card -->
                  <div
                    v-else-if="part.type === 'reel'"
                    class="mt-1.5 p-3 rounded-xl border border-pink-500/20 bg-gradient-to-r from-pink-500/10 via-purple-500/10 to-indigo-500/10 flex items-center justify-between gap-3 shadow-2xs"
                  >
                    <div class="flex items-center gap-2.5 min-w-0">
                      <div class="h-9 w-9 rounded-lg bg-pink-500/20 text-pink-500 flex items-center justify-center shrink-0">
                        <Film :size="18" />
                      </div>
                      <div class="min-w-0">
                        <div class="text-xs font-semibold text-foreground truncate">Instagram Reel</div>
                        <div class="text-[11px] text-muted-foreground truncate">{{ t("conversations.reelShared") }}</div>
                      </div>
                    </div>
                    <a
                      v-if="part.url"
                      :href="part.url"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-xs text-primary hover:underline flex items-center gap-1 shrink-0 px-2.5 py-1 rounded-lg bg-card border border-border"
                    >
                      <span>{{ t("common.open") }}</span>
                      <ExternalLink :size="11" />
                    </a>
                  </div>

                  <!-- Video Attachment -->
                  <div
                    v-else-if="part.type === 'video' && part.url"
                    class="mt-1.5 rounded-xl overflow-hidden border border-border/50 max-w-[280px] sm:max-w-[340px] bg-black shadow-2xs"
                  >
                    <video :src="part.url" controls class="w-full max-h-[280px] rounded-xl"></video>
                  </div>

                  <!-- Instagram Placeholder Badge (for legacy messages) -->
                  <div
                    v-else-if="part.type === 'placeholder'"
                    class="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border/70 bg-muted/50 text-xs font-medium my-0.5"
                  >
                    <span class="font-semibold text-foreground">{{ part.badge }}</span>
                    <span class="text-muted-foreground">{{ part.text }}</span>
                  </div>
                </div>
              </div>

              <!-- AI Feedback & Learning Action Bar -->
              <div v-if="m.sender_type === 'ai'" class="ai-feedback-bar">
                <button
                  type="button"
                  class="feedback-action-btn"
                  :title="t('conversations.goodResponse')"
                  @click="handlePositiveFeedback(m)"
                >
                  <ThumbsUp :size="12" />
                </button>
                <button
                  type="button"
                  class="feedback-action-btn"
                  :title="t('conversations.correctResponseTitle')"
                  @click="openCorrectionModal(m)"
                >
                  <ThumbsDown :size="12" />
                  <span>{{ t("conversations.correct") }}</span>
                </button>
              </div>
            </div>

            <!-- Bottom spacer div -->
            <div class="h-2 w-full shrink-0" />
          </div>

          <!-- Telegram-style Floating Scroll to Bottom Button -->
          <transition
            enter-active-class="transition duration-200 ease-out"
            enter-from-class="opacity-0 scale-75 translate-y-3"
            enter-to-class="opacity-100 scale-100 translate-y-0"
            leave-active-class="transition duration-150 ease-in"
            leave-from-class="opacity-100 scale-100 translate-y-0"
            leave-to-class="opacity-0 scale-75 translate-y-3"
          >
            <button
              v-if="showScrollDownBtn"
              type="button"
              class="absolute bottom-20 right-6 h-10 w-10 rounded-full bg-card/95 hover:bg-card text-foreground border border-border/80 shadow-lg flex items-center justify-center transition-all active:scale-95 z-20 cursor-pointer backdrop-blur-md hover:shadow-xl group"
              :title="t('conversations.scrollToBottom')"
              @click="scrollToBottomSmooth"
            >
              <ArrowDown :size="18" class="text-primary group-hover:scale-110 transition-transform" />
            </button>
          </transition>

          <!-- Operator Live Reply Input Bar (Optimistic Instant Response) -->
          <div class="p-2.5 sm:p-3.5 border-t border-border bg-card/95 backdrop-blur-sm shrink-0 pb-[max(0.625rem,env(safe-area-inset-bottom))]">
            <form class="flex items-center gap-2" @submit.prevent="handleSendReply">
              <Input
                v-model="replyText"
                type="text"
                :placeholder="t('conversations.typeMessage')"
                class="flex-1 h-10 px-4 rounded-xl border border-border bg-background text-sm text-foreground focus-visible:ring-2 focus-visible:ring-primary/30 transition-all placeholder:text-muted-foreground/60"
                @keydown="handleReplyKeydown"
              />
              <Button
                type="submit"
                class="h-10 px-4 rounded-xl gap-2 font-medium shrink-0 shadow-2xs inline-flex flex-row items-center justify-center whitespace-nowrap cursor-pointer"
                :disabled="!replyText.trim()"
              >
                <Send :size="15" class="shrink-0" />
                <span class="whitespace-nowrap">{{ t("conversations.send") }}</span>
              </Button>
            </form>
          </div>
        </template>

        <div v-else class="flex h-full items-center justify-center text-muted-foreground p-8 text-center">
          {{ t("conversations.selectChat") }}
        </div>
      </div>
    </div>


    <!-- AI Learning & Correction Modal -->
    <Dialog :open="isFeedbackModalOpen" @update:open="(v) => { if (!v) closeCorrectionModal() }">
      <DialogContent class="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle class="flex items-center gap-2">
            <Sparkles :size="20" class="text-primary" />
            {{ t("conversations.trainAiTitle") }}
          </DialogTitle>
        </DialogHeader>

        <div class="flex flex-col gap-4">
          <div v-if="feedbackCustomerQuery" class="bg-muted/50 px-4 py-3 rounded-lg border-l-2 border-primary">
            <div class="text-xs text-muted-foreground font-semibold mb-1">{{ t("conversations.customerQuery") }}</div>
            <div class="text-sm text-foreground">"{{ feedbackCustomerQuery }}"</div>
          </div>

          <div v-if="feedbackTargetMessage" class="bg-destructive/10 px-4 py-3 rounded-lg border-l-2 border-destructive">
            <div class="text-xs text-destructive font-semibold mb-1">{{ t("conversations.aiWrongResponse") }}</div>
            <div class="text-sm text-foreground">{{ feedbackTargetMessage.content }}</div>
          </div>

          <div class="space-y-1.5">
            <Label for="feedback-correction">
              {{ t("conversations.aiCorrectPrompt") }}
            </Label>
            <Textarea
              id="feedback-correction"
              v-model="feedbackCorrectionText"
              rows="3"
              :placeholder="t('conversations.aiCorrectPlaceholder')"
            />
          </div>

          <div class="flex items-center justify-end gap-3 mt-2">
            <Button variant="outline" @click="closeCorrectionModal">{{ t("common.cancel") }}</Button>
            <Button :disabled="feedbackSubmitting || !feedbackCorrectionText.trim()" class="gap-2" @click="submitCorrection">
              <CheckCircle2 :size="16" />
              {{ feedbackSubmitting ? t("common.saving") : t("conversations.trainAndSave") }}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>

    <!-- Delete Conversation Confirm Modal -->
    <Dialog v-model:open="isDeleteConfirmModalOpen">
      <DialogContent class="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle class="text-destructive">{{ t("conversations.deleteModalTitle") }}</DialogTitle>
        </DialogHeader>
        <p class="text-sm text-foreground mb-5">
          {{ t("conversations.deleteModalConfirm", { name: selected?.customer_username || t("conversations.customer") }) }}
        </p>
        <div class="flex items-center justify-end gap-3">
          <Button variant="outline" @click="isDeleteConfirmModalOpen = false">{{ t("common.cancel") }}</Button>
          <Button variant="destructive" class="gap-2" :disabled="deletingConversation" @click="confirmDeleteConversation">
            <Trash2 :size="16" />
            {{ deletingConversation ? t("common.deleting") : t("common.delete") }}
          </Button>
        </div>
      </DialogContent>
    </Dialog>

    <!-- Media Preview Lightbox Modal -->
    <Dialog :open="!!previewMediaUrl" @update:open="(v) => { if (!v) closeMediaPreview() }">
      <DialogContent class="sm:max-w-[720px] p-2 bg-background/95 backdrop-blur-md border border-border/80">
        <div class="flex flex-col items-center justify-center p-1">
          <img
            v-if="previewMediaType === 'image' && previewMediaUrl"
            :src="previewMediaUrl"
            alt="Media Preview"
            class="max-h-[82vh] w-auto max-w-full rounded-lg object-contain shadow-md"
          />
          <video
            v-else-if="previewMediaType === 'video' && previewMediaUrl"
            :src="previewMediaUrl"
            controls
            autoplay
            class="max-h-[82vh] w-auto max-w-full rounded-lg shadow-md"
          ></video>
        </div>
      </DialogContent>
    </Dialog>

  </div>
</template>
