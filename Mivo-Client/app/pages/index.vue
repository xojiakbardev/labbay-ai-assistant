<script setup lang="ts">
import { toast } from "vue-sonner";
import { ApiError } from "~/composables/useApi";
import { safeHttpsUrl } from "~/lib/utils";
import type { ConversationDetail, ConversationSummary, Message } from "~/types/api";
import {
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
} from "@lucide/vue";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const { onConversationUpdated } = useNotifications();

const CONVERSATION_PAGE = 50;
const MAX_CONVERSATION_PAGE = 200;
const MESSAGE_PAGE = 100;
const MAX_MESSAGE_LIMIT = 500;
const REPLY_MAX_LENGTH = 1000;

const conversations = ref<ConversationSummary[]>([]);
const hasMoreConversations = ref(false);
const loadingMoreConversations = ref(false);
// Server state of the open thread: its messages are exactly what the server
// returned (oldest first). Optimistic replies live in `localMessages`.
const selected = ref<ConversationDetail | null>(null);
const loading = ref(true);
const listError = ref<string | null>(null);
// A background refresh failed; cleared by the next successful one.
const syncError = ref(false);
const threadLoading = ref(false);
const messagesContainerRef = ref<HTMLElement | null>(null);

// Replies the server hasn't acknowledged, per conversation — pending, or
// failed before reaching it. Kept apart from server messages so no merge or
// reload can ever wipe one (and its text) out, and they survive switching.
const localMessages = reactive<Record<string, Message[]>>({});

const threadMessages = computed<Message[]>(() => {
  if (!selected.value) return [];
  return [...selected.value.messages, ...(localMessages[selected.value.id] ?? [])];
});

// Mobile specific active view state
const isMobileThreadActive = useState<boolean>("isMobileThreadActive", () => false);

// AI Feedback Modal state
const isFeedbackModalOpen = ref(false);
const feedbackTargetMessage = ref<Message | null>(null);
const feedbackCustomerQuery = ref("");
const feedbackCorrectionText = ref("");
const feedbackSubmitting = ref(false);

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

function errorText(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t("conversations.genericError");
}

function isAbortError(err: unknown): boolean {
  return (err as { name?: string } | null)?.name === "AbortError";
}

function isAiStatus(status: string | undefined) {
  return status === "ai_active" || status === "active";
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
  const msgs = threadMessages.value;
  if (msgs.length <= displayedCount.value) return msgs;
  return msgs.slice(-displayedCount.value);
});

const hiddenLoadedCount = computed(() => Math.max(0, threadMessages.value.length - displayedCount.value));

// Older messages exist either in the local window or on the server (the
// detail endpoint returns only the most recent ones).
const hasOlderMessages = computed(() => {
  if (!selected.value) return false;
  if (hiddenLoadedCount.value > 0) return true;
  return selected.value.has_more_messages && selected.value.messages.length < MAX_MESSAGE_LIMIT;
});

