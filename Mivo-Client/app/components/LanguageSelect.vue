<script setup lang="ts">
import { computed } from "vue";
import type { Locale } from "~/composables/useI18n";
import { useUserPreferences } from "~/composables/useUserPreferences";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "~/components/ui/select";

const props = defineProps<{
  class?: string;
  triggerClass?: string;
  align?: "start" | "center" | "end";
}>();

const { locale, setLocale } = useUserPreferences();

interface LanguageOption {
  value: Locale;
  code: string;
  label: string;
  flag: string;
}

const DEFAULT_LANG: LanguageOption = {
  value: "uz",
  code: "UZ",
  label: "O'zbekcha",
  flag: "/flags/uz.svg",
};

const LANGUAGES: LanguageOption[] = [
  DEFAULT_LANG,
  { value: "ru", code: "RU", label: "Русский", flag: "/flags/ru.svg" },
  { value: "en", code: "EN", label: "English", flag: "/flags/en.svg" },
];

const currentLang = computed<LanguageOption>(() => {
  return LANGUAGES.find((l) => l.value === locale.value) ?? DEFAULT_LANG;
});

function onSelect(val: any) {
  if (typeof val === "string" && (val === "uz" || val === "ru" || val === "en")) {
    setLocale(val as Locale);
  }
}
</script>

<template>
  <div :class="['shrink-0 inline-flex', props.class]">
    <Select :model-value="locale" @update:model-value="onSelect">
      <SelectTrigger
        :class="[
          '!h-9 !px-2 !gap-1.5 text-xs font-semibold rounded-xl border border-border bg-card hover:bg-muted text-foreground transition-all shadow-2xs cursor-pointer',
          props.triggerClass,
        ]"
      >
        <div class="flex items-center gap-1.5 min-w-0">
          <img
            :src="currentLang.flag"
            :alt="currentLang.label"
            class="w-5 h-5 rounded-full object-cover shrink-0 shadow-2xs border border-black/10 dark:border-white/10"
          />
          <span class="font-bold text-xs text-foreground uppercase tracking-wide">
            {{ currentLang.code }}
          </span>
        </div>
      </SelectTrigger>
      <SelectContent :align="align || 'end'" class="min-w-[130px]">
        <SelectItem
          v-for="item in LANGUAGES"
          :key="item.value"
          :value="item.value"
          class="text-xs font-medium cursor-pointer py-1.5 px-2"
        >
          <div class="flex items-center gap-2">
            <img
              :src="item.flag"
              :alt="item.label"
              class="w-5 h-5 rounded-full object-cover shrink-0 shadow-2xs border border-black/10 dark:border-white/10"
            />
            <span class="font-medium text-foreground">{{ item.label }}</span>
          </div>
        </SelectItem>
      </SelectContent>
    </Select>
  </div>
</template>
