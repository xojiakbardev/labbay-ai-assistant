<script setup lang="ts">
import { toast } from "vue-sonner";
import {
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuRoot,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "reka-ui";
import { ApiError } from "~/composables/useApi";
import { daysAgo, formatClock, formatDayMonth, formatShortDate } from "~/composables/useDateFormat";
import { safeHttpsUrl } from "~/lib/utils";
import type { ConversationDetail, ConversationStatus, ConversationSummary, Message } from "~/types/api";
import {
  Bot,
  ArrowLeft,
  ThumbsUp,
  ThumbsDown,
  CheckCircle2,
  Check,
  Sparkles,
  Trash2,
  RefreshCw,
  Send,
  Loader2,
  Clock,
  AlertCircle,
  Film,
  ExternalLink,
  ChevronUp,
  ArrowDown,
  MoreVertical,
  MessageSquare,
  Info,
  Plug,
} from "@lucide/vue";

definePageMeta({ layout: "dashboard" });

const api = useMivoApi();
const { t, locale } = useI18n();
const route = useRoute();
const router = useRouter();
const { onConversationUpdated } = useNotifications();

const CONVERSATION_PAGE = 50;
const MAX_CONVERSATION_PAGE = 200;
const MESSAGE_PAGE = 100;
const MAX_MESSAGE_LIMIT = 500;
const REPLY_MAX_LENGTH = 1000;
// The character counter appears once a reply gets this close to the limit.
const REPLY_COUNTER_FROM = 850;
// Consecutive messages from one sender closer together than this form one group.
const GROUP_GAP_MS = 5 * 60 * 1000;

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
// Opening a thread failed for a reason other than "it's gone".
const threadError = ref<string | null>(null);

// Replies the server hasn't acknowledged, per conversation — pending, or
// failed before reaching it. Kept apart from server messages so no merge or
// reload can ever wipe one (and its text) out, and they survive switching.
const localMessages = reactive<Record<string, Message[]>>({});

const threadMessages = computed<Message[]>(() => {
  if (!selected.value) return [];
  return [...selected.value.messages, ...(localMessages[selected.value.id] ?? [])];
});

// Mobile: list and thread are separate screens (the layout hides its own
// header and tab bar while a thread is open).
const isMobileThreadActive = useState<boolean>("isMobileThreadActive", () => false);

function isMobileViewport(): boolean {
  return window.matchMedia("(max-width: 900px)").matches;
}

function isCoarsePointer(): boolean {
  return window.matchMedia("(pointer: coarse)").matches;
}

// Context the page needs besides conversations: whether replies can reach
// Instagram at all, and whether the AI is switched off for the whole business.
// null = unknown (not loaded, or the request failed) — then nothing is claimed.
const igConnected = ref<boolean | null>(null);
const aiGloballyOff = ref<boolean | null>(null);

async function loadContext() {
  const [ig, biz] = await Promise.allSettled([api.getInstagramStatus(), api.getBusiness()]);
  if (ig.status === "fulfilled") igConnected.value = ig.value.connected;
  else console.error("Failed to load Instagram status", ig.reason);
  if (biz.status === "fulfilled") aiGloballyOff.value = !biz.value.ai_enabled || biz.value.ai_suspended;
  else console.error("Failed to load business settings", biz.reason);
}

// AI Feedback Modal state
const isFeedbackModalOpen = ref(false);
const feedbackTargetMessage = ref<Message | null>(null);
const feedbackCustomerQuery = ref("");
const feedbackCorrectionText = ref("");
const feedbackSubmitting = ref(false);
// Messages rated in this session, so a rating shows as given.
const ratedMessages = reactive<Record<string, "up" | "down">>({});

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

function isReaction(m: Pick<Message, "message_type">) {
  return m.message_type === "reaction";
}

// --- Scrolling ---------------------------------------------------------------

const chat = useChatScroll();
const messagesContainerRef = chat.container;
const messagesContentRef = chat.content;
// New messages that arrived while the owner was reading further up.
const unseenCount = ref(0);

watch(chat.pinned, (pinned) => {
  if (pinned) unseenCount.value = 0;
});

// Message window: everything from `firstVisibleId` to the newest message is
// rendered. Anchoring the window at its oldest message (not "the last N")
// means a new message extends it instead of pushing one out of the top —
// which would shift what the owner is reading.
const PAGE_SIZE = 35;
const firstVisibleId = ref<string | null>(null);
const isLoadingOlder = ref(false);

const firstVisibleIndex = computed(() => {
  const msgs = threadMessages.value;
  const idx = firstVisibleId.value ? msgs.findIndex((m) => m.id === firstVisibleId.value) : -1;
  return idx === -1 ? Math.max(0, msgs.length - PAGE_SIZE) : idx;
});

const visibleMessages = computed(() => threadMessages.value.slice(firstVisibleIndex.value));
const hiddenLoadedCount = computed(() => firstVisibleIndex.value);

function resetWindow() {
  const msgs = threadMessages.value;
  firstVisibleId.value = msgs[Math.max(0, msgs.length - PAGE_SIZE)]?.id ?? null;
}

// Older messages exist either in the local window or on the server (the
// detail endpoint returns only the most recent ones).
const hasOlderMessages = computed(() => {
  if (!selected.value) return false;
  if (hiddenLoadedCount.value > 0) return true;
  return selected.value.has_more_messages && selected.value.messages.length < MAX_MESSAGE_LIMIT;
});

async function loadOlderMessages() {
  const el = messagesContainerRef.value;
  const conv = selected.value;
  if (!el || !conv || !hasOlderMessages.value || isLoadingOlder.value) return;
  isLoadingOlder.value = true;
  chat.unpin();
  try {
    if (hiddenLoadedCount.value === 0) {
      // Everything loaded is already shown — fetch a longer tail from the server.
      const limit = Math.min(conv.messages.length + MESSAGE_PAGE, MAX_MESSAGE_LIMIT);
      const detail = await api.getConversation(conv.id, { limit });
      if (selected.value?.id !== conv.id) return;
      replaceThread(conv.id, detail);
    }
    // Measured right before the DOM grows, so the reader's position is kept
    // exactly: the new height goes above what they were looking at.
    const prevHeight = el.scrollHeight;
    const prevTop = el.scrollTop;
    const msgs = threadMessages.value;
    const nextIndex = Math.max(0, firstVisibleIndex.value - PAGE_SIZE);
    firstVisibleId.value = msgs[nextIndex]?.id ?? null;
    await nextTick();
    el.scrollTop = el.scrollHeight - prevHeight + prevTop;
  } catch (err) {
    toast.error(errorText(err));
  } finally {
    isLoadingOlder.value = false;
    chat.onScroll();
  }
}

let lastScrollTop = 0;
function handleMessagesScroll() {
  chat.onScroll();
  const el = messagesContainerRef.value;
  if (!el) return;
  // Older messages load when the reader scrolls *up* to near the top — never
  // from a programmatic jump, and never twice at once (same guard as the button).
  const goingUp = el.scrollTop < lastScrollTop;
  lastScrollTop = el.scrollTop;
  if (goingUp && el.scrollTop <= 120 && hasOlderMessages.value && !isLoadingOlder.value) {
    loadOlderMessages();
  }
}

function jumpToLatest() {
  chat.scrollToBottom(true);
}

// --- Media -------------------------------------------------------------------

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

const REEL_TYPES = ["ig_reel", "reel"];
const POST_TYPES = ["share", "ig_post"];
const STORY_TYPES = ["story_mention", "story"];

function isSharedPost(m: Message) {
  return [...REEL_TYPES, ...POST_TYPES, ...STORY_TYPES].includes((m.attachment_type || "").toLowerCase());
}

// Media messages carry no text of their own; one Instagram gives no viewable
// URL for (a template, say) is shown as this chip instead.
function hasUnviewableMedia(m: Message) {
  return !!m.attachment_type && !mediaOf(m) && !m.content;
}

function mediaLabel(type: string | null | undefined): string {
  const kind = (type || "").toLowerCase();
  if (kind === "image") return t("conversations.media.image");
  if (kind === "video") return t("conversations.media.video");
  if (kind === "audio") return t("conversations.media.audio");
  if (REEL_TYPES.includes(kind)) return t("conversations.media.reel");
  if (POST_TYPES.includes(kind)) return t("conversations.media.post");
  if (STORY_TYPES.includes(kind)) return t("conversations.media.story");
  return t("conversations.media.file");
}

// --- List helpers ------------------------------------------------------------

// Re-evaluates relative times ("now", "14:05", "yesterday") once a minute.
const now = ref(Date.now());
let clockTimer: ReturnType<typeof setInterval> | null = null;

function previewText(c: ConversationSummary): string {
  const lm = c.last_message;
  if (!lm) return "";
  let body: string;
  if (isReaction(lm)) {
    body = t("conversations.media.reaction", { emoji: lm.content });
  } else {
    const text = lm.content.trim();
    // An untranscribed voice note still carries its bracketed placeholder.
    const placeholder = lm.attachment_type === "audio" && /^\[.*\]$/.test(text);
    const kind = (lm.attachment_type || "").toLowerCase();
    if (!lm.attachment_type) body = text;
    else if (!text || placeholder) body = mediaLabel(kind);
    else if (kind === "image" || kind === "video") body = text;
    else body = `${mediaLabel(kind)}: ${text}`;
  }
  if (lm.sender_type === "human") return `${t("conversations.you")}: ${body}`;
  if (lm.sender_type === "ai") return `${t("conversations.modeAi")}: ${body}`;
  return body;
}

function listTime(c: ConversationSummary): string {
  const iso = c.last_message?.created_at ?? c.last_message_at;
  if (!iso) return "";
  const nowDate = new Date(now.value);
  if (nowDate.getTime() - new Date(iso).getTime() < 60_000) return t("conversations.now");
  const days = daysAgo(iso, nowDate);
  if (days <= 0) return formatClock(iso);
  if (days === 1) return t("conversations.yesterday");
  return formatShortDate(iso, locale.value, nowDate);
}

// The owner owes this customer an answer: operator mode and the customer
// spoke last.
function awaitingReply(c: ConversationSummary): boolean {
  return !isAiStatus(c.status) && c.status !== "closed" && c.last_message?.sender_type === "customer";
}

function cleanCustomerName(username?: string | null) {
  if (!username) return t("conversations.customer");
  const str = username.trim();
  const lower = str.toLowerCase();
  // Instagram's placeholder handles and the server's own "unknown user" label.
  if (lower.startsWith("@user_") || lower.startsWith("user_") || str === "Instagram foydalanuvchisi") {
    return t("conversations.customer");
  }
  return str;
}

function getAvatarLetter(username?: string | null) {
  return cleanCustomerName(username).replace("@", "").trim().charAt(0).toUpperCase() || "M";
}

const threadSubtitle = computed(() => {
  const c = selected.value;
  if (!c) return "";
  const parts: string[] = [];
  const name = c.customer_name?.trim();
  if (name && name.toLowerCase() !== (c.customer_username || "").replace("@", "").toLowerCase()) parts.push(name);
  if (c.customer_phone) parts.push(c.customer_phone);
  return parts.length ? parts.join(" · ") : "Instagram";
});

// --- Thread rows: day dividers, sender groups, reactions ----------------------

interface BubbleItem {
  msg: Message;
  // AI reactions left on this (customer) message instead of a reply.
  reactions: Message[];
}
interface GroupRow {
  kind: "group";
  key: string;
  sender: Message["sender_type"];
  items: BubbleItem[];
}
interface DayRow {
  kind: "day";
  key: string;
  label: string;
}
interface ReactionRow {
  kind: "reaction";
  key: string;
  msg: Message;
}
type ThreadRow = GroupRow | DayRow | ReactionRow;

function dayLabel(iso: string): string {
  const days = daysAgo(iso, new Date(now.value));
  if (days === 0) return t("conversations.today");
  if (days === 1) return t("conversations.yesterday");
  return formatDayMonth(iso, locale.value, new Date(now.value));
}

const threadRows = computed<ThreadRow[]>(() => {
  const rows: ThreadRow[] = [];
  let group: GroupRow | null = null;
  let lastDay = "";
  let prev: Message | null = null;
  let lastCustomerBubble: BubbleItem | null = null;
  for (const m of visibleMessages.value) {
    if (isReaction(m)) {
      // A reaction answers the customer's previous message — it's drawn on it.
      if (lastCustomerBubble) lastCustomerBubble.reactions.push(m);
      else rows.push({ kind: "reaction", key: m.id, msg: m });
      continue;
    }
    const d = new Date(m.created_at);
    const day = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    if (day !== lastDay) {
      rows.push({ kind: "day", key: `day-${m.id}`, label: dayLabel(m.created_at) });
      lastDay = day;
      group = null;
    }
    const gap = prev ? d.getTime() - new Date(prev.created_at).getTime() > GROUP_GAP_MS : true;
    if (!group || group.sender !== m.sender_type || gap) {
      group = { kind: "group", key: `group-${m.id}`, sender: m.sender_type, items: [] };
      rows.push(group);
    }
    const item: BubbleItem = { msg: m, reactions: [] };
    group.items.push(item);
    if (m.sender_type === "customer") lastCustomerBubble = item;
    prev = m;
  }
  return rows;
});

function groupSenderLabel(sender: Message["sender_type"]): string {
  if (sender === "human") return t("conversations.you");
  if (sender === "system") return t("conversations.system");
  return t("conversations.aiAssistant");
}

function reactionLabel(r: Message): string {
  if (r.delivery_status === "failed") return `${t("conversations.aiReacted", { emoji: r.content })} — ${t("conversations.notDelivered")}`;
  if (r.delivery_status === "pending") return `${t("conversations.aiReacted", { emoji: r.content })} — ${t("conversations.sending")}`;
  return t("conversations.aiReacted", { emoji: r.content });
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
    pushedThreadEntry = false;
    const nextQuery = { ...route.query };
    delete nextQuery.id;
    router.replace({ query: nextQuery });
  }
  toast.error(t("conversations.notFound"));
}

