export interface Business {
  id: string;
  name: string;
  subscription_expires_at: string | null;
  description: string | null;
  target_customers: string | null;
  tone: string | null;
  language: string | null;
  selling_approach: string | null;
  rules_text: string | null;
  discount_policy: string | null;
  delivery_info: string | null;
  payment_info: string | null;
  handoff_instructions: string | null;
  ai_enabled: boolean;
  // The platform's kill switch (set by the superadmin) — read-only here; the
  // owner's own ai_enabled switch can't override it.
  ai_suspended: boolean;
  ui_preferences?: Record<string, any> | null;
}

// PATCH /business rejects unknown fields (extra=forbid) and an explicit null
// for name/ai_enabled — send only what was actually edited.
export interface BusinessUpdate {
  name?: string;
  description?: string | null;
  target_customers?: string | null;
  tone?: string | null;
  language?: string | null;
  selling_approach?: string | null;
  rules_text?: string | null;
  discount_policy?: string | null;
  delivery_info?: string | null;
  payment_info?: string | null;
  handoff_instructions?: string | null;
  ai_enabled?: boolean;
  ui_preferences?: Record<string, any>;
}

export interface Variant {
  id?: string;
  variant_type: string;
  value: string;
  attributes?: Record<string, any>;
  sku?: string | null;
  barcode?: string | null;
  price_override?: number | null;
  stock_quantity?: number | null;
  image_url?: string | null;
  images?: string[];
  availability: boolean;
}

export interface ProductImage {
  id: string;
  url: string;
  is_primary: boolean;
}

export interface Product {
  id: string;
  name: string;
  description: string | null;
  price: number | null;
  currency: string;
  availability: boolean;
  attributes: Record<string, unknown>;
  source: "manual" | "ai_import";
  variants: Variant[];
  images: ProductImage[];
}

// Write shapes (ProductCreate / ProductUpdate on the server). Image URLs must
// be http(s) — `data:` URLs are rejected with a 422; upload files first.
export interface ImageInput {
  url: string;
  is_primary: boolean;
}

export interface VariantInput {
  variant_type: string;
  value: string;
  attributes: Record<string, any>;
  sku: string | null;
  barcode: string | null;
  price_override: number | null;
  stock_quantity: number | null;
  image_url: string | null;
  images: string[];
  availability: boolean;
}

export interface ProductInput {
  name: string;
  description?: string | null;
  price?: number | null;
  currency?: string;
  availability?: boolean;
  attributes?: Record<string, any>;
  variants?: VariantInput[];
  images?: ImageInput[];
}

// PATCH never sends null for name/currency/availability/attributes (NOT NULL
// columns) — omit a field to leave it unchanged.
export interface ProductPatch {
  name?: string;
  description?: string | null;
  price?: number | null;
  currency?: string;
  availability?: boolean;
  attributes?: Record<string, any>;
  variants?: VariantInput[];
  images?: ImageInput[];
}

export interface MediaUpload {
  url: string;
}

export interface Lead {
  id: string;
  customer_id: string;
  customer_username?: string | null;
  // null once the conversation was deleted — the lead outlives it.
  conversation_id: string | null;
  status: "cold" | "warm" | "hot";
  score: number;
  phone: string | null;
  interested_products: { id: string; name: string }[];
  summary: string | null;
  qualification_reason: string | null;
  // Only the locales the AI actually wrote (uz always, ru/en maybe).
  summaries?: Record<string, string> | null;
  hot_notified_at: string | null;
  created_at: string;
  updated_at: string;
}

export type ConversationStatus = "ai_active" | "active" | "human_needed" | "human_active" | "closed";

export interface ConversationSummary {
  id: string;
  customer_id: string;
  customer_username?: string | null;
  customer_name?: string | null;
  customer_phone?: string | null;
  channel: string;
  status: ConversationStatus | string;
  last_message_at: string | null;
  created_at: string;
}

export type DeliveryStatus = "pending" | "sent" | "failed" | "unknown";

export interface Message {
  id: string;
  sender_type: "customer" | "ai" | "human" | "system";
  content: string;
  message_type: string;
  // Media lives only here — never parse it out of `content`, which is
  // whatever the customer typed.
  attachment_url: string | null;
  attachment_type: string | null;
  // Outbound messages only.
  delivery_status: DeliveryStatus | null;
  delivery_error: string | null;
  created_at: string;
  // Client-only: an optimistic operator reply that the server hasn't
  // acknowledged (pending) or that never reached it (failed).
  local?: boolean;
}

