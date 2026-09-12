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
} from "@lucide/vue";
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
  return "Mivo AI";
});

function isActive(to: string) {
  return to === "/" ? route.path === "/" : route.path.startsWith(to);
}

// The chat page is an app-like screen: exactly one viewport tall, its panes
// scroll inside. Every other page scrolls the document as usual.
const isChat = computed(() => route.path === "/");

const isMobileThreadActive = useState<boolean>("isMobileThreadActive", () => false);

// On a phone an open thread takes the whole screen (its own header has the
// back button), so the app header and tab bar step aside.
const isChatThreadOpenOnMobile = computed(() => isChat.value && (isMobileThreadActive.value || Boolean(route.query.id)));
</script>

<template>
  <main class="flex w-full bg-background text-foreground" :class="isChat ? 'h-dvh overflow-hidden' : 'min-h-dvh'">
    <!-- Top header, phones -->
    <header v-if="!isChatThreadOpenOnMobile" class="mobile-top-header">
      <NuxtLink to="/" class="flex items-center gap-2 min-w-0 rounded-lg">
        <img src="/logo.png" alt="" class="w-8 h-8 object-contain rounded-lg shrink-0" />
        <span class="font-bold text-base text-foreground tracking-tight truncate">Mivo AI</span>
      </NuxtLink>

      <div class="flex items-center gap-1 shrink-0">
        <NotificationBell />

        <Button
          variant="outline"
          size="icon"
          class="h-9 w-9 rounded-xl border-border bg-card hover:bg-muted text-foreground cursor-pointer"
          :title="theme === 'dark' ? t('dashboard.lightMode') : t('dashboard.darkMode')"
          :aria-label="theme === 'dark' ? t('dashboard.lightMode') : t('dashboard.darkMode')"
          @click="toggleTheme"
        >
          <Sun v-if="theme === 'dark'" :size="16" class="text-amber-400" />
          <Moon v-else :size="16" class="text-primary" />
        </Button>

        <LanguageSelect />

        <Button
          variant="ghost"
          size="icon"
          class="h-9 w-9 rounded-xl text-muted-foreground hover:text-destructive hover:bg-destructive/10 shrink-0"
          :title="t('nav.signOut')"
          :aria-label="t('nav.signOut')"
          @click="signOut"
        >
          <LogOut :size="16" />
        </Button>
      </div>
    </header>

    <!-- Sidebar, desktop -->
    <aside class="sidebar">
      <div class="flex items-center gap-3 px-1 py-0.5">
        <img src="/logo.png" alt="" class="w-[38px] h-[38px] object-contain rounded-[10px] shrink-0" />
        <div class="flex flex-col min-w-0">
          <span class="font-bold text-xl leading-tight text-foreground truncate">Mivo AI</span>
          <span class="text-xs text-muted-foreground font-medium truncate">{{ t("dashboard.smartAssistant") }}</span>
        </div>
      </div>

      <nav class="mt-6 flex-1 flex flex-col gap-1" :aria-label="t('dashboard.mainNav')">
        <NuxtLink
          v-for="item in NAV_ITEMS"
          :key="item.to"
          :to="item.to"
          class="nav-link"
          :class="{ active: isActive(item.to) }"
          :aria-current="isActive(item.to) ? 'page' : undefined"
        >
          <component :is="item.component" :size="18" aria-hidden="true" />
          <span>{{ item.label }}</span>
        </NuxtLink>
      </nav>

      <PlanUsageCard compact class="mt-4" />
    </aside>

    <!-- Workspace: desktop header + page -->
    <div class="flex-1 flex flex-col min-w-0 bg-background" :class="{ 'h-dvh overflow-hidden': isChat }">
      <header class="desktop-top-header">
        <h1 class="font-bold text-lg text-foreground tracking-tight m-0">{{ currentPageTitle }}</h1>

        <div class="flex items-center gap-2">
          <NotificationBell />

          <div class="h-4 w-px bg-border my-auto mx-1" aria-hidden="true"></div>

          <Button
            variant="outline"
            size="icon"
            class="h-9 w-9 rounded-xl border-border bg-card hover:bg-muted text-foreground cursor-pointer"
            :title="theme === 'dark' ? t('dashboard.lightMode') : t('dashboard.darkMode')"
            :aria-label="theme === 'dark' ? t('dashboard.lightMode') : t('dashboard.darkMode')"
            @click="toggleTheme"
          >
            <Sun v-if="theme === 'dark'" :size="16" class="text-amber-400" />
            <Moon v-else :size="16" class="text-primary" />
          </Button>

          <LanguageSelect />

          <div class="h-4 w-px bg-border my-auto mx-1" aria-hidden="true"></div>

          <Button
            variant="ghost"
            size="icon"
            class="h-9 w-9 rounded-xl text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors cursor-pointer"
            :title="t('nav.signOut')"
            :aria-label="t('nav.signOut')"
            @click="signOut"
          >
            <LogOut :size="16" />
          </Button>
        </div>
      </header>

      <section class="content" :class="{ 'content-chat': isChat, 'content-thread': isChatThreadOpenOnMobile }">
        <slot />
      </section>
    </div>

    <!-- Tab bar, phones -->
    <nav v-if="!isChatThreadOpenOnMobile" class="mobile-bottom-bar" :aria-label="t('dashboard.mainNav')">
      <NuxtLink
        v-for="item in NAV_ITEMS"
        :key="item.to"
        :to="item.to"
        class="mobile-nav-item"
        :class="{ active: isActive(item.to) }"
        :aria-current="isActive(item.to) ? 'page' : undefined"
      >
        <component :is="item.component" :size="20" aria-hidden="true" />
        <span>{{ item.shortLabel }}</span>
      </NuxtLink>
    </nav>
  </main>
</template>