async function loadOlderMessages() {
  if (!messagesContainerRef.value || !hasOlderMessages.value || isLoadingOlder.value || !selected.value) return;
  isLoadingOlder.value = true;
  const container = messagesContainerRef.value;
  const prevScrollHeight = container.scrollHeight;
  const prevScrollTop = container.scrollTop;

  if (hiddenLoadedCount.value === 0) {
    // Everything loaded is already shown — fetch a longer tail from the server.
    const convId = selected.value.id;
    const limit = Math.min(selected.value.messages.length + MESSAGE_PAGE, MAX_MESSAGE_LIMIT);
    try {
      const detail = await api.getConversation(convId, { limit });
      replaceThread(convId, detail);
    } catch (err) {
      toast.error(errorText(err));
      isLoadingOlder.value = false;
      return;
    }
  }

  displayedCount.value = Math.min(displayedCount.value + PAGE_SIZE, threadMessages.value.length);

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

// Media comes only from attachment_url/attachment_type, and only https URLs
// are rendered or linked — message text is never parsed for links.
interface MessageMedia {
  kind: "image" | "video" | "audio" | "link";
  url: string;
}

function mediaOf(m: Message): MessageMedia | null {
  const url = safeHttpsUrl(m.attachment_url);
  if (!url) return null;
  const type = (m.attachment_type || "").toLowerCase();
  if (type === "image") return { kind: "image", url };
  if (type === "video") return { kind: "video", url };
  if (type === "audio") return { kind: "audio", url };
  return { kind: "link", url };
}

function isSharedPost(m: Message) {
  return ["share", "ig_reel", "story_mention"].includes((m.attachment_type || "").toLowerCase());
}

function formatTime(isoStr?: string) {
  if (!isoStr) return "";
  const d = new Date(isoStr);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// --- Thread state helpers ----------------------------------------------------

/** Merges server messages into the open thread: known ids are updated in
 * place (delivery status changes), new ones appended. Returns the new ones. */
function mergeIncoming(convId: string, incoming: Message[]): Message[] {
  const conv = selected.value;
  if (!conv || conv.id !== convId || incoming.length === 0) return [];
  const byId = new Map(incoming.map((m) => [m.id, m]));
  const updated = conv.messages.map((m) => byId.get(m.id) ?? m);
  const known = new Set(updated.map((m) => m.id));
  const added = incoming.filter((m) => !known.has(m.id));
  conv.messages = [...updated, ...added];
  return added;
}

/** Replaces the open thread with a fresh detail response. */
function replaceThread(convId: string, detail: ConversationDetail): Message[] {
  const conv = selected.value;
  if (!conv || conv.id !== convId) return [];
  const known = new Set(conv.messages.map((m) => m.id));
  const added = detail.messages.filter((m) => !known.has(m.id));
  selected.value = detail;
  return added;
}

/** Id to poll `?after=` from: the last message, or — while an outbound
 * message is still pending — the one before it, so its final delivery
 * status comes back too. null means "reload the thread". */
function pollAnchorId(msgs: Message[]): string | null {
  if (msgs.length === 0) return null;
  const firstPending = msgs.findIndex((m) => m.delivery_status === "pending");
  if (firstPending === -1) return msgs[msgs.length - 1]!.id;
  return firstPending === 0 ? null : msgs[firstPending - 1]!.id;
}

async function syncThread(convId: string, signal: AbortSignal): Promise<Message[]> {
  const conv = selected.value;
  if (!conv || conv.id !== convId) return [];
  const anchor = pollAnchorId(conv.messages);
  if (anchor) {
    try {
      const incoming: Message[] = [];
      let after = anchor;
      // Pages of MESSAGE_PAGE until the server has nothing newer.
      for (;;) {
        const page = await api.listMessagesAfter(convId, after, { limit: MESSAGE_PAGE, signal });
        incoming.push(...page);
        if (page.length < MESSAGE_PAGE) break;
        after = page[page.length - 1]!.id;
      }
      return mergeIncoming(convId, incoming);
    } catch (err) {
      // 404 = the anchor message is gone (the thread itself is checked by
      // the reload below, which 404s too if the conversation was deleted).
      if (!(err instanceof ApiError && err.status === 404)) throw err;
    }
  }
  const limit = Math.min(Math.max(MESSAGE_PAGE, conv.messages.length), MAX_MESSAGE_LIMIT);
  const detail = await api.getConversation(convId, { limit, signal });
  return replaceThread(convId, detail);
}

function handleConversationGone(id: string) {
  conversations.value = conversations.value.filter((c) => c.id !== id);
  delete localMessages[id];
  if (selected.value?.id === id) selected.value = null;
  if (requestedId === id) requestedId = null;
  if (route.query.id === id) {
    isMobileThreadActive.value = false;
    const nextQuery = { ...route.query };
    delete nextQuery.id;
    router.replace({ query: nextQuery });
  }
  toast.error(t("conversations.notFound"));
}

// --- Operator replies --------------------------------------------------------

const replyText = ref("");
// Per-conversation drafts, so switching chats never sends a half-typed
// reply to the wrong customer.
const drafts: Record<string, string> = {};

function setLocal(convId: string, localId: string, patch: Partial<Message>) {
  const msg = localMessages[convId]?.find((m) => m.id === localId);
  if (msg) Object.assign(msg, patch);
}

function removeLocal(convId: string, localId: string) {
  const list = localMessages[convId];
  if (!list) return;
  localMessages[convId] = list.filter((m) => m.id !== localId);
}

function addLocal(convId: string, content: string): Message {
  const msg: Message = {
    id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    sender_type: "human",
    content,
    message_type: "text",
    attachment_url: null,
    attachment_type: null,
    delivery_status: "pending",
    delivery_error: null,
    created_at: new Date().toISOString(),
    local: true,
  };
  localMessages[convId] = [...(localMessages[convId] ?? []), msg];
  return msg;
}

// The server hands a conversation to the human when the operator replies
// (ai_active / human_needed -> human_active) — mirror it in the UI.
function applyOperatorTakeover(convId: string) {
  const apply = (c: ConversationSummary) => {
    if (c.status === "ai_active" || c.status === "human_needed") c.status = "human_active";
  };
  if (selected.value?.id === convId) apply(selected.value);
  const item = conversations.value.find((c) => c.id === convId);
  if (item) apply(item);
}

function bumpConversation(convId: string, at: string) {
  const idx = conversations.value.findIndex((c) => c.id === convId);
  if (idx === -1) return;
  const [item] = conversations.value.splice(idx, 1);
  item!.last_message_at = at;
  conversations.value.unshift(item!);
}

async function deliver(convId: string, localId: string, content: string) {
  setLocal(convId, localId, { delivery_status: "pending", delivery_error: null });
  // Server messages this thread already had — to recognise the one a failed
  // delivery leaves behind.
  const knownIds = new Set(selected.value?.id === convId ? selected.value.messages.map((m) => m.id) : []);
  try {
    const msg = await api.sendConversationReply(convId, content);
    removeLocal(convId, localId);
    mergeIncoming(convId, [msg]);
    applyOperatorTakeover(convId);
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 400)) {
      setLocal(convId, localId, { delivery_status: "failed", delivery_error: errorText(err) });
      toast.error(errorText(err));
      return;
    }
    // 400 = Instagram refused the delivery. The server kept the message,
    // marked failed — unless it was rejected before being stored (no
    // Instagram account connected). Check which, so the text is never lost
    // and never shown twice.
    toast.error(err.message);
    let stored = false;
    try {
      const recent = await api.getConversation(convId, { limit: 20 });
      stored = recent.messages.some(
        (m) => !knownIds.has(m.id) && m.sender_type === "human" && m.content === content && m.delivery_status === "failed"
      );
    } catch (checkErr) {
      console.error("Failed to re-check the thread after a failed reply", checkErr);
    }
    if (stored) {
      removeLocal(convId, localId);
      applyOperatorTakeover(convId);
      if (selected.value?.id === convId) await syncNow({ force: true });
    } else {
      setLocal(convId, localId, { delivery_status: "failed", delivery_error: err.message });
    }
  }
}

