import type { Locale } from "~/composables/useI18n";

// Chromium/Node's ICU data for "uz-UZ" doesn't reliably spell out month
// names via Intl.DateTimeFormat (short month comes back as "M08" instead of
// "avg") — so month names are hand-maintained here instead of relying on
// toLocaleDateString for anything that needs to read as a real date.
const MONTHS_FULL: Record<Locale, string[]> = {
  uz: ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentyabr", "oktyabr", "noyabr", "dekabr"],
  ru: ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"],
  en: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
};

const MONTHS_SHORT: Record<Locale, string[]> = {
  uz: ["yan", "fev", "mar", "apr", "may", "iyun", "iyul", "avg", "sen", "okt", "noy", "dek"],
  ru: ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"],
  en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
};

/** "4-avgust 2026" / "4 августа 2026" / "August 4, 2026" */
export function formatFullDate(iso: string | null, locale: Locale): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const day = d.getDate();
  const month = MONTHS_FULL[locale][d.getMonth()];
  const year = d.getFullYear();
  if (locale === "uz") return `${day}-${month} ${year}`;
  if (locale === "ru") return `${day} ${month} ${year}`;
  return `${month} ${day}, ${year}`;
}

/** Short day label for dense chart axes: "4-avg" / "4 авг" / "Aug 4" */
export function formatShortDay(iso: string, locale: Locale): string {
  const d = new Date(iso);
  const day = d.getDate();
  const month = MONTHS_SHORT[locale][d.getMonth()];
  return locale === "en" ? `${month} ${day}` : `${day}-${month}`;
}

/** "YYYY-MM" period -> "Avgust '26" / "Август '26" / "Aug '26" */
export function formatShortMonth(period: string, locale: Locale): string {
  const [y, m] = period.split("-").map(Number);
  const month = m ? MONTHS_SHORT[locale][m - 1] : undefined;
  if (!month) return period;
  const label = month.charAt(0).toUpperCase() + month.slice(1);
  return `${label} '${String(y).slice(2)}`;
}
