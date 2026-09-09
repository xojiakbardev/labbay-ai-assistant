import type { RevenuePoint, SuperadminBusiness, SuperadminStats, UsagePoint } from "~/types/api";

export function useSuperadminApi() {
  const { apiRequest } = useApi();

  return {
    listBusinesses: () => apiRequest<SuperadminBusiness[]>("/superadmin/businesses"),
    createBusiness: (body: { email: string; password: string; business_name: string; trial_days?: number }) =>
      apiRequest<{ access_token: string }>("/superadmin/businesses", { method: "POST", body }),
    extendSubscription: (
      businessId: string,
      body: {
        subscription_expires_at: string;
        payment_amount?: number | null;
        payment_currency?: string;
        payment_note?: string | null;
      }
    ) =>
      apiRequest<SuperadminBusiness>(`/superadmin/businesses/${businessId}/subscription`, {
        method: "PATCH",
        body,
      }),
    setAiEnabled: (businessId: string, ai_enabled: boolean) =>
      apiRequest<SuperadminBusiness>(`/superadmin/businesses/${businessId}/ai`, {
        method: "PATCH",
        body: { ai_enabled },
      }),
    deleteBusiness: (businessId: string) =>
      apiRequest<void>(`/superadmin/businesses/${businessId}`, { method: "DELETE" }),

    getStats: () => apiRequest<SuperadminStats>("/superadmin/stats"),
    getUsageTimeseries: (days = 30) => apiRequest<UsagePoint[]>(`/superadmin/usage-timeseries?days=${days}`),
    getRevenueTimeseries: (months = 6) => apiRequest<RevenuePoint[]>(`/superadmin/revenue-timeseries?months=${months}`),
  };
}