async function handleSendReply() {
  const conv = selected.value;
  const content = replyText.value.trim();
  if (!conv || !content) return;
  if (content.length > REPLY_MAX_LENGTH) {
    toast.error(t("conversations.replyTooLong", { max: REPLY_MAX_LENGTH }));
    return;
  }

  // Clear input immediately so operator can type next message without waiting
  replyText.value = "";
  drafts[conv.id] = "";

  const local = addLocal(conv.id, content);
  scrollToBottom(true);
  bumpConversation(conv.id, local.created_at);
  await deliver(conv.id, local.id, content);
}

function retrySendMessage(msg: Message) {
  const conv = selected.value;
  if (!conv || msg.sender_type !== "human" || msg.delivery_status !== "failed") return;
  if (msg.local) {
    deliver(conv.id, msg.id, msg.content);
  } else {
    // Stored server-side as failed: send its text again as a new reply.
    const local = addLocal(conv.id, msg.content);
    scrollToBottom(true);
    deliver(conv.id, local.id, msg.content);
  }
}

function handleReplyKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSendReply();
  }
}

// --- Scrolling ---------------------------------------------------------------

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

// --- Opening a conversation --------------------------------------------------

// Every open gets a sequence number and its own AbortController: a slow
// response for a chat the user already left is cancelled, and if it still
// lands it is dropped instead of overwriting the chat now on screen.
let openSeq = 0;
let openAbort: AbortController | null = null;
let requestedId: string | null = null;

