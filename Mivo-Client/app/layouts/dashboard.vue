<script setup lang="ts">
import {
  MessageSquare,
  Flame,
  Package,
  Sparkles,
  FlaskConical,
  Share2,
  LogOut,
  Sun,
  Moon
} from "lucide-vue-next";
import type { Locale } from "~/composables/useI18n";
import { useUserPreferences } from "~/composables/useUserPreferences";

const { signOut } = useAuth();
const route = useRoute();
const { t } = useI18n();
const { theme, toggleTheme, initPreferences } = useUserPreferences();

onMounted(() => {
  initPreferences();
});

const NAV_ITEMS = computed(() => [
  { to: "/", label: t("nav.conversations"), shortLabel: t("nav.conversationsShort"), component: MessageSquare },
  { to: "/leads", label: t("nav.leads"), shortLabel: t("nav.leadsShort"), component: Flame },
  { to: "/products", label: t("nav.products"), shortLabel: t("nav.productsShort"), component: Package },
  { to: "/ai-settings", label: t("nav.aiSettings"), shortLabel: t("nav.aiSettingsShort"), component: Sparkles },
  { to: "/sandbox", label: t("nav.sandbox"), shortLabel: t("nav.sandboxShort"), component: FlaskConical },
  { to: "/integrations", label: t("nav.integrations"), shortLabel: t("nav.integrationsShort"), component: Share2 },
]);

const currentPageTitle = computed(() => {
  if (route.path === "/") return t("nav.conversations");
  if (route.path.startsWith("/leads")) return t("nav.leads");
  if (route.path.startsWith("/products")) return t("nav.products");
  if (route.path.startsWith("/ai-settings")) return t("nav.aiSettings");
  if (route.path.startsWith("/sandbox")) return t("nav.sandbox");
  if (route.path.startsWith("/integrations")) return t("nav.integrations");
  if (route.path.startsWith("/notifications")) return t("nav.notifications");
  if (route.path.startsWith("/superadmin")) return "SuperAdmin";
  return "Mivo AI";
});


function isActive(to: string) {
  return to === "/" ? route.path === "/" : route.path.startsWith(to);
}

const isMobileThreadActive = useState<boolean>("isMobileThreadActive", () => false);

const isChatThreadOpenOnMobile = computed(() => {
  const isHome = route.path === "/" || route.path === "";
  return isHome && (isMobileThreadActive.value || Boolean(route.query.id));
});
</script>

