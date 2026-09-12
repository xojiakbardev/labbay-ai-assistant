<script setup lang="ts">
import { LayoutDashboard, Building2, Tags, LogOut, Sun, Moon } from "@lucide/vue";
import { useTheme } from "~/composables/useTheme";

const { signOut } = useAuth();
const route = useRoute();
const { t } = useI18n();
const { theme, toggleTheme } = useTheme();

const NAV_ITEMS = computed(() => [
  { to: "/superadmin", label: t("superadmin.nav.dashboard"), shortLabel: t("superadmin.nav.dashboard"), component: LayoutDashboard },
  { to: "/superadmin/businesses", label: t("superadmin.nav.businesses"), shortLabel: t("superadmin.nav.businesses"), component: Building2 },
  { to: "/superadmin/plans", label: t("superadmin.nav.plans"), shortLabel: t("superadmin.nav.plans"), component: Tags },
]);

function isActive(to: string) {
  return to === "/superadmin" ? route.path === "/superadmin" : route.path.startsWith(to);
}
</script>

<template>
  <main class="flex w-full min-h-screen bg-background text-foreground">
    <header class="mobile-top-header">
      <div class="flex items-center gap-2 min-w-0">
        <img src="/logo.png" alt="Mivo AI Logo" class="w-8 h-8 object-contain rounded-lg shrink-0" />
        <span class="font-bold text-base text-foreground tracking-tight truncate">Mivo Admin</span>
      </div>
      <div class="flex items-center gap-1.5 shrink-0">
        <Button variant="ghost" size="icon" class="h-8 w-8 text-foreground shrink-0" @click="toggleTheme">
          <Sun v-if="theme === 'dark'" :size="15" class="text-amber-400" />
          <Moon v-else :size="15" class="text-primary" />
        </Button>
        <Button variant="ghost" size="icon" class="h-8 w-8 text-muted-foreground hover:text-destructive shrink-0" :title="t('nav.signOut')" @click="signOut">
          <LogOut :size="15" />
        </Button>
      </div>
    </header>

    <aside class="sidebar">
      <div class="flex items-center gap-3">
        <img src="/logo.png" alt="Mivo AI Logo" class="w-[38px] h-[38px] object-contain rounded-[10px]" />
        <div class="leading-tight">
          <div class="font-bold text-xl text-foreground">Mivo AI</div>
          <div class="text-[0.7rem] font-semibold text-primary uppercase tracking-wide">Superadmin</div>
        </div>
      </div>

      <nav class="mt-6 flex-1 flex flex-col gap-0.5">
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

      <div class="pt-4 border-t border-border">
        <Button
          variant="outline"
          class="w-full mb-2 gap-2 border-border text-muted-foreground hover:text-foreground"
          size="sm"
          @click="toggleTheme"
        >
          <Sun v-if="theme === 'dark'" :size="15" class="text-amber-400" />
          <Moon v-else :size="15" class="text-primary" />
          <span>{{ theme === 'dark' ? t("dashboard.lightMode") : t("dashboard.darkMode") }}</span>
        </Button>

        <LanguageSelect class="w-full mb-2" trigger-class="w-full justify-between" align="start" />

        <Button
          variant="ghost"
          class="w-full gap-2 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
          @click="signOut"
        >
          <LogOut :size="16" />
          <span>{{ t("nav.signOut") }}</span>
        </Button>
      </div>
    </aside>

    <section class="content">
      <slot />
    </section>

    <nav class="mobile-bottom-bar">
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