async function openConversation(id: string, updateQuery = true) {
  if (updateQuery && route.query.id !== id) {
    router.replace({ query: { ...route.query, id } });
  }
  isMobileThreadActive.value = true;
  if (requestedId === id && (threadLoading.value || selected.value?.id === id)) return;

  if (selected.value) drafts[selected.value.id] = replyText.value;
  replyText.value = drafts[id] ?? "";

  const seq = ++openSeq;
  requestedId = id;
  openAbort?.abort();
  pollAbort?.abort();
  const ctrl = new AbortController();
  openAbort = ctrl;
  selected.value = null;
  threadLoading.value = true;

  try {
    const detail = await api.getConversation(id, { limit: MESSAGE_PAGE, signal: ctrl.signal });
    if (seq !== openSeq) return;
    selected.value = detail;
    displayedCount.value = PAGE_SIZE;
    shouldStickToBottom.value = true;
    showScrollDownBtn.value = false;
    scrollToBottom(false);
    nextTick(() => {
      attachObservers();
    });
  } catch (err) {
    if (seq !== openSeq || isAbortError(err)) return;
    requestedId = null;
    if (err instanceof ApiError && err.status === 404) {
      handleConversationGone(id);
    } else {
      console.error("Failed to load conversation", err);
      toast.error(errorText(err));
    }
  } finally {
    if (seq === openSeq) {
      threadLoading.value = false;
      openAbort = null;
    }
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
  const conv = selected.value;
  if (!conv) return;
  const newStatus = isAiStatus(conv.status) ? "human_needed" : "ai_active";
  try {
    const updated = await api.updateConversationStatus(conv.id, newStatus);
    if (selected.value?.id === conv.id) selected.value.status = updated.status;
    const item = conversations.value.find((c) => c.id === conv.id);
    if (item) item.status = updated.status;
    toast.success(newStatus === "ai_active" ? t("conversations.switchedToAi") : t("conversations.switchedToOperator"));
  } catch (err) {
    console.error("Failed to update status", err);
    toast.error(errorText(err));
  }
}

// --- Background sync ---------------------------------------------------------

// DMs arrive from real customers at any moment: a manual refresh button, the
// SSE "conversation_updated" event, and a quiet 12s poll. At most one sync
// runs at a time (a trigger during one queues exactly one follow-up), and the
// poll skips hidden tabs.
const refreshing = ref(false);
const POLL_INTERVAL_MS = 12000;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let syncInFlight = false;
let syncQueued = false;
let pollAbort: AbortController | null = null;

async function refreshList() {
  const limit = Math.min(Math.max(CONVERSATION_PAGE, conversations.value.length), MAX_CONVERSATION_PAGE);
  const fresh = await api.listConversations(limit, 0);
  const freshIds = new Set(fresh.map((c) => c.id));
  const tail = conversations.value.slice(limit).filter((c) => !freshIds.has(c.id));
  conversations.value = [...fresh, ...tail];
  if (tail.length === 0) hasMoreConversations.value = fresh.length === limit;
  // Keep the open thread's header in step (e.g. the AI handed over).
  const conv = selected.value;
  const summary = conv ? fresh.find((c) => c.id === conv.id) : undefined;
  if (conv && summary) conv.status = summary.status;
}

async function syncNow(opts: { force?: boolean; userInitiated?: boolean } = {}) {
  if (syncInFlight) {
    syncQueued = true;
    return;
  }
  if (!opts.force && document.hidden) return;
  syncInFlight = true;
  const ctrl = new AbortController();
  pollAbort = ctrl;
  const convId = selected.value?.id ?? null;
  try {
    await refreshList();
    if (convId && selected.value?.id === convId) {
      const added = await syncThread(convId, ctrl.signal);
      if (added.length > 0) {
        // Only move the view on an actual new message, and not while the
        // owner is reading back through older ones.
        if (shouldStickToBottom.value) scrollToBottom();
        else showScrollDownBtn.value = true;
      }
    }
    syncError.value = false;
  } catch (err) {
    if (isAbortError(err)) return;
    if (convId && err instanceof ApiError && err.status === 404) {
      handleConversationGone(convId);
      return;
    }
    console.error("Failed to refresh conversations", err);
    syncError.value = true;
    if (opts.userInitiated) toast.error(errorText(err));
  } finally {
    syncInFlight = false;
    if (pollAbort === ctrl) pollAbort = null;
    if (syncQueued) {
      syncQueued = false;
      syncNow();
    }
  }
}

async function manualRefresh() {
  refreshing.value = true;
  try {
    await syncNow({ force: true, userInitiated: true });
  } finally {
    refreshing.value = false;
  }
}

async function loadMoreConversations() {
  if (loadingMoreConversations.value || !hasMoreConversations.value) return;
  loadingMoreConversations.value = true;
  try {
    const page = await api.listConversations(CONVERSATION_PAGE, conversations.value.length);
    const known = new Set(conversations.value.map((c) => c.id));
    conversations.value = [...conversations.value, ...page.filter((c) => !known.has(c.id))];
    hasMoreConversations.value = page.length === CONVERSATION_PAGE;
  } catch (err) {
    toast.error(errorText(err));
  } finally {
    loadingMoreConversations.value = false;
  }
}

function openDeleteConfirmModal() {
  isDeleteConfirmModalOpen.value = true;
}

async function confirmDeleteConversation() {
  const conv = selected.value;
  if (!conv) return;
  deletingConversation.value = true;
  try {
    await api.deleteConversation(conv.id);
    conversations.value = conversations.value.filter((c) => c.id !== conv.id);
    delete localMessages[conv.id];
    delete drafts[conv.id];
    if (selected.value?.id === conv.id) selected.value = null;
    requestedId = null;
    replyText.value = "";
    isDeleteConfirmModalOpen.value = false;
    isMobileThreadActive.value = false;
    toast.success(t("conversations.deleted"));
    router.replace({ query: {} });
  } catch (err) {
    console.error("Failed to delete conversation", err);
    toast.error(errorText(err));
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
    toast.success(t("conversations.feedbackThanks"));
  } catch (err) {
    console.error("Failed to submit feedback", err);
    toast.error(errorText(err));
  }
}

function openCorrectionModal(msg: Message) {
  feedbackTargetMessage.value = msg;
  const msgs = threadMessages.value;
  const msgIndex = msgs.findIndex((m) => m.id === msg.id);
  const previous = msgIndex > 0 ? msgs[msgIndex - 1] : undefined;
  feedbackCustomerQuery.value = previous && previous.sender_type === "customer" ? previous.content : "";
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
    toast.success(t("conversations.correctionLearned"));
  } catch (err) {
    console.error("Failed to submit correction", err);
    toast.error(errorText(err));
  } finally {
    feedbackSubmitting.value = false;
  }
}

