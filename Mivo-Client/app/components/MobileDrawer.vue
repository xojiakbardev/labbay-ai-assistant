<script setup lang="ts">
import { X } from "lucide-vue-next";

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

function close() {
  emit("update:open", false);
  emit("close");
}

// Manage body scroll lock
watch(
  () => props.open,
  (val) => {
    if (typeof document === "undefined") return;
    if (val) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
  },
  { immediate: true }
);

onUnmounted(() => {
  if (typeof document !== "undefined") {
    document.body.style.overflow = "";
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
        class="bg-card text-card-foreground border-t sm:border border-border rounded-t-3xl sm:rounded-2xl shadow-2xl w-full sm:max-w-md max-h-[85vh] sm:max-h-[90vh] flex flex-col overflow-hidden animate-in slide-in-from-bottom sm:zoom-in-95 duration-200 ease-out"
        @click.stop
      >
        <!-- Top Drag Pill / Handle -->
        <div class="pt-3 pb-1 flex justify-center shrink-0 cursor-pointer" @click="close">
          <div class="w-12 h-1.5 rounded-full bg-muted-foreground/30 hover:bg-muted-foreground/50 transition-colors" />
        </div>

        <!-- Header -->
        <div class="px-5 py-3 border-b border-border flex items-center justify-between shrink-0">
          <div>
            <h3 class="font-bold text-base text-foreground flex items-center gap-2">
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
            class="h-8 w-8 rounded-full text-muted-foreground hover:text-foreground hover:bg-muted"
            @click="close"
          >
            <X :size="16" />
          </Button>
        </div>

        <!-- Body Content -->
        <div class="p-5 overflow-y-auto space-y-5 flex-1 overscroll-contain">
          <slot />
        </div>

        <!-- Footer Actions -->
        <div
          v-if="$slots.footer"
          class="p-4 border-t border-border bg-card/90 backdrop-blur-xs shrink-0 flex items-center gap-2.5"
        >
          <slot name="footer" />
        </div>
      </div>
    </div>
  </Teleport>
</template>
