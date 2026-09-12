import type {
  AiFeedback,
  Business,
  BusinessUpdate,
  ConversationDetail,
  ConversationStatus,
  ConversationSummary,
  Customer,
  FeedbackCreateInput,
  InstagramStatus,
  Lead,
  MediaUpload,
  Message,
  NotificationItem,
  Product,
  ProductImage,
  ProductInput,
  ProductPatch,
  ReplyDefaults,
  SandboxState,
  SandboxTurnResponse,
  TelegramStatus,
} from "~/types/api";


export function useMivoApi() {
  const { apiRequest } = useApi();

  return {
    getBusiness: () => apiRequest<Business>("/business"),
    updateBusiness: (patch: BusinessUpdate) =>
      apiRequest<Business>("/business", { method: "PATCH", body: patch }),
    // Built-in wording of the fixed replies (reply_texts overrides these).
    getReplyDefaults: () => apiRequest<ReplyDefaults>("/business/reply-defaults"),

    listProducts: () => apiRequest<Product[]>("/products"),
    getProduct: (id: string) => apiRequest<Product>(`/products/${id}`),
    createProduct: (product: ProductInput) =>
      apiRequest<Product>("/products", { method: "POST", body: product }),
    updateProduct: (id: string, patch: ProductPatch) =>
      apiRequest<Product>(`/products/${id}`, { method: "PATCH", body: patch }),
    deleteProduct: (id: string) => apiRequest<void>(`/products/${id}`, { method: "DELETE" }),
    // Adds an image row to an existing product; is_primary makes it the only primary.
    uploadProductImage: (id: string, file: File, isPrimary = false) => {
      const form = new FormData();
      form.append("file", file);
      return apiRequest<ProductImage>(`/products/${id}/images?is_primary=${isPrimary}`, {
        method: "POST",
        body: form,
        isForm: true,
      });
    },
    // Stores an image and returns its public URL (variant photos, and the main
    // photo of a product that doesn't exist yet). jpeg/png/webp, ≤ 5 MB.
    uploadProductMedia: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return apiRequest<MediaUpload>("/products/media", { method: "POST", body: form, isForm: true });
    },

    // Extraction only — nothing is saved until confirmImport.
    previewImport: (text: string) =>
      apiRequest<{ products: ProductInput[] }>("/products/import/preview", {
        method: "POST",
        body: { text },
      }),
    // All-or-nothing; 422 with a message on invalid data.
    confirmImport: (products: ProductInput[]) =>
      apiRequest<Product[]>("/products/import/confirm", { method: "POST", body: { products } }),

    listLeads: (limit = 500, offset = 0) => apiRequest<Lead[]>(`/leads?limit=${limit}&offset=${offset}`),
    getLead: (id: string) => apiRequest<Lead>(`/leads/${id}`),

    // Newest activity first; limit ≤ 200.
    listConversations: (limit = 50, offset = 0) =>
      apiRequest<ConversationSummary[]>(`/conversations?limit=${limit}&offset=${offset}`),
    getConversation: (id: string, opts: { limit?: number; signal?: AbortSignal } = {}) =>
      apiRequest<ConversationDetail>(`/conversations/${id}?limit=${opts.limit ?? 100}`, { signal: opts.signal }),
    // Only messages newer than `afterId` (oldest first) — for polling.
    listMessagesAfter: (id: string, afterId: string, opts: { limit?: number; signal?: AbortSignal } = {}) =>
      apiRequest<Message[]>(
        `/conversations/${id}/messages?after=${encodeURIComponent(afterId)}&limit=${opts.limit ?? 100}`,
        { signal: opts.signal }
      ),
    // 400 {detail} when delivery fails — the message is still stored (as failed).
    sendConversationReply: (id: string, content: string) =>
      apiRequest<Message>(`/conversations/${id}/reply`, {
        method: "POST",
        body: { content },
      }),
    updateConversationStatus: (id: string, status: ConversationStatus) =>
      apiRequest<ConversationSummary>(`/conversations/${id}/status?status_value=${status}`, {
        method: "PATCH",
      }),
    deleteConversation: (id: string) =>
      apiRequest<void>(`/conversations/${id}`, { method: "DELETE" }),
    // phone: a valid phone number or "" to clear (422 otherwise).
    updateCustomer: (id: string, patch: { username?: string; name?: string; phone?: string }) =>
      apiRequest<Customer>(`/customers/${id}`, {
        method: "PATCH",
        body: patch,
      }),

    submitAiFeedback: (input: FeedbackCreateInput) =>
      apiRequest<AiFeedback>("/ai/feedback", { method: "POST", body: input }),
    getLearnedRules: () => apiRequest<AiFeedback[]>("/ai/learned-rules"),
    deleteLearnedRule: (id: string) =>
      apiRequest<void>(`/ai/learned-rules/${id}`, { method: "DELETE" }),

    getInstagramStatus: () => apiRequest<InstagramStatus>("/integrations/instagram/status"),
    connectInstagram: () =>
      apiRequest<{ oauth_url: string }>("/integrations/instagram/connect", { method: "POST" }),
    // Second half of the OAuth flow: the callback lands the browser on
    // /integrations?instagram_pending=<completion_id>.
    completeInstagramConnect: (completionId: string) =>
      apiRequest<InstagramStatus>("/integrations/instagram/complete", {
        method: "POST",
        body: { completion_id: completionId },
      }),
    disconnectInstagram: () =>
      apiRequest<void>("/integrations/instagram/disconnect", { method: "DELETE" }),
    getTelegramStatus: () => apiRequest<TelegramStatus>("/integrations/telegram/status"),
    // 503 when the bot isn't configured on the server.
    connectTelegram: () =>
      apiRequest<{ deep_link: string; expires_at: string }>("/integrations/telegram/connect", { method: "POST" }),
    disconnectTelegram: () =>
      apiRequest<void>("/integrations/telegram/disconnect", { method: "DELETE" }),

    // limit ≤ 100.
    listNotifications: (limit = 50, offset = 0) =>
      apiRequest<NotificationItem[]>(`/notifications?limit=${limit}&offset=${offset}`),
    getUnreadCount: () => apiRequest<{ unread_count: number }>("/notifications/unread-count"),
    markNotificationRead: (id: string) =>
      apiRequest<NotificationItem>(`/notifications/${id}/read`, { method: "PATCH" }),
    markAllNotificationsRead: () =>
      apiRequest<{ marked_read: number }>("/notifications/read-all", { method: "POST" }),
    deleteNotification: (id: string) =>
      apiRequest<void>(`/notifications/${id}`, { method: "DELETE" }),
    deleteReadNotifications: () =>
      apiRequest<void>("/notifications/read", { method: "DELETE" }),
    // A one-minute, stream-only credential: the access token never goes into a URL.
    getStreamTicket: () =>
      apiRequest<{ ticket: string; expires_in: number }>("/notifications/stream-ticket", { method: "POST" }),

    // public_key is "" when push isn't configured on the server.
    getVapidPublicKey: () =>
      apiRequest<{ public_key: string }>("/push/vapid-public-key"),
    getPushStatus: () =>
      apiRequest<{ subscribed: boolean; devices_count: number }>("/push/status"),
    // 503 when push isn't configured, 422 when the endpoint isn't a browser push service.
    subscribePush: (data: { endpoint: string; keys: { p256dh: string; auth: string }; user_agent?: string }) =>
      apiRequest<{ subscribed: boolean; devices_count: number }>("/push/subscribe", { method: "POST", body: data }),
    unsubscribePush: (data: { endpoint: string }) =>
      apiRequest<{ subscribed: boolean; devices_count: number }>("/push/unsubscribe", { method: "POST", body: data }),
    sendTestPush: () =>
      apiRequest<{ sent_count: number }>("/push/test", { method: "POST" }),

    getSandboxState: () =>
      apiRequest<SandboxState>("/ai/sandbox"),
    sendSandboxMessage: (body: { content: string; attachment_url?: string; simulate_telegram?: boolean }) =>
      apiRequest<SandboxTurnResponse>("/ai/sandbox/message", { method: "POST", body }),
    resetSandbox: () =>
      apiRequest<{ status: string; message: string }>("/ai/sandbox/reset", { method: "POST" }),
  };
}