<template>
  <main class="flex w-full min-h-screen bg-background text-foreground">
    <!-- Top Mobile Header -->
    <header
      class="mobile-top-header"
      :class="{ '!hidden': isChatThreadOpenOnMobile, 'chat-open': isChatThreadOpenOnMobile }"
      :style="isChatThreadOpenOnMobile ? 'display: none !important;' : ''"
    >
      <div class="flex items-center gap-2 min-w-0">
        <img src="/logo.png" alt="Mivo AI Logo" class="w-8 h-8 object-contain rounded-lg shrink-0" />
        <span class="font-bold text-base text-foreground tracking-tight truncate">Mivo AI</span>
      </div>

      <div class="flex items-center gap-1 sm:gap-1.5 shrink-0">
        <!-- Notification Bell -->
        <NotificationBell />

        <!-- Light / Dark Mode Switcher -->
        <Button
          variant="outline"
          size="icon"
          class="h-9 w-9 rounded-xl border-border bg-card hover:bg-muted text-foreground transition-all cursor-pointer shadow-2xs"
          :title="theme === 'dark' ? t('dashboard.lightMode') : t('dashboard.darkMode')"
          @click="toggleTheme"
        >
          <Sun v-if="theme === 'dark'" :size="15" class="text-amber-400" />
          <Moon v-else :size="15" class="text-primary" />
        </Button>

        <!-- Language Switcher -->
        <LanguageSelect />

        <Button variant="ghost" size="icon" class="h-8 w-8 text-muted-foreground hover:text-destructive shrink-0" :title="t('nav.signOut')" @click="signOut">
          <LogOut :size="15" />
        </Button>
      </div>
    </header>

    <!-- Desktop Sidebar (hidden on mobile) -->
    <aside class="sidebar">
      <!-- Logo / Brand Title -->
      <div class="flex items-center gap-3 px-1 py-0.5">
        <img src="/logo.png" alt="Mivo AI Logo" class="w-[38px] h-[38px] object-contain rounded-[10px] shrink-0" />
        <div class="flex flex-col min-w-0">
          <span class="font-bold text-xl leading-tight text-foreground truncate">Mivo AI</span>
          <span class="text-[11px] text-muted-foreground font-medium truncate">{{ t("dashboard.smartAssistant") }}</span>
        </div>
      </div>

      <!-- Navigation Links -->
      <nav class="mt-6 flex-1 flex flex-col gap-1">
        <NuxtLink
          v-for="item in NAV_ITEMS"
          :key="item.to"
          :to="item.to"
          class="nav-link"
          :class="{ active: isActive(item.to) }"
        >
          <component :is="item.component" :size="18" />
          <span>{{ item.label }}</span>
        </NuxtLink>
      </nav>

      <!-- Clean Sidebar Footer -->
      <div class="pt-4 border-t border-border flex items-center justify-between text-xs text-muted-foreground px-2">
        <div class="flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
          <span class="font-medium text-foreground text-xs">{{ t("dashboard.systemActive") }}</span>
        </div>
        <span class="text-[11px] text-muted-foreground/80 font-mono">v2.1</span>
      </div>
    </aside>

    <!-- Main Workspace Area: Desktop Top Navbar + Page Content -->
    <div
      class="flex-1 flex flex-col min-w-0 bg-background"
      :class="route.path === '/' ? 'h-screen max-h-screen overflow-hidden' : 'min-h-screen'"
    >
      <!-- Desktop Top Navbar (Hidden on mobile) -->
      <header class="desktop-top-header">
        <!-- Current Page Title -->
        <div class="flex items-center gap-3 min-w-0">
          <span class="font-bold text-lg text-foreground tracking-tight">{{ currentPageTitle }}</span>
        </div>

        <!-- Right Side Controls: Notifications, Theme, Language, Sign Out -->
        <div class="flex items-center gap-2">
          <!-- Notification Bell -->
          <NotificationBell />

          <div class="h-4 w-px bg-border my-auto mx-1"></div>

          <!-- Light / Dark Mode Switcher -->
          <Button
            variant="outline"
            size="icon"
            class="h-9 w-9 rounded-xl border-border bg-card hover:bg-muted text-foreground transition-all cursor-pointer shadow-2xs"
            :title="theme === 'dark' ? t('dashboard.lightMode') : t('dashboard.darkMode')"
            @click="toggleTheme"
          >
            <Sun v-if="theme === 'dark'" :size="16" class="text-amber-400" />
            <Moon v-else :size="16" class="text-primary" />
          </Button>

          <!-- Language Switcher -->
          <LanguageSelect />

          <div class="h-4 w-px bg-border my-auto mx-1"></div>

          <!-- Sign Out Button -->
          <Button
            variant="ghost"
            size="icon"
            class="h-9 w-9 rounded-xl text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors cursor-pointer"
            :title="t('nav.signOut')"
            @click="signOut"
          >
            <LogOut :size="16" />
          </Button>
        </div>
      </header>

      <!-- Page Content -->
      <section
        class="content flex-1"
        :class="{
          '!p-0 overflow-hidden h-[calc(100vh-3.5rem)] max-h-[calc(100vh-3.5rem)] min-h-0 flex flex-col': route.path === '/' && !isChatThreadOpenOnMobile,
          '!p-0 overflow-hidden h-dvh max-h-dvh min-h-0 flex flex-col': isChatThreadOpenOnMobile
        }"
      >
        <slot />
      </section>
    </div>

    <!-- Mobile Fixed Bottom Navigation Bar -->
    <nav
      class="mobile-bottom-bar"
      :class="{ '!hidden': isChatThreadOpenOnMobile, 'chat-open': isChatThreadOpenOnMobile }"
      :style="isChatThreadOpenOnMobile ? 'display: none !important;' : ''"
    >
      <NuxtLink
        v-for="item in NAV_ITEMS"
        :key="item.to"
        :to="item.to"
        class="mobile-nav-item"
        :class="{ active: isActive(item.to) }"
      >
        <component :is="item.component" :size="20" />
        <span>{{ item.shortLabel }}</span>
      </NuxtLink>
    </nav>
  </main>
</template>
