<script setup lang="ts">
interface Props {
  title: string;
  description?: string;
  class?: string;
}
defineProps<Props>();
</script>

<template>
  <div :class="['w-full rounded-xl border border-dashed border-border bg-card p-12 text-center flex flex-col items-center justify-center transition-all duration-200', $props.class]">
    <div class="w-14 h-14 rounded-full bg-primary/10 text-primary flex items-center justify-center mb-4">
      <slot name="icon" />
    </div>
    <h3 class="text-lg font-semibold text-foreground mb-1">{{ title }}</h3>
    <p v-if="description" class="text-sm text-muted-foreground max-w-md mb-6 leading-relaxed">
      {{ description }}
    </p>
    <div v-if="$slots.default" class="flex items-center gap-3">
      <slot />
    </div>
  </div>
</template>
