<script setup lang="ts">
import { Package, Sparkles, ArrowRight } from "@lucide/vue";
import InstagramIcon from "~/components/InstagramIcon.vue";
import TelegramIcon from "~/components/TelegramIcon.vue";

definePageMeta({ layout: "default" });

const { t } = useI18n();

// Lucide dropped brand icons in v1 — Instagram/Telegram use the local ones.
const STEPS = computed(() => [
  { to: "/integrations", label: t("onboarding.stepInstagram"), detail: t("onboarding.stepInstagramDetail"), icon: InstagramIcon },
  { to: "/integrations", label: t("onboarding.stepTelegram"), detail: t("onboarding.stepTelegramDetail"), icon: TelegramIcon },
  { to: "/products", label: t("onboarding.stepProducts"), detail: t("onboarding.stepProductsDetail"), icon: Package },
  { to: "/ai-settings", label: t("onboarding.stepAi"), detail: t("onboarding.stepAiDetail"), icon: Sparkles },
]);
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-background p-4">
    <Card class="w-full max-w-lg p-8">
      <div class="mb-6 flex flex-col items-center text-center">
        <img src="/logo.png" alt="Mivo AI" class="mb-3 h-11 w-11 rounded-xl object-contain" />
        <h1 class="text-xl font-bold text-foreground">{{ t("onboarding.title") }}</h1>
        <p class="mt-2 text-sm text-muted-foreground leading-relaxed">
          {{ t("onboarding.subtitle") }}
        </p>
      </div>

      <ol class="flex flex-col gap-2">
        <li v-for="step in STEPS" :key="step.label">
          <NuxtLink
            :to="step.to"
            class="flex items-center gap-3 rounded-lg border border-border p-3 transition-colors hover:bg-accent"
          >
            <div class="card-icon bg-primary/10 text-primary shrink-0">
              <component :is="step.icon" :size="18" />
            </div>
            <div class="min-w-0">
              <div class="text-sm font-semibold text-foreground">{{ step.label }}</div>
              <div class="text-xs text-muted-foreground">{{ step.detail }}</div>
            </div>
          </NuxtLink>
        </li>
      </ol>

      <NuxtLink v-slot="{ navigate }" to="/" custom>
        <Button class="mt-6 w-full gap-2" @click="navigate">
          <span>{{ t("onboarding.goToDashboard") }}</span>
          <ArrowRight :size="16" />
        </Button>
      </NuxtLink>
    </Card>
  </div>
</template>