export interface ConversationDetail extends ConversationSummary {
  // Oldest first — the most recent `limit` messages.
  messages: Message[];
  has_more_messages: boolean;
}

// Non-notification payload on the SSE `notification` event.
export interface ConversationUpdatedEvent {
  type: "conversation_updated";
  conversation_id: string;
  customer_id: string;
  last_message: string;
  sender_type: string;
}

export interface Customer {
  id: string;
  business_id: string;
  ig_scoped_id: string;
  username: string | null;
  name: string | null;
  phone: string | null;
}

export interface AiFeedback {
  id: string;
  business_id: string;
  conversation_id?: string | null;
  message_id?: string | null;
  rating: "thumb_up" | "thumb_down";
  customer_query?: string | null;
  ai_response?: string | null;
  correction?: string | null;
  is_active: boolean;
  created_at: string;
}

export interface FeedbackCreateInput {
  conversation_id?: string | null;
  message_id?: string | null;
  rating: "thumb_up" | "thumb_down";
  customer_query?: string | null;
  ai_response?: string | null;
  correction?: string | null;
}

export interface InstagramStatus {
  connected: boolean;
  username?: string | null;
  expires_at?: string | null;
}

export interface TelegramStatus {
  connected: boolean;
  username?: string | null;
  chat_id?: string | null;
}

export interface SuperadminBusiness {
  id: string;
  name: string;
  owner_email: string;
  // The owner's own switch — read-only for the superadmin.
  ai_enabled: boolean;
  // The platform kill switch the superadmin controls.
  ai_suspended: boolean;
  subscription_expires_at: string | null;
  subscription_active: boolean;
  created_at: string;
  cost_last_30d_usd: number;
}

export interface SuperadminStats {
  total_businesses: number;
  active_businesses: number;
  expired_businesses: number;
  tokens_today: number;
  cost_today_usd: number;
  cost_this_month_usd: number;
  income_this_month: Record<string, number>;
  openrouter_balance_usd: number | null;
  openrouter_limit_usd: number | null;
}

export interface UsagePoint {
  date: string;
  tokens: number;
  cost_usd: number;
}

export interface RevenuePoint {
  period: string;
  income: Record<string, number>;
  expense_usd: number;
}

export type NotificationType =
  | "lead_hot"
  | "lead_warm"
  | "lead_updated"
  | "handoff"
  | "ai_limit"
  | "delivery_failed"
  | "system";

export interface NotificationItem {
  id: string;
  business_id?: string;
  type: NotificationType | string;
  title: string;
  message: string;
  lead_id?: string | null;
  customer_id?: string | null;
  // handoff / ai_limit / delivery_failed carry { conversation_id }.
  extra_metadata?: Record<string, any> | null;
  is_read: boolean;
  created_at: string;
  read_at?: string | null;
}

export interface SandboxMessage {
  id: string;
  sender_type: "customer" | "ai" | "human" | string;
  content: string;
  attachment_url?: string | null;
  created_at: string;
}

export interface SandboxProduct {
  id: string;
  name: string;
  price: number | null;
  currency: string;
  image_url: string | null;
  availability: boolean;
}

export interface SandboxTurnResponse {
  reply: string;
  lead_status: "cold" | "warm" | "hot";
  lead_score: number;
  qualification_reason: string;
  phone_detected?: string | null;
  extracted_facts: string[];
  known_facts: { text: string; noted_at: string }[];
  interested_products: SandboxProduct[];
  executed_tools: { name: string; arguments: Record<string, any> }[];
  telegram_sent: boolean;
  messages: SandboxMessage[];
  // The customer's message just ended the conversation: no reply was
  // written, at most a reaction (an emoji) was left on it.
  conversation_closed: boolean;
  reaction: string | null;
}

export interface SandboxState {
  conversation_id: string | null;
  messages: SandboxMessage[];
  lead_status: "cold" | "warm" | "hot" | null;
  lead_score: number | null;
  phone: string | null;
  qualification_reason: string | null;
  known_facts: { text: string; noted_at: string }[];
  interested_products: SandboxProduct[];
}