async function loadInitial() {
  loading.value = true;
  listError.value = null;
  try {
    const page = await api.listConversations(CONVERSATION_PAGE, 0);
    conversations.value = page;
    hasMoreConversations.value = page.length === CONVERSATION_PAGE;
  } catch (err) {
    console.error("Failed to load conversations", err);
    listError.value = errorText(err);
    return;
  } finally {
    loading.value = false;
  }
  const queryId = route.query.id as string | undefined;
  if (queryId) {
    // Links from notifications may point past the first page — open it anyway.
    await openConversation(queryId, false);
  } else if (conversations.value.length > 0 && window.innerWidth > 768) {
    await openConversation(conversations.value[0]!.id, false);
  }
}

function onVisibilityChange() {
  if (!document.hidden) syncNow();
}

let stopConversationEvents: (() => void) | null = null;

onMounted(async () => {
  stopConversationEvents = onConversationUpdated(() => {
    syncNow();
  });
  document.addEventListener("visibilitychange", onVisibilityChange);
  pollTimer = setInterval(() => syncNow(), POLL_INTERVAL_MS);
  await loadInitial();
});

onUnmounted(() => {
  isMobileThreadActive.value = false;
  if (pollTimer) clearInterval(pollTimer);
  openAbort?.abort();
  pollAbort?.abort();
  stopConversationEvents?.();
  document.removeEventListener("visibilitychange", onVisibilityChange);
  if (messagesResizeObserver) messagesResizeObserver.disconnect();
  if (messagesMutationObserver) messagesMutationObserver.disconnect();
});

