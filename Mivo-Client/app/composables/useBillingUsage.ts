import type { BillingUsage } from "~/types/api";

/** The owner's plan and this month's AI replies, shared by every card that
 * shows it (sidebar, AI settings page). */
export function useBillingUsage() {
  const usage = useState<BillingUsage | null>("billingUsage", () => null);
  const api = useMivoApi();

  async function refresh() {
    try {
      usage.value = await api.getBillingUsage();
    } catch (err) {
      // Informational only: the card stays hidden rather than showing stale numbers.
      console.warn("[billing] usage unavailable", err);
      usage.value = null;
    }
  }

  return { usage, refresh };
}
