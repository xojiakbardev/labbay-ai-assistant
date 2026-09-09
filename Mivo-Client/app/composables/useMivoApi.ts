import type {
  AiFeedback,
  Business,
  ConversationDetail,
  ConversationSummary,
  FeedbackCreateInput,
  Lead,
  NotificationItem,
  Product,
  SandboxState,
  SandboxTurnResponse,
} from "~/types/api";


export function useMivoApi() {
  const { apiRequest } = useApi();

  return {
    getBusiness: () => apiRequest<Business>("/business"),
    updateBusiness: (patch: Partial<Business>) =>
      apiRequest<Business>("/business", { method: "PATCH", body: patch }),

    listProducts: () => apiRequest<Product[]>("/products"),
    getProduct: (id: string) => apiRequest<Product>(`/products/${id}`),
    createProduct: (product: Partial<Product>) =>
      apiRequest<Product>("/products", { method: "POST", body: product }),
    updateProduct: (id: string, patch: Partial<Product>) =>
      apiRequest<Product>(`/products/${id}`, { method: "PATCH", body: patch }),
    deleteProduct: (id: string) => apiRequest<void>(`/products/${id}`, { method: "DELETE" }),
    uploadProductImage: (id: string, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return apiRequest(`/products/${id}/images`, { method: "POST", body: form, isForm: true });
    },

    previewImport: (text: string) =>
      apiRequest<{ products: Partial<Product>[] }>("/products/import/preview", {
        method: "POST",
        body: { text },
      }),
    confirmImport: (products: Partial<Product>[]) =>
      apiRequest<Product[]>("/products/import/confirm", { method: "POST", body: { products } }),

    listLeads: () => apiRequest<Lead[]>("/leads"),
    getLead: (id: string) => apiRequest<Lead>(`/leads/${id}`),

    listConversations: () => apiRequest<ConversationSummary[]>("/conversations"),
    getConversation: (id: string) => apiRequest<ConversationDetail>(`/conversations/${id}`),
    sendConversationReply: (id: string, content: string) =>
      apiRequest<any>(`/conversations/${id}/reply`, {
        method: "POST",
        body: { content },
      }),
    updateConversationStatus: (id: string, status: string) =>
      apiRequest<ConversationSummary>(`/conversations/${id}/status?status_value=${status}`, {
        method: "PATCH",
      }),
    deleteConversation: (id: string) =>
      apiRequest<void>(`/conversations/${id}`, { method: "DELETE" }),
    updateCustomer: (id: string, patch: { username?: string; phone?: string }) =>
      apiRequest<any>(`/customers/${id}`, {
        method: "PATCH",
        body: patch,
      }),

    submitAiFeedback: (input: FeedbackCreateInput) =>
      apiRequest<AiFeedback>("/ai/feedback", { method: "POST", body: input }),
    getLearnedRules: () => apiRequest<AiFeedback[]>("/ai/learned-rules"),
    deleteLearnedRule: (id: string) =>
      apiRequest<void>(`/ai/learned-rules/${id}`, { method: "DELETE" }),

    getInstagramStatus: () =>
      apiRequest<{ connected: boolean; username?: string; expires_at?: string }>(
        "/integrations/instagram/status"
      ),
    connectInstagram: () =>
      apiRequest<{ oauth_url: string }>("/integrations/instagram/connect", { method: "POST" }),
    disconnectInstagram: () =>
      apiRequest<void>("/integrations/instagram/disconnect", { method: "DELETE" }),
    getTelegramStatus: () =>
      apiRequest<{ connected: boolean; username?: string; chat_id?: string }>(
        "/integrations/telegram/status"
      ),
    connectTelegram: () =>
      apiRequest<{ deep_link: string; expires_at: string }>("/integrations/telegram/connect", { method: "POST" }),
    disconnectTelegram: () =>
      apiRequest<void>("/integrations/telegram/disconnect", { method: "DELETE" }),

    listNotifications: () => apiRequest<NotificationItem[]>("/notifications"),
    getUnreadCount: () => apiRequest<{ unread_count: number }>("/notifications/unread-count"),
    markNotificationRead: (id: string) =>
      apiRequest<NotificationItem>(`/notifications/${id}/read`, { method: "PATCH" }),
    markAllNotificationsRead: () =>
      apiRequest<{ marked_read: number }>("/notifications/read-all", { method: "POST" }),
    deleteNotification: (id: string) =>
      apiRequest<void>(`/notifications/${id}`, { method: "DELETE" }),
    deleteReadNotifications: () =>
      apiRequest<void>("/notifications/read", { method: "DELETE" }),

    getVapidPublicKey: () =>
      apiRequest<{ public_key: string }>("/push/vapid-public-key"),
    getPushStatus: () =>
      apiRequest<{ subscribed: boolean; devices_count: number }>("/push/status"),
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