// --- Operator replies --------------------------------------------------------

const replyText = ref("");
const replyInputRef = ref<HTMLTextAreaElement | null>(null);
// Per-conversation drafts, so switching chats never sends a half-typed
// reply to the wrong customer.
const drafts: Record<string, string> = {};

// Replies can't reach the customer without a connected Instagram account.
const canReply = computed(() => igConnected.value !== false);

// Grows with the text up to five lines, then scrolls inside.
function autoGrowComposer() {
  const el = replyInputRef.value;
  if (!el) return;
  const style = window.getComputedStyle(el);
  const lineHeight = parseFloat(style.lineHeight) || 20;
  const max = lineHeight * 5 + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, max)}px`;
  el.style.overflowY = el.scrollHeight > max ? "auto" : "hidden";
}

watch(replyText, () => nextTick(autoGrowComposer));

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

function bumpConversation(convId: string, msg: Message) {
  const idx = conversations.value.findIndex((c) => c.id === convId);
  if (idx === -1) return;
  const [item] = conversations.value.splice(idx, 1);
  item!.last_message_at = msg.created_at;
  item!.last_message = {
    content: msg.content.slice(0, 120),
    sender_type: msg.sender_type,
    message_type: msg.message_type,
    attachment_type: msg.attachment_type,
    created_at: msg.created_at,
  };
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
  if (!conv || !content || !canReply.value) return;
  if (content.length > REPLY_MAX_LENGTH) {
    toast.error(t("conversations.replyTooLong", { max: REPLY_MAX_LENGTH }));
    return;
  }

  // Clear the input at once so the next message can be typed while this
  // one is on its way; focus stays in the composer.
  replyText.value = "";
  drafts[conv.id] = "";
  replyInputRef.value?.focus();

  const local = addLocal(conv.id, content);
  await nextTick();
  chat.scrollToBottom();
  bumpConversation(conv.id, local);
  await deliver(conv.id, local.id, content);
}

async function retrySendMessage(msg: Message) {
  const conv = selected.value;
  if (!conv || msg.sender_type !== "human" || msg.delivery_status !== "failed") return;
  if (msg.local) {
    deliver(conv.id, msg.id, msg.content);
  } else {
    // Stored server-side as failed: send its text again as a new reply.
    const local = addLocal(conv.id, msg.content);
    await nextTick();
    chat.scrollToBottom();
    deliver(conv.id, local.id, msg.content);
  }
}

function handleReplyKeydown(e: KeyboardEvent) {
  if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
  // On phones Enter is a new line; the send button sends.
  if (isCoarsePointer()) return;
  e.preventDefault();
  handleSendReply();
}

// --- Opening a conversation --------------------------------------------------

// Every open gets a sequence number and its own AbortController: a slow
// response for a chat the user already left is cancelled, and if it still
// lands it is dropped instead of overwriting the chat now on screen.
let openSeq = 0;
let openAbort: AbortController | null = null;
let requestedId: string | null = null;
// On a phone, opening a thread from the list adds a history entry, so the
// system back button/gesture returns to the list instead of leaving the page.
let pushedThreadEntry = false;

async function openConversation(id: string, updateQuery = true) {
  if (updateQuery && route.query.id !== id) {
    const query = { ...route.query, id };
    if (isMobileViewport() && !route.query.id) {
      pushedThreadEntry = true;
      router.push({ query });
    } else {
      router.replace({ query });
    }
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
  threadError.value = null;
  threadLoading.value = true;

  try {
    const detail = await api.getConversation(id, { limit: MESSAGE_PAGE, signal: ctrl.signal });
    if (seq !== openSeq) return;
    selected.value = detail;
    unseenCount.value = 0;
    lastScrollTop = 0;
    resetWindow();
    await nextTick();
    chat.scrollToBottom();
  } catch (err) {
    if (seq !== openSeq || isAbortError(err)) return;
    requestedId = null;
    if (err instanceof ApiError && err.status === 404) {
      handleConversationGone(id);
    } else {
      console.error("Failed to load conversation", err);
      threadError.value = errorText(err);
    }
  } finally {
    if (seq === openSeq) {
      threadLoading.value = false;
      openAbort = null;
    }
  }
}

function retryOpen() {
  const id = typeof route.query.id === "string" ? route.query.id : null;
  if (id) openConversation(id, false);
}

function closeMobileThread() {
  isMobileThreadActive.value = false;
  if (!route.query.id) return;
  if (pushedThreadEntry) {
    pushedThreadEntry = false;
    router.back();
    return;
  }
  const nextQuery = { ...route.query };
  delete nextQuery.id;
  router.replace({ query: nextQuery });
}

// AI mode: the AI answers. Operator mode: the AI stays quiet and the owner
// answers (human_active — a person has taken the conversation over).
const statusSaving = ref(false);

async function setMode(mode: "ai" | "operator") {
  const conv = selected.value;
  if (!conv || statusSaving.value) return;
  if ((mode === "ai") === isAiStatus(conv.status)) return;
  const newStatus: ConversationStatus = mode === "ai" ? "ai_active" : "human_active";
  statusSaving.value = true;
  try {
    const updated = await api.updateConversationStatus(conv.id, newStatus);
    if (selected.value?.id === conv.id) selected.value.status = updated.status;
    const item = conversations.value.find((c) => c.id === conv.id);
    if (item) item.status = updated.status;
    toast.success(mode === "ai" ? t("conversations.switchedToAi") : t("conversations.switchedToOperator"));
  } catch (err) {
    console.error("Failed to update status", err);
    toast.error(errorText(err));
  } finally {
    statusSaving.value = false;
  }
}

// One line above the composer saying who answers this customer right now.
const threadNote = computed<{ tone: "muted" | "warn"; text: string; link?: string } | null>(() => {
  const c = selected.value;
  if (!c) return null;
  if (c.status === "closed") return { tone: "muted", text: t("conversations.noteClosed") };
  if (c.status === "human_needed") return { tone: "warn", text: t("conversations.noteHandoff") };
  if (isAiStatus(c.status)) {
    if (aiGloballyOff.value) return { tone: "warn", text: t("conversations.noteAiOff"), link: "/ai-settings" };
    return { tone: "muted", text: t("conversations.noteAi") };
  }
  return { tone: "muted", text: t("conversations.noteOperator") };
});

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
      // While pinned, the scroll follows on its own (useChatScroll); while
      // the owner reads further up, nothing moves — they get a counter.
      if (added.length > 0 && !chat.pinned.value) unseenCount.value += added.length;
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
    pushedThreadEntry = false;
    toast.success(t("conversations.deleted"));
    router.replace({ query: {} });
  } catch (err) {
    console.error("Failed to delete conversation", err);
    toast.error(errorText(err));
  } finally {
    deletingConversation.value = false;
  }
}

// --- AI feedback -------------------------------------------------------------

async function handlePositiveFeedback(msg: Message) {
  if (ratedMessages[msg.id] === "up") return;
  try {
    await api.submitAiFeedback({
      conversation_id: selected.value?.id,
      message_id: msg.id,
      rating: "thumb_up",
      ai_response: msg.content,
    });
    ratedMessages[msg.id] = "up";
    toast.success(t("conversations.feedbackThanks"));
  } catch (err) {
    console.error("Failed to submit feedback", err);
    toast.error(errorText(err));
  }
}

function openCorrectionModal(msg: Message) {
  feedbackTargetMessage.value = msg;
  // The customer message this reply answered: the nearest one before it.
  const msgs = threadMessages.value;
  const msgIndex = msgs.findIndex((m) => m.id === msg.id);
  let query = "";
  for (let i = msgIndex - 1; i >= 0; i--) {
    const m = msgs[i]!;
    if (m.sender_type === "customer") {
      query = m.content;
      break;
    }
    if (!isReaction(m)) break;
  }
  feedbackCustomerQuery.value = query;
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
  const target = feedbackTargetMessage.value;
  try {
    await api.submitAiFeedback({
      conversation_id: selected.value?.id,
      message_id: target.id,
      rating: "thumb_down",
      customer_query: feedbackCustomerQuery.value,
      ai_response: target.content,
      correction: feedbackCorrectionText.value.trim(),
    });
    ratedMessages[target.id] = "down";
    closeCorrectionModal();
    toast.success(t("conversations.correctionLearned"));
  } catch (err) {
    console.error("Failed to submit correction", err);
    toast.error(errorText(err));
  } finally {
    feedbackSubmitting.value = false;
  }
}

// --- Lifecycle ---------------------------------------------------------------

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
  } else if (conversations.value.length > 0 && !isMobileViewport()) {
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
  clockTimer = setInterval(() => (now.value = Date.now()), 60_000);
  loadContext();
  await loadInitial();
});

onUnmounted(() => {
  isMobileThreadActive.value = false;
  if (pollTimer) clearInterval(pollTimer);
  if (clockTimer) clearInterval(clockTimer);
  openAbort?.abort();
  pollAbort?.abort();
  stopConversationEvents?.();
  document.removeEventListener("visibilitychange", onVisibilityChange);
});

watch(
  () => route.query.id,
  async (newId) => {
    if (newId && typeof newId === "string") {
      if (requestedId !== newId) await openConversation(newId, false);
      else isMobileThreadActive.value = true;
    } else if (!newId) {
      // Back from a thread (button, gesture, or browser history).
      pushedThreadEntry = false;
      isMobileThreadActive.value = false;
    }
  }
);
</script>

<template>
  <div class="conversations-page" :class="{ 'mobile-thread-active': isMobileThreadActive }">
    <!-- Initial load -->
    <div v-if="loading" class="split-view" aria-busy="true">
      <div class="conversation-sidebar p-4 space-y-3">
        <Skeleton class="h-5 w-32 mb-4" />
        <div v-for="i in 5" :key="i" class="flex items-center gap-3 p-2 rounded-lg">
          <Skeleton class="w-10 h-10 rounded-full shrink-0" />
          <div class="space-y-2 flex-1">
            <Skeleton class="h-4 w-28" />
            <Skeleton class="h-3 w-40" />
          </div>
        </div>
      </div>
      <div class="thread-area p-6 space-y-4">
        <Skeleton class="h-10 w-48" />
        <Skeleton class="h-14 w-1/2 rounded-2xl" />
        <Skeleton class="h-14 w-2/3 rounded-2xl ml-auto" />
        <Skeleton class="h-14 w-1/3 rounded-2xl" />
      </div>
    </div>

    <!-- Initial load failed -->
    <div v-else-if="listError" class="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <AlertCircle :size="28" class="text-destructive" />
      <p class="text-sm font-medium text-foreground">{{ t("conversations.loadError") }}</p>
      <p class="text-xs text-muted-foreground">{{ listError }}</p>
      <Button variant="outline" class="h-10 gap-2" @click="loadInitial">
        <RefreshCw :size="14" />
        <span>{{ t("common.retry") }}</span>
      </Button>
    </div>

    <div v-else class="split-view">
      <!-- Conversation list -->
      <section
        class="conversation-sidebar"
        :class="{ 'mobile-hidden': isMobileThreadActive }"
        :aria-label="t('conversations.listTitle')"
      >
        <div class="flex items-center justify-between gap-2 border-b border-border px-4 py-2 shrink-0 min-h-14">
          <h2 class="text-sm font-semibold text-foreground">
            {{ t("conversations.listTitle") }}
            <span class="font-normal text-muted-foreground">{{ conversations.length }}{{ hasMoreConversations ? "+" : "" }}</span>
          </h2>
          <button
            type="button"
            class="icon-btn"
            :title="t('common.refresh')"
            :aria-label="t('common.refresh')"
            :disabled="refreshing"
            @click="manualRefresh"
          >
            <RefreshCw :size="16" :class="{ 'animate-spin': refreshing }" />
          </button>
        </div>

        <div
          v-if="syncError"
          class="px-4 py-2 text-xs text-destructive bg-destructive/5 border-b border-destructive/20 flex items-center gap-1.5 shrink-0"
          role="status"
        >
          <AlertCircle :size="12" class="shrink-0" />
          <span>{{ t("conversations.syncError") }}</span>
        </div>

        <ul v-if="conversations.length > 0" class="flex-1 min-h-0 overflow-y-auto overscroll-contain m-0 p-0 list-none custom-scrollbar">
          <li v-for="c in conversations" :key="c.id">
            <button
              type="button"
              class="conversation-item"
              :class="{ active: (selected?.id ?? requestedId) === c.id }"
              :aria-current="(selected?.id ?? requestedId) === c.id ? 'true' : undefined"
              @click="openConversation(c.id)"
            >
              <span class="avatar" aria-hidden="true">{{ getAvatarLetter(c.customer_username) }}</span>
              <span class="min-w-0 flex-1">
                <span class="flex items-baseline justify-between gap-2">
                  <span class="truncate text-sm text-foreground" :class="awaitingReply(c) ? 'font-bold' : 'font-semibold'">
                    {{ cleanCustomerName(c.customer_username) }}
                  </span>
                  <span class="shrink-0 text-xs" :class="awaitingReply(c) ? 'text-primary font-semibold' : 'text-muted-foreground'">
                    {{ listTime(c) }}
                  </span>
                </span>
                <span class="mt-0.5 flex items-center justify-between gap-2">
                  <span
                    class="truncate text-[13px]"
                    :class="awaitingReply(c) ? 'text-foreground font-medium' : 'text-muted-foreground'"
                  >
                    {{ previewText(c) || " " }}
                  </span>
                  <span
                    v-if="c.status === 'human_needed'"
                    class="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold text-amber-700 dark:text-amber-300"
                  >
                    {{ t("conversations.statusHandoff") }}
                  </span>
                  <span
                    v-else-if="isAiStatus(c.status)"
                    class="shrink-0 inline-flex items-center gap-1 rounded-full bg-primary/10 px-1.5 py-0.5 text-[11px] font-semibold text-primary"
                    :title="t('conversations.modeAiHint')"
                  >
                    <Bot :size="12" aria-hidden="true" />
                    <span>{{ t("conversations.modeAi") }}</span>
                  </span>
                  <span
                    v-else
                    class="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground"
                    :title="t('conversations.modeOperatorHint')"
                  >
                    {{ c.status === "closed" ? t("conversations.statusClosed") : t("conversations.modeOperator") }}
                  </span>
                </span>
              </span>
            </button>
          </li>
          <li v-if="hasMoreConversations" class="p-3 flex justify-center">
            <Button
              variant="outline"
              class="h-10 gap-1.5"
              :disabled="loadingMoreConversations"
              @click="loadMoreConversations"
            >
              <Loader2 v-if="loadingMoreConversations" :size="14" class="animate-spin" />
              <span>{{ t("common.loadMore") }}</span>
            </Button>
          </li>
        </ul>

        <!-- No conversations yet: say what to do about it -->
        <div v-else class="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <div class="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
            <MessageSquare :size="22" />
          </div>
          <h2 class="text-sm font-semibold text-foreground">{{ t("conversations.noConversationsTitle") }}</h2>
          <p class="max-w-xs text-sm text-muted-foreground">
            {{ igConnected === true ? t("conversations.emptyConnectedHint") : t("conversations.noConversationsDesc") }}
          </p>
          <Button v-if="igConnected === false" as-child class="h-10 gap-2">
            <NuxtLink to="/integrations">
              <Plug :size="16" />
              <span>{{ t("conversations.connectInstagram") }}</span>
            </NuxtLink>
          </Button>
          <Button v-else-if="igConnected === true" variant="outline" as-child class="h-10 gap-2">
            <NuxtLink to="/sandbox">
              <Sparkles :size="16" />
              <span>{{ t("conversations.tryInSandbox") }}</span>
            </NuxtLink>
          </Button>
          <Button v-else variant="outline" as-child class="h-10">
            <NuxtLink to="/integrations">{{ t("conversations.openIntegrations") }}</NuxtLink>
          </Button>
        </div>
      </section>

      <!-- Thread -->
      <section class="thread-area" :class="{ 'mobile-visible': isMobileThreadActive }">
        <template v-if="selected">
          <!-- Thread header -->
          <header class="thread-header">
            <button
              type="button"
              class="mobile-back-btn icon-btn -ml-1"
              :aria-label="t('conversations.backToList')"
              @click="closeMobileThread"
            >
              <ArrowLeft :size="20" />
            </button>

            <span class="avatar hidden sm:flex" aria-hidden="true">{{ getAvatarLetter(selected.customer_username) }}</span>
            <div class="min-w-0 flex-1">
              <h2 class="truncate text-[15px] font-semibold leading-tight text-foreground">
                {{ cleanCustomerName(selected.customer_username) }}
              </h2>
              <p class="truncate text-xs text-muted-foreground">{{ threadSubtitle }}</p>
            </div>

            <!-- Who replies: AI or the owner -->
            <div
              class="mode-toggle"
              role="radiogroup"
              :aria-label="t('conversations.modeGroupLabel')"
              :aria-busy="statusSaving"
            >
              <button
                type="button"
                role="radio"
                class="mode-option"
                :aria-checked="isAiStatus(selected.status)"
                :title="t('conversations.modeAiHint')"
                :disabled="statusSaving"
                @click="setMode('ai')"
              >
                <Bot :size="14" aria-hidden="true" />
                <span>{{ t("conversations.modeAi") }}</span>
              </button>
              <button
                type="button"
                role="radio"
                class="mode-option"
                :aria-checked="!isAiStatus(selected.status)"
                :title="t('conversations.modeOperatorHint')"
                :disabled="statusSaving"
                @click="setMode('operator')"
              >
                <span>{{ t("conversations.modeOperator") }}</span>
              </button>
            </div>

            <!-- Everything else, destructive last, behind one menu -->
            <DropdownMenuRoot :modal="false">
              <DropdownMenuTrigger as-child>
                <button type="button" class="icon-btn" :aria-label="t('conversations.moreActions')" :title="t('conversations.moreActions')">
                  <MoreVertical :size="18" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuPortal>
                <DropdownMenuContent align="end" :side-offset="6" class="menu-content">
                  <DropdownMenuItem class="menu-item" :disabled="refreshing" @select="manualRefresh">
                    <RefreshCw :size="16" />
                    <span>{{ t("common.refresh") }}</span>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator class="my-1 h-px bg-border" />
                  <DropdownMenuItem class="menu-item text-destructive" @select="openDeleteConfirmModal">
                    <Trash2 :size="16" />
                    <span>{{ t("conversations.deleteChatTitle") }}</span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenuPortal>
            </DropdownMenuRoot>
          </header>

          <!-- Messages -->
          <div class="relative flex min-h-0 flex-1 flex-col">
            <div
              ref="messagesContainerRef"
              class="flex-1 min-h-0 overflow-y-auto overscroll-contain custom-scrollbar"
              role="log"
              :aria-label="t('conversations.messagesLabel')"
              @scroll.passive="handleMessagesScroll"
              @wheel.passive="chat.onUserIntent"
              @touchstart.passive="chat.onUserIntent"
            >
              <div ref="messagesContentRef" class="flex flex-col gap-3 px-3 py-4 sm:px-5">
                <div v-if="hasOlderMessages" class="flex justify-center">
                  <button
                    type="button"
                    class="inline-flex min-h-9 items-center gap-1.5 rounded-full border border-border bg-card px-3.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted"
                    :disabled="isLoadingOlder"
                    @click="loadOlderMessages"
                  >
                    <Loader2 v-if="isLoadingOlder" :size="12" class="animate-spin" />
                    <ChevronUp v-else :size="12" />
                    <span>{{ hiddenLoadedCount > 0 ? t("conversations.loadOlder", { count: hiddenLoadedCount }) : t("conversations.loadOlderServer") }}</span>
                  </button>
                </div>

                <template v-for="row in threadRows" :key="row.key">
                  <!-- Day divider -->
                  <div v-if="row.kind === 'day'" class="day-divider" role="separator">
                    <span>{{ row.label }}</span>
                  </div>

                  <!-- A reaction whose customer message is outside the loaded window -->
                  <div v-else-if="row.kind === 'reaction'" class="flex justify-center">
                    <span class="inline-flex items-center gap-1.5 rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground" :class="{ 'opacity-60': row.msg.delivery_status === 'failed' }">
                      <span aria-hidden="true">{{ row.msg.content }}</span>
                      <span>{{ reactionLabel(row.msg) }}</span>
                    </span>
                  </div>

                  <!-- Messages from one sender, close together -->
                  <div
                    v-else
                    class="flex flex-col gap-1"
                    :class="row.sender === 'customer' ? 'items-start' : 'items-end'"
                  >
                    <div
                      v-if="row.sender !== 'customer'"
                      class="flex items-center gap-1 px-1 text-xs font-medium text-muted-foreground"
                    >
                      <Bot v-if="row.sender === 'ai'" :size="12" class="text-primary" aria-hidden="true" />
                      <span>{{ groupSenderLabel(row.sender) }}</span>
                    </div>

                    <div
                      v-for="(item, idx) in row.items"
                      :key="item.msg.id"
                      class="bubble-row flex max-w-[88%] flex-col sm:max-w-[75%]"
                      :class="[
                        row.sender === 'customer' ? 'items-start' : 'items-end',
                        item.reactions.length ? 'mb-3' : '',
                        idx === row.items.length - 1 ? 'bubble-row-last' : '',
                      ]"
                    >
                      <div
                        class="bubble relative"
                        :class="[
                          item.msg.sender_type === 'customer' ? 'bubble-customer' : item.msg.sender_type === 'human' ? 'bubble-human' : 'bubble-ai',
                          idx === row.items.length - 1 ? 'bubble-tail' : '',
                          item.msg.content ? 'px-3.5 py-2' : 'p-1',
                          item.msg.delivery_status === 'pending' ? 'opacity-70' : '',
                          item.msg.delivery_status === 'failed' ? 'bubble-failed' : '',
                        ]"
                        :title="formatClock(item.msg.created_at)"
                      >
                        <template v-if="mediaOf(item.msg)">
                          <!-- Photo: its box is sized up front, so loading it never shifts the thread -->
                          <button
                            v-if="mediaOf(item.msg)!.kind === 'image'"
                            type="button"
                            class="media-box block cursor-zoom-in"
                            :class="{ 'mb-1.5': item.msg.content }"
                            :aria-label="t('common.zoom')"
                            @click="openMediaPreview(mediaOf(item.msg)!.url, 'image')"
                          >
                            <img
                              :src="mediaOf(item.msg)!.url"
                              :alt="t('conversations.imageAlt')"
                              class="h-full w-full object-contain"
                              loading="lazy"
                              decoding="async"
                              referrerpolicy="no-referrer"
                            />
                          </button>

                          <video
                            v-else-if="mediaOf(item.msg)!.kind === 'video'"
                            :src="mediaOf(item.msg)!.url"
                            controls
                            playsinline
                            preload="metadata"
                            class="media-box block bg-black object-contain"
                            :class="{ 'mb-1.5': item.msg.content }"
                          ></video>

                          <!-- Voice note; its transcript is the text below -->
                          <audio
                            v-else-if="mediaOf(item.msg)!.kind === 'audio'"
                            :src="mediaOf(item.msg)!.url"
                            controls
                            preload="none"
                            class="block h-10 w-[240px] max-w-full"
                            :class="{ 'mb-1.5': item.msg.content }"
                          ></audio>

                          <!-- Shared post / other attachment: an explicit, https-only link -->
                          <div
                            v-else
                            class="flex items-center justify-between gap-3 rounded-xl border border-border bg-card p-2.5"
                            :class="{ 'mb-1.5': item.msg.content }"
                          >
                            <div class="flex min-w-0 items-center gap-2.5">
                              <div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-pink-500/15 text-pink-600 dark:text-pink-400">
                                <Film :size="18" />
                              </div>
                              <span class="truncate text-xs font-semibold text-foreground">
                                {{ isSharedPost(item.msg) ? t("conversations.reelShared") : t("conversations.attachment") }}
                              </span>
                            </div>
                            <a
                              :href="mediaOf(item.msg)!.url"
                              target="_blank"
                              rel="noopener noreferrer"
                              class="inline-flex min-h-9 shrink-0 items-center gap-1 rounded-lg border border-border bg-background px-2.5 text-xs font-medium text-primary hover:bg-muted"
                            >
                              <span>{{ t("common.open") }}</span>
                              <ExternalLink :size="12" />
                            </a>
                          </div>
                        </template>

                        <div v-if="hasUnviewableMedia(item.msg)" class="px-2.5 py-1.5 text-xs italic text-muted-foreground">
                          {{ isSharedPost(item.msg) ? t("conversations.reelShared") : t("conversations.unviewableMessage") }}
                        </div>

                        <!-- Text is always plain text, exactly as sent -->
                        <div
                          v-if="item.msg.content"
                          class="whitespace-pre-wrap break-words text-sm leading-relaxed"
                        >{{ item.msg.content }}</div>

                        <!-- The AI answered this message with a reaction instead of words -->
                        <span v-if="item.reactions.length" class="reaction-chips">
                          <span
                            v-for="r in item.reactions"
                            :key="r.id"
                            class="reaction-chip"
                            :class="{ 'reaction-chip-muted': r.delivery_status === 'failed' || r.delivery_status === 'pending' }"
                            role="img"
                            :aria-label="reactionLabel(r)"
                            :title="reactionLabel(r)"
                          >
                            <span aria-hidden="true">{{ r.content }}</span>
                            <AlertCircle v-if="r.delivery_status === 'failed'" :size="11" class="text-destructive" aria-hidden="true" />
                          </span>
                        </span>
                      </div>

                      <p
                        v-if="item.reactions.some((r) => r.delivery_status === 'failed')"
                        class="mt-4 px-1 text-xs text-destructive"
                      >
                        {{ t("conversations.reactionNotDelivered") }}
                      </p>

                      <!-- Outbound delivery problems, per message -->
                      <div
                        v-if="item.msg.delivery_status === 'pending'"
                        class="mt-0.5 flex items-center gap-1 px-1 text-xs text-muted-foreground"
                        role="status"
                      >
                        <Clock :size="11" aria-hidden="true" />
                        <span>{{ t("conversations.sending") }}</span>
                      </div>
                      <div
                        v-else-if="item.msg.delivery_status === 'failed' && item.msg.sender_type !== 'customer'"
                        class="mt-0.5 flex flex-wrap items-center justify-end gap-x-2 px-1 text-xs text-destructive"
                        role="alert"
                      >
                        <span class="inline-flex items-center gap-1">
                          <AlertCircle :size="12" aria-hidden="true" />
                          {{ item.msg.delivery_error || t("conversations.notDelivered") }}
                        </span>
                        <button
                          v-if="item.msg.sender_type === 'human'"
                          type="button"
                          class="min-h-8 font-semibold underline underline-offset-2"
                          @click="retrySendMessage(item.msg)"
                        >
                          {{ t("conversations.retry") }}
                        </button>
                      </div>

                      <!-- Teach the AI: quiet until the reply is hovered/focused (always shown on touch) -->
                      <div
                        v-if="item.msg.sender_type === 'ai' && item.msg.content && !mediaOf(item.msg)"
                        class="feedback-bar"
                        :class="{ 'feedback-bar-rated': ratedMessages[item.msg.id] }"
                      >
                        <button
                          type="button"
                          class="feedback-btn"
                          :class="{ 'text-primary': ratedMessages[item.msg.id] === 'up' }"
                          :aria-label="t('conversations.goodResponse')"
                          :aria-pressed="ratedMessages[item.msg.id] === 'up'"
                          :title="t('conversations.goodResponse')"
                          @click="handlePositiveFeedback(item.msg)"
                        >
                          <ThumbsUp :size="14" />
                        </button>
                        <button
                          type="button"
                          class="feedback-btn"
                          :class="{ 'text-primary': ratedMessages[item.msg.id] === 'down' }"
                          :title="t('conversations.correctResponseTitle')"
                          @click="openCorrectionModal(item.msg)"
                        >
                          <ThumbsDown :size="14" />
                          <span>{{ t("conversations.correct") }}</span>
                        </button>
                      </div>
                    </div>

                    <!-- Group time (and delivery tick for what we sent) -->
                    <div class="flex items-center gap-1 px-1 text-[11px] text-muted-foreground">
                      <span>{{ formatClock(row.items[row.items.length - 1]!.msg.created_at) }}</span>
                      <template v-if="row.sender !== 'customer' && row.items[row.items.length - 1]!.msg.delivery_status === 'sent'">
                        <Check :size="12" aria-hidden="true" />
                        <span class="sr-only">{{ t("conversations.delivered") }}</span>
                      </template>
                    </div>
                  </div>
                </template>
              </div>
            </div>

            <!-- Back to the newest message -->
            <Transition name="fade-up">
              <button
                v-if="!chat.pinned.value"
                type="button"
                class="jump-latest"
                :class="{ 'jump-latest-count': unseenCount > 0 }"
                :aria-label="unseenCount > 0 ? t('conversations.newMessages', { count: unseenCount }) : t('conversations.scrollToBottom')"
                @click="jumpToLatest"
              >
                <ArrowDown :size="16" aria-hidden="true" />
                <span v-if="unseenCount > 0">{{ t("conversations.newMessages", { count: unseenCount }) }}</span>
              </button>
            </Transition>
          </div>

          <!-- Composer -->
          <footer class="composer">
            <p
              v-if="threadNote"
              class="mb-2 flex items-start gap-1.5 text-xs leading-snug"
              :class="threadNote.tone === 'warn' ? 'text-amber-700 dark:text-amber-300' : 'text-muted-foreground'"
            >
              <Info :size="13" class="mt-px shrink-0" aria-hidden="true" />
              <span>
                {{ threadNote.text }}
                <NuxtLink v-if="threadNote.link" :to="threadNote.link" class="font-semibold underline underline-offset-2">
                  {{ t("nav.aiSettings") }}
                </NuxtLink>
              </span>
            </p>

            <div v-if="!canReply" class="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-muted/50 px-3 py-2.5">
              <span class="text-sm text-foreground">{{ t("conversations.connectToReply") }}</span>
              <Button as-child class="h-10 gap-2">
                <NuxtLink to="/integrations">
                  <Plug :size="16" />
                  <span>{{ t("conversations.connectInstagram") }}</span>
                </NuxtLink>
              </Button>
            </div>

            <form v-else class="flex items-end gap-2" @submit.prevent="handleSendReply">
              <label for="reply-input" class="sr-only">{{ t("conversations.typeMessage") }}</label>
              <textarea
                id="reply-input"
                ref="replyInputRef"
                v-model="replyText"
                rows="1"
                :maxlength="REPLY_MAX_LENGTH"
                :placeholder="t('conversations.typeMessage')"
                aria-describedby="reply-hint"
                enterkeyhint="enter"
                class="composer-input"
                @keydown="handleReplyKeydown"
              ></textarea>
              <span id="reply-hint" class="sr-only">{{ t("conversations.composerHint") }}</span>
              <Button
                type="submit"
                class="h-11 min-w-11 shrink-0 gap-2 rounded-xl px-3 sm:px-4"
                :disabled="!replyText.trim()"
                :aria-label="t('conversations.send')"
                @mousedown.prevent
              >
                <Send :size="18" />
                <span class="hidden sm:inline">{{ t("conversations.send") }}</span>
              </Button>
            </form>
            <p
              v-if="canReply && replyText.length >= REPLY_COUNTER_FROM"
              class="mt-1 text-right text-xs"
              :class="replyText.length >= REPLY_MAX_LENGTH ? 'text-destructive' : 'text-muted-foreground'"
              aria-live="polite"
            >
              {{ t("conversations.charsLeft", { count: REPLY_MAX_LENGTH - replyText.length }) }}
            </p>
          </footer>
        </template>

        <!-- Loading / failed / nothing selected: keep a way back on phones -->
        <template v-else>
          <header v-if="isMobileThreadActive" class="thread-header thread-header-mobile">
            <button type="button" class="mobile-back-btn icon-btn -ml-1" :aria-label="t('conversations.backToList')" @click="closeMobileThread">
              <ArrowLeft :size="20" />
            </button>
          </header>
          <div v-if="threadLoading" class="flex flex-1 items-center justify-center p-8 text-muted-foreground" aria-busy="true">
            <Loader2 :size="22" class="animate-spin" />
            <span class="sr-only">{{ t("common.loading") }}</span>
          </div>
          <div v-else-if="threadError" class="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
            <AlertCircle :size="24" class="text-destructive" />
            <p class="text-sm text-foreground">{{ t("conversations.threadLoadError") }}</p>
            <p class="text-xs text-muted-foreground">{{ threadError }}</p>
            <Button variant="outline" class="h-10 gap-2" @click="retryOpen">
              <RefreshCw :size="14" />
              <span>{{ t("common.retry") }}</span>
            </Button>
          </div>
          <div v-else class="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center text-muted-foreground">
            <MessageSquare :size="28" class="opacity-60" />
            <p class="text-sm">{{ t("conversations.selectChat") }}</p>
          </div>
        </template>
      </section>
    </div>

    <!-- Teach the AI a better reply -->
    <Dialog :open="isFeedbackModalOpen" @update:open="(v) => { if (!v) closeCorrectionModal() }">
      <DialogContent class="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle class="flex items-center gap-2">
            <Sparkles :size="18" class="text-primary" />
            {{ t("conversations.trainAiTitle") }}
          </DialogTitle>
        </DialogHeader>

        <div class="flex flex-col gap-4">
          <div v-if="feedbackCustomerQuery" class="bg-muted/50 px-4 py-3 rounded-lg border-l-2 border-primary">
            <div class="text-xs text-muted-foreground font-semibold mb-1">{{ t("conversations.customerQuery") }}</div>
            <div class="text-sm text-foreground whitespace-pre-wrap">{{ feedbackCustomerQuery }}</div>
          </div>

          <div v-if="feedbackTargetMessage" class="bg-destructive/10 px-4 py-3 rounded-lg border-l-2 border-destructive">
            <div class="text-xs text-destructive font-semibold mb-1">{{ t("conversations.aiWrongResponse") }}</div>
            <div class="text-sm text-foreground whitespace-pre-wrap">{{ feedbackTargetMessage.content }}</div>
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

          <div class="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="outline" class="h-10" @click="closeCorrectionModal">{{ t("common.cancel") }}</Button>
            <Button :disabled="feedbackSubmitting || !feedbackCorrectionText.trim()" class="h-10 gap-2" @click="submitCorrection">
              <CheckCircle2 :size="16" />
              {{ feedbackSubmitting ? t("common.saving") : t("conversations.trainAndSave") }}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>

    <!-- Delete conversation -->
    <Dialog v-model:open="isDeleteConfirmModalOpen">
      <DialogContent class="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>{{ t("conversations.deleteModalTitle") }}</DialogTitle>
          <DialogDescription>
            {{ t("conversations.deleteModalConfirm", { name: cleanCustomerName(selected?.customer_username) }) }}
          </DialogDescription>
        </DialogHeader>
        <p class="text-xs text-muted-foreground">{{ t("conversations.deleteKeepsLead") }}</p>
        <div class="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" class="h-10" @click="isDeleteConfirmModalOpen = false">{{ t("common.cancel") }}</Button>
          <Button variant="destructive" class="h-10 gap-2" :disabled="deletingConversation" @click="confirmDeleteConversation">
            <Trash2 :size="16" />
            {{ deletingConversation ? t("common.deleting") : t("common.delete") }}
          </Button>
        </div>
      </DialogContent>
    </Dialog>

    <!-- Media preview -->
    <Dialog :open="!!previewMediaUrl" @update:open="(v) => { if (!v) closeMediaPreview() }">
      <DialogContent class="sm:max-w-[720px] p-2">
        <DialogTitle class="sr-only">{{ t("conversations.imageAlt") }}</DialogTitle>
        <div class="flex flex-col items-center justify-center p-1">
          <img
            v-if="previewMediaType === 'image' && previewMediaUrl"
            :src="previewMediaUrl"
            :alt="t('conversations.imageAlt')"
            referrerpolicy="no-referrer"
            class="max-h-[82dvh] w-auto max-w-full rounded-lg object-contain"
          />
          <video
            v-else-if="previewMediaType === 'video' && previewMediaUrl"
            :src="previewMediaUrl"
            controls
            autoplay
            playsinline
            class="max-h-[82dvh] w-auto max-w-full rounded-lg"
          ></video>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
