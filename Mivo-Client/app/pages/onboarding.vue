<script setup lang="ts">
import { Instagram, Send, Package, Sparkles, ArrowRight } from "lucide-vue-next";

definePageMeta({ layout: "default" });

const STEPS = [
  { to: "/integrations", label: "Connect Instagram", detail: "Let customers DM your business.", icon: Instagram },
  { to: "/integrations", label: "Connect Telegram", detail: "So you get notified about hot leads.", icon: Send },
  { to: "/products", label: "Add your products", detail: "Manually, or paste text for AI import.", icon: Package },
  { to: "/ai-settings", label: "Configure your AI selling style", detail: "Tone, rules, policies.", icon: Sparkles },
];
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-background p-4">
    <Card class="w-full max-w-lg p-8">
      <div class="mb-6 flex flex-col items-center text-center">
        <img src="/logo.png" alt="Mivo AI" class="mb-3 h-11 w-11 rounded-xl object-contain" />
        <h1 class="text-xl font-bold text-foreground">Welcome to Mivo AI</h1>
        <p class="mt-2 text-sm text-muted-foreground leading-relaxed">
          Connect Instagram, add your products, tell Mivo AI how you sell — and it handles your
          incoming customers.
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
          <span>Go to dashboard</span>
          <ArrowRight :size="16" />
        </Button>
      </NuxtLink>
    </Card>
  </div>
</template>
