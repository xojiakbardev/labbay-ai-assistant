import type { Locale } from "./useI18n";

export type ThemeMode = "dark" | "light";
export type ProductsViewMode = "grid" | "table";

const productsViewMode = ref<ProductsViewMode>("grid");
const preferencesLoaded = ref(false);

export function useUserPreferences() {
  const { theme, applyTheme, toggleTheme: baseToggleTheme } = useTheme();
  const { locale, setLocale: baseSetLocale } = useI18n();
  const api = useMivoApi();

  onMounted(() => {
    const savedView = localStorage.getItem("mivo_products_view_mode") as ProductsViewMode | null;
    if (savedView === "grid" || savedView === "table") {
      productsViewMode.value = savedView;
    }
  });

  async function initPreferences() {
    if (preferencesLoaded.value) return;
    try {
      const business = await api.getBusiness();
      if (business && business.ui_preferences) {
        const p = business.ui_preferences;
        if (p.theme && (p.theme === "dark" || p.theme === "light")) {
          applyTheme(p.theme);
        }
        if (p.locale && (p.locale === "uz" || p.locale === "ru" || p.locale === "en")) {
          baseSetLocale(p.locale);
        }
        if (p.products_view_mode && (p.products_view_mode === "grid" || p.products_view_mode === "table")) {
          productsViewMode.value = p.products_view_mode;
          localStorage.setItem("mivo_products_view_mode", p.products_view_mode);
        }
      }
      preferencesLoaded.value = true;
    } catch {
      // Ignored if not logged in or network fails
    }
  }

  async function setTheme(newTheme: ThemeMode) {
    applyTheme(newTheme);
    try {
      await api.updateBusiness({
        ui_preferences: { theme: newTheme },
      });
    } catch {}
  }

  async function toggleTheme() {
    const next = theme.value === "dark" ? "light" : "dark";
    await setTheme(next);
  }

  async function setLocale(newLocale: Locale) {
    baseSetLocale(newLocale);
    try {
      await api.updateBusiness({
        ui_preferences: { locale: newLocale },
      });
    } catch {}
  }

  async function setProductsViewMode(mode: ProductsViewMode) {
    productsViewMode.value = mode;
    localStorage.setItem("mivo_products_view_mode", mode);
    try {
      await api.updateBusiness({
        ui_preferences: { products_view_mode: mode },
      });
    } catch {}
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
