import { toast } from "vue-sonner";
import type { Locale } from "./useI18n";

export type ThemeMode = "dark" | "light";
export type ProductsViewMode = "grid" | "table";

const productsViewMode = ref<ProductsViewMode>("grid");
const preferencesLoaded = ref(false);

/** Sign-out: the next account must load its own preferences. */
export function resetUserPreferencesState(): void {
  preferencesLoaded.value = false;
}

export function useUserPreferences() {
  const { theme, applyTheme } = useTheme();
  const { locale, setLocale: baseSetLocale, t } = useI18n();
  const api = useMivoApi();
  const { isAuthenticated, isSuperadmin } = useAuth();

  onMounted(() => {
    const savedView = localStorage.getItem("mivo_products_view_mode") as ProductsViewMode | null;
    if (savedView === "grid" || savedView === "table") {
      productsViewMode.value = savedView;
    }
  });

  async function loadPreferences(): Promise<Record<string, any>> {
    const business = await api.getBusiness();
    preferencesLoaded.value = true;
    return { ...(business.ui_preferences || {}) };
  }

  async function initPreferences() {
    if (preferencesLoaded.value) return;
    let p: Record<string, any>;
    try {
      p = await loadPreferences();
    } catch (err) {
      console.error("[useUserPreferences] Failed to load preferences", err);
      toast.error(t("preferences.loadError"));
      return;
    }
    if (p.theme === "dark" || p.theme === "light") {
      applyTheme(p.theme);
    }
    if (p.locale === "uz" || p.locale === "ru" || p.locale === "en") {
      baseSetLocale(p.locale);
    }
    if (p.products_view_mode === "grid" || p.products_view_mode === "table") {
      productsViewMode.value = p.products_view_mode;
      localStorage.setItem("mivo_products_view_mode", p.products_view_mode);
    }
  }

  // The choice is applied (and kept in localStorage) right away; only the
  // cross-device sync goes to the server, and a failed sync is reported.
  // Preferences live on the business, so there's nothing to sync on the login
  // page or for a superadmin (no business of their own). Only the changed key
  // is sent — the server merges it into the stored preferences, so two quick
  // changes can't overwrite each other.
  async function savePreference(key: string, value: string) {
    if (!isAuthenticated.value || isSuperadmin.value !== false) return;
    try {
      await api.updateBusiness({ ui_preferences: { [key]: value } });
    } catch (err) {
      console.error("[useUserPreferences] Failed to save preference", key, err);
      toast.error(t("preferences.saveError"));
    }
  }

  async function setTheme(newTheme: ThemeMode) {
    applyTheme(newTheme);
    await savePreference("theme", newTheme);
  }

  async function toggleTheme() {
    const next = theme.value === "dark" ? "light" : "dark";
    await setTheme(next);
  }

  async function setLocale(newLocale: Locale) {
    baseSetLocale(newLocale);
    await savePreference("locale", newLocale);
  }

  async function setProductsViewMode(mode: ProductsViewMode) {
    productsViewMode.value = mode;
    localStorage.setItem("mivo_products_view_mode", mode);
    await savePreference("products_view_mode", mode);
  }

  return {
    theme,
    locale,
    productsViewMode,
    initPreferences,
    setTheme,
    toggleTheme,
    setLocale,
    setProductsViewMode,
  };
}
