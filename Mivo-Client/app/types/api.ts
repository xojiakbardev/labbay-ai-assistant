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
  ui_preferences?: Record<string, any> | null;
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

export interface Lead {
  id: string;
  customer_id: string;
  customer_username?: string | null;
  conversation_id: string;
  status: "cold" | "warm" | "hot";
  score: number;
  phone: string | null;
  interested_products: { id: string; name: string }[];
  summary: string | null;
  qualification_reason: string | null;
  summaries?: Record<string, string> | null;
  hot_notified_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationSummary {
  id: string;
  customer_id: string;
  customer_username?: string | null;
  customer_phone?: string | null;
  channel: string;
  status: string;
  last_message_at: string | null;
  created_at: string;
}

export interface Message {
  id: string;
  sender_type: "customer" | "ai" | "human" | "system";
  content: string;
  message_type: string;
  created_at: string;
  status?: "pending" | "sent" | "failed";
}

export interface ConversationDetail extends ConversationSummary {
  messages: Message[];
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

export interface SuperadminBusiness {
  id: string;
  name: string;
  owner_email: string;
  ai_enabled: boolean;
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

export interface NotificationItem {
  id: string;
  business_id: string;
  type: "lead_hot" | "lead_warm" | "lead_updated" | "system" | string;
  title: string;
  message: string;
  lead_id?: string | null;
  customer_id?: string | null;
  extra_metadata?: Record<string, any>;
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