watch(
  () => route.query.id,
  async (newId) => {
    if (newId && typeof newId === "string" && requestedId !== newId) {
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
</script>

<template>
  <div class="conversations-page" :class="{ 'mobile-thread-active': isMobileThreadActive }">
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

    <!-- Initial load failed -->
    <div v-else-if="listError" class="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <AlertCircle :size="28" class="text-destructive" />
      <p class="text-sm text-foreground">{{ t("conversations.loadError") }}</p>
      <p class="text-xs text-muted-foreground">{{ listError }}</p>
      <Button variant="outline" size="sm" class="gap-2" @click="loadInitial">
        <RefreshCw :size="14" />
        <span>{{ t("common.retry") }}</span>
      </Button>
    </div>

    <!-- Split View Messenger Container -->
    <div v-else class="split-view" :class="{ 'mobile-thread-open': isMobileThreadActive }">
      <!-- Conversation List Sidebar -->
      <div class="conversation-sidebar" :class="{ 'mobile-hidden': isMobileThreadActive }">
        <div class="px-4 py-3.5 border-b border-border text-xs font-bold text-muted-foreground uppercase tracking-wide shrink-0">
          {{ t("conversations.activeChats") }} ({{ conversations.length }}{{ hasMoreConversations ? "+" : "" }})
        </div>

        <div v-if="syncError" class="px-4 py-2 text-[11px] text-destructive bg-destructive/5 border-b border-destructive/20 flex items-center gap-1.5 shrink-0">
          <AlertCircle :size="12" class="shrink-0" />
          <span>{{ t("conversations.syncError") }}</span>
        </div>

        <ul v-if="conversations.length > 0" class="flex-1 min-h-0 overflow-y-auto overscroll-contain m-0 p-0 list-none custom-scrollbar">
          <li
            v-for="c in conversations"
            :key="c.id"
            class="conversation-item"
            :class="{ active: (selected?.id ?? requestedId) === c.id }"
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
                  :class="isAiStatus(c.status) ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'"
                  :title="isAiStatus(c.status) ? t('conversations.modeAi') : t('conversations.modeOperator')"
                >
                  <Bot v-if="isAiStatus(c.status)" :size="12" />
                  <User v-else :size="12" />
                </span>
                <span class="text-xs text-muted-foreground">Instagram</span>
              </div>
            </div>
          </li>
          <li v-if="hasMoreConversations" class="p-3 flex justify-center">
            <Button
              variant="outline"
              size="sm"
              class="h-8 text-xs gap-1.5"
              :disabled="loadingMoreConversations"
              @click="loadMoreConversations"
            >
              <Loader2 v-if="loadingMoreConversations" :size="12" class="animate-spin" />
              <span>{{ t("common.loadMore") }}</span>
            </Button>
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
                :class="isAiStatus(selected.status)
                  ? 'border-primary/40 bg-primary/10 text-primary hover:bg-primary/15'
                  : 'border-border/80 bg-card text-muted-foreground hover:bg-muted hover:text-foreground'"
                :title="isAiStatus(selected.status)
                  ? t('conversations.aiModeActiveTooltip')
                  : t('conversations.operatorModeActiveTooltip')"
                @click="toggleConversationStatus"
              >
                <Bot v-if="isAiStatus(selected.status)" :size="16" class="shrink-0" />
                <User v-else :size="16" class="shrink-0" />
              </button>

              <!-- Manual Refresh -->
              <button
                type="button"
                class="inline-flex items-center justify-center h-8 w-8 sm:h-9 sm:w-9 rounded-xl border border-border/80 bg-card hover:bg-muted text-foreground transition-all shadow-2xs active:scale-95 disabled:opacity-50 cursor-pointer"
                :title="t('common.refresh')"
                :disabled="refreshing"
                @click="manualRefresh"
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
                <span>{{ hiddenLoadedCount > 0 ? t("conversations.loadOlder", { count: hiddenLoadedCount }) : t("conversations.loadOlderServer") }}</span>
              </button>
            </div>

            <!-- Message Bubbles -->
            <div
              v-for="m in visibleMessages"
              :key="m.id"
              class="bubble max-w-[85%] sm:max-w-[80%] break-words relative transition-all duration-150"
              :class="[
                m.sender_type === 'customer' ? 'bubble-customer' : m.sender_type === 'human' ? 'bubble-human' : 'bubble-ai',
                m.delivery_status === 'pending' ? 'opacity-70' : '',
                m.delivery_status === 'failed' ? 'border-destructive/60 bg-destructive/5' : ''
              ]"
            >
              <!-- Bubble Header -->
              <div class="flex items-center justify-between text-xs mb-1.5 gap-2 opacity-75">
                <div class="flex items-center gap-1.5">
                  <User v-if="m.sender_type === 'customer'" :size="12" />
                  <UserCheck v-else-if="m.sender_type === 'human'" :size="12" class="text-blue-500" />
                  <Bot v-else :size="12" class="text-primary" />
                  <span class="font-semibold text-xs text-foreground/90">
                    {{ m.sender_type === 'customer' ? cleanCustomerName(selected.customer_username) : m.sender_type === 'human' ? t('conversations.youOperator') : t('conversations.aiAssistant') }}
                  </span>
                </div>

                <!-- Status & Time Badge -->
                <div class="flex items-center gap-1 text-[11px]">
                  <span v-if="m.delivery_status === 'pending'" class="text-muted-foreground flex items-center gap-1">
                    <Clock :size="11" class="animate-pulse" />
                    <span class="hidden sm:inline">{{ t("conversations.sending") }}</span>
                  </span>
                  <button
                    v-else-if="m.delivery_status === 'failed' && m.sender_type === 'human'"
                    type="button"
                    class="text-destructive flex items-center gap-1 cursor-pointer font-medium hover:underline"
                    :title="t('conversations.retryTitle')"
                    @click="retrySendMessage(m)"
                  >
                    <AlertCircle :size="11" />
                    <span>{{ t("conversations.retry") }}</span>
                  </button>
                  <span v-else-if="m.delivery_status === 'failed'" class="text-destructive flex items-center gap-1 font-medium">
                    <AlertCircle :size="11" />
                    <span>{{ t("conversations.notDelivered") }}</span>
                  </span>
                  <span v-else class="opacity-70">{{ formatTime(m.created_at) }}</span>
                </div>
              </div>

              <!-- Message Content & Media Rendering -->
              <div class="text-sm leading-relaxed">
                <template v-if="mediaOf(m)">
                  <!-- Image Attachment -->
                  <div
                    v-if="mediaOf(m)!.kind === 'image'"
                    class="mt-1.5 mb-1 rounded-xl overflow-hidden border border-border/50 max-w-[280px] sm:max-w-[340px] bg-black/5 dark:bg-white/5 cursor-pointer hover:opacity-95 transition-opacity shadow-2xs group relative"
                    @click="openMediaPreview(mediaOf(m)!.url, 'image')"
                  >
                    <img
                      :src="mediaOf(m)!.url"
                      :alt="t('conversations.imageAlt')"
                      class="w-full h-auto max-h-[340px] object-cover rounded-xl group-hover:scale-[1.01] transition-transform duration-200"
                      loading="lazy"
                      referrerpolicy="no-referrer"
                    />
                    <div class="absolute inset-0 bg-black/20 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center text-white text-xs font-medium">
                      {{ t("common.zoom") }}
                    </div>
                  </div>

                  <!-- Video Attachment -->
                  <div
                    v-else-if="mediaOf(m)!.kind === 'video'"
                    class="mt-1.5 mb-1 rounded-xl overflow-hidden border border-border/50 max-w-[280px] sm:max-w-[340px] bg-black shadow-2xs"
                  >
                    <video :src="mediaOf(m)!.url" controls preload="metadata" class="w-full max-h-[280px] rounded-xl"></video>
                  </div>

                  <!-- Voice note -->
                  <audio
                    v-else-if="mediaOf(m)!.kind === 'audio'"
                    :src="mediaOf(m)!.url"
                    controls
                    preload="none"
                    class="mt-1.5 mb-1 w-full max-w-[280px]"
                  ></audio>

                  <!-- Shared post / other attachment: an explicit, https-only link -->
                  <div
                    v-else
                    class="mt-1.5 mb-1 p-3 rounded-xl border border-pink-500/20 bg-gradient-to-r from-pink-500/10 via-purple-500/10 to-indigo-500/10 flex items-center justify-between gap-3 shadow-2xs"
                  >
                    <div class="flex items-center gap-2.5 min-w-0">
                      <div class="h-9 w-9 rounded-lg bg-pink-500/20 text-pink-500 flex items-center justify-center shrink-0">
                        <Film :size="18" />
                      </div>
                      <div class="min-w-0">
                        <div class="text-xs font-semibold text-foreground truncate">
                          {{ isSharedPost(m) ? t("conversations.reelShared") : t("conversations.attachment") }}
                        </div>
                      </div>
                    </div>
                    <a
                      :href="mediaOf(m)!.url"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="text-xs text-primary hover:underline flex items-center gap-1 shrink-0 px-2.5 py-1 rounded-lg bg-card border border-border"
                    >
                      <span>{{ t("common.open") }}</span>
                      <ExternalLink :size="11" />
                    </a>
                  </div>
                </template>

                <!-- Text is always plain text, exactly as sent -->
                <div v-if="m.content" class="whitespace-pre-wrap">{{ m.content }}</div>

                <p v-if="m.delivery_status === 'failed'" class="mt-1.5 text-[11px] text-destructive">
                  {{ m.delivery_error || t("conversations.notDelivered") }}
                </p>
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
                maxlength="1000"
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

        <div v-else-if="threadLoading" class="flex h-full items-center justify-center p-8 text-muted-foreground">
          <Loader2 :size="22" class="animate-spin" />
        </div>

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
              maxlength="1000"
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
        <p class="text-sm text-foreground">
          {{ t("conversations.deleteModalConfirm", { name: selected?.customer_username || t("conversations.customer") }) }}
        </p>
        <p class="text-xs text-muted-foreground mb-5">{{ t("conversations.deleteKeepsLead") }}</p>
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
            :alt="t('conversations.imageAlt')"
            referrerpolicy="no-referrer"
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
