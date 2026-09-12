<script setup lang="ts">
import { X } from "@lucide/vue";

const props = withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    description?: string;
  }>(),
  {
    open: false,
    description: "",
  }
);

const emit = defineEmits<{
  (e: "update:open", val: boolean): void;
  (e: "close"): void;
}>();

const { t } = useI18n();
const panelRef = ref<HTMLElement | null>(null);
const titleId = `drawer-title-${Math.random().toString(36).slice(2, 8)}`;

function close() {
  emit("update:open", false);
  emit("close");
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") close();
}

// Body scroll lock + Escape to close + focus moves into the sheet.
watch(
  () => props.open,
  (val) => {
    if (typeof document === "undefined") return;
    if (val) {
      document.body.style.overflow = "hidden";
      document.addEventListener("keydown", onKeydown);
      nextTick(() => panelRef.value?.focus());
    } else {
      document.body.style.overflow = "";
      document.removeEventListener("keydown", onKeydown);
    }
  },
  { immediate: true }
);

onUnmounted(() => {
  if (typeof document !== "undefined") {
    document.body.style.overflow = "";
    document.removeEventListener("keydown", onKeydown);
  }
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex flex-col justify-end sm:justify-center sm:items-center bg-black/60 backdrop-blur-xs transition-opacity p-0 sm:p-4"
      @click.self="close"
    >
      <!-- Sheet Panel -->
      <div
        ref="panelRef"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        tabindex="-1"
        class="bg-card text-card-foreground border-t sm:border border-border rounded-t-3xl sm:rounded-2xl shadow-2xl w-full sm:max-w-md max-h-[85dvh] sm:max-h-[90dvh] flex flex-col overflow-hidden animate-in slide-in-from-bottom sm:zoom-in-95 duration-200 ease-out outline-none"
        @click.stop
      >
        <!-- Top Drag Pill / Handle -->
        <div class="pt-3 pb-1 flex justify-center shrink-0 cursor-pointer" aria-hidden="true" @click="close">
          <div class="w-12 h-1.5 rounded-full bg-muted-foreground/30 hover:bg-muted-foreground/50 transition-colors" />
        </div>

        <!-- Header -->
        <div class="px-5 py-3 border-b border-border flex items-center justify-between gap-2 shrink-0">
          <div class="min-w-0">
            <h3 :id="titleId" class="font-bold text-base text-foreground flex items-center gap-2">
              <slot name="title-icon" />
              <span>{{ title }}</span>
            </h3>
            <p v-if="description" class="text-xs text-muted-foreground mt-0.5">
              {{ description }}
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            class="h-10 w-10 shrink-0 rounded-full text-muted-foreground hover:text-foreground hover:bg-muted"
            :aria-label="t('common.close')"
            @click="close"
          >
            <X :size="18" />
          </Button>
        </div>

        <!-- Body Content -->
        <div class="p-5 overflow-y-auto space-y-5 flex-1 overscroll-contain">
          <slot />
        </div>

        <!-- Footer Actions -->
        <div
          v-if="$slots.footer"
          class="p-4 pb-[max(1rem,env(safe-area-inset-bottom))] border-t border-border bg-card/90 backdrop-blur-xs shrink-0 flex items-center gap-2.5"
        >
          <slot name="footer" />
        </div>
      </div>
    </div>
  </Teleport>
</template>
