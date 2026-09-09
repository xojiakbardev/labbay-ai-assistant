<script setup lang="ts">
import { z } from "zod";
import { ApiError } from "~/composables/useApi";
import type { Locale } from "~/composables/useI18n";

definePageMeta({ layout: "default" });

const { signIn, isSuperadmin } = useAuth();
const { t } = useI18n();

const loginKey = ref("");
const password = ref("");
const error = ref<string | null>(null);
const loading = ref(false);

// Login is whatever identifier was set for this account (username, phone, or key)
const loginSchema = computed(() =>
  z.object({
    loginKey: z.string().min(1, t("auth.loginRequired") || t("auth.emailRequired")),
    password: z.string().min(1, t("auth.passwordRequired")),
  })
);

async function onSubmit() {
  error.value = null;

  const result = loginSchema.value.safeParse({ loginKey: loginKey.value, password: password.value });
  if (!result.success) {
    error.value = result.error.issues[0]?.message ?? (t("auth.loginInvalid") || t("auth.emailInvalid"));
    return;
  }

  loading.value = true;
  try {
    await signIn(result.data.loginKey, result.data.password);
    await navigateTo(isSuperadmin.value ? "/superadmin" : "/");
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : t("auth.loginFailed");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="flex min-h-screen items-center justify-center bg-background p-4">
    <Card class="w-full max-w-sm border-border/80 shadow-lg">
      <CardHeader class="pb-4">
        <div class="flex items-center justify-between gap-3">
          <div class="flex items-center gap-2.5">
            <img src="/logo.png" alt="Mivo AI" class="h-10 w-10 rounded-xl object-contain shadow-xs" />
            <CardTitle class="text-xl font-bold tracking-tight text-foreground">{{ t("auth.login") }}</CardTitle>
          </div>

          <LanguageSelect />
        </div>
      </CardHeader>

      <CardContent class="pt-2">
        <form class="space-y-4" @submit.prevent="onSubmit">
          <div class="space-y-1.5">
            <Label for="login-key" class="text-xs font-medium text-foreground/80">{{ t("auth.loginPlaceholder") || t("auth.emailPlaceholder") }}</Label>
            <Input
              id="login-key"
              v-model="loginKey"
              type="text"
              autocomplete="username"
              required
              class="h-10 bg-background"
            />
          </div>

          <div class="space-y-1.5">
            <Label for="password" class="text-xs font-medium text-foreground/80">{{ t("auth.passwordPlaceholder") }}</Label>
            <Input
              id="password"
              v-model="password"
              type="password"
              autocomplete="current-password"
              required
              class="h-10 bg-background"
            />
          </div>

          <p v-if="error" class="text-xs font-medium text-destructive bg-destructive/10 p-2.5 rounded-lg border border-destructive/20 animate-in fade-in duration-200">
            {{ error }}
          </p>

          <Button type="submit" class="w-full h-10 mt-2 font-medium cursor-pointer shadow-xs" :disabled="loading">
            {{ loading ? "…" : t("auth.login") }}
          </Button>
        </form>
      </CardContent>
    </Card>
  </div>
</template>
