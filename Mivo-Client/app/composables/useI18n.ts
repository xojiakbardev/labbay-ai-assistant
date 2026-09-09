import { uz } from "../locales/uz";
import { ru } from "../locales/ru";
import { en } from "../locales/en";

export type Locale = "uz" | "ru" | "en";

const dictionaries: Record<Locale, any> = { uz, ru, en };

function getInitialLocale(): Locale {
  if (typeof window !== "undefined") {
    try {
      const saved = localStorage.getItem("mivo_locale") as Locale;
      if (saved && (saved === "uz" || saved === "ru" || saved === "en")) {
        return saved;
      }
    } catch {}
  }
  return "uz";
}

const currentLocale = ref<Locale>(getInitialLocale());

function getNestedValue(obj: any, keys: string[]): string | null {
  let curr = obj;
  for (const key of keys) {
    if (curr && typeof curr === "object" && key in curr) {
      curr = curr[key];
    } else {
      return null;
    }
  }
  return typeof curr === "string" ? curr : null;
}

export function useI18n() {
  onMounted(() => {
    if (import.meta.client) {
      const saved = localStorage.getItem("mivo_locale") as Locale;
      if (saved && (saved === "uz" || saved === "ru" || saved === "en") && currentLocale.value !== saved) {
        currentLocale.value = saved;
      }
    }
  });

  function setLocale(newLocale: Locale) {
    currentLocale.value = newLocale;
    if (import.meta.client) {
      localStorage.setItem("mivo_locale", newLocale);
      document.documentElement.lang = newLocale;
    }
  }

  function t(path: string, params?: Record<string, string | number>): string {
    const active = dictionaries[currentLocale.value] || uz;
    const keys = path.split(".");

    let raw = getNestedValue(active, keys);
    if (raw === null && currentLocale.value !== "en") {
      raw = getNestedValue(en, keys);
    }
    if (raw === null && currentLocale.value !== "uz") {
      raw = getNestedValue(uz, keys);
    }

    if (raw === null) {
      return path;
    }

    if (!params) return raw;
    return raw.replace(/\{(\w+)\}/g, (match, key) => (key in params ? String(params[key]) : match));
  }

  return {
    locale: currentLocale,
    setLocale,
    t,
  };
}
