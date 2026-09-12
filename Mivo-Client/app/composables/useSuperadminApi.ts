import type { Plan, PlanInput, RevenuePoint, SuperadminBusiness, SuperadminStats, UsagePoint } from "~/types/api";

export function useSuperadminApi() {
  const { apiRequest } = useApi();

  return {
    listBusinesses: () => apiRequest<SuperadminBusiness[]>("/superadmin/businesses"),
    // trial_days: how long the subscription runs from today. plan_id null =
    // no plan (unlimited); left out = the default plan.
    createBusiness: (body: {
      email: string;
      password: string;
      business_name: string;
      trial_days?: number;
      plan_id?: string | null;
    }) => apiRequest<{ access_token: string }>("/superadmin/businesses", { method: "POST", body }),
    // The plan starts again today for `months`; the payment is recorded.
    renewPlan: (
      businessId: string,
      body: {
        plan_id: string | null;
        months: number;
        payment_amount?: number | null;
        payment_currency?: string;
        payment_note?: string | null;
      }
    ) => apiRequest<SuperadminBusiness>(`/superadmin/businesses/${businessId}/renew`, { method: "POST", body }),
    // The platform kill switch — separate from the owner's own ai_enabled,
    // which the superadmin can only see.
    setAiSuspended: (businessId: string, ai_suspended: boolean) =>
      apiRequest<SuperadminBusiness>(`/superadmin/businesses/${businessId}/ai`, {
        method: "PATCH",
        body: { ai_suspended },
      }),
    deleteBusiness: (businessId: string) =>
      apiRequest<void>(`/superadmin/businesses/${businessId}`, { method: "DELETE" }),

    listPlans: () => apiRequest<Plan[]>("/superadmin/plans"),
    createPlan: (body: PlanInput) => apiRequest<Plan>("/superadmin/plans", { method: "POST", body }),
    updatePlan: (planId: string, body: Partial<PlanInput>) =>
      apiRequest<Plan>(`/superadmin/plans/${planId}`, { method: "PATCH", body }),
    // 409 while a business is on it.
    deletePlan: (planId: string) => apiRequest<void>(`/superadmin/plans/${planId}`, { method: "DELETE" }),

    getStats: () => apiRequest<SuperadminStats>("/superadmin/stats"),
    getUsageTimeseries: (days = 30) => apiRequest<UsagePoint[]>(`/superadmin/usage-timeseries?days=${days}`),
    getRevenueTimeseries: (months = 6) => apiRequest<RevenuePoint[]>(`/superadmin/revenue-timeseries?months=${months}`),
  };
}
