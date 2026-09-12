<script setup lang="ts">
import { Flame, Sparkles, Info, UserCheck, CirclePause, Gauge, TriangleAlert } from "@lucide/vue";

// Icon + tint per notification type; the size of the tile comes from the
// caller's class.
const props = withDefaults(defineProps<{ type: string; size?: number }>(), { size: 16 });

const visual = computed(() => {
  switch (props.type) {
    case "lead_hot":
      return { icon: Flame, tone: "bg-amber-500/15 text-amber-500 dark:bg-amber-500/25" };
    case "lead_warm":
      return { icon: Flame, tone: "bg-yellow-500/15 text-yellow-600 dark:text-yellow-400" };
    case "lead_updated":
      return { icon: Sparkles, tone: "bg-blue-500/15 text-blue-500" };
    case "handoff":
      return { icon: UserCheck, tone: "bg-sky-500/15 text-sky-600 dark:text-sky-400" };
    case "ai_limit":
      return { icon: CirclePause, tone: "bg-orange-500/15 text-orange-600 dark:text-orange-400" };
    case "plan_limit":
      return { icon: Gauge, tone: "bg-orange-500/15 text-orange-600 dark:text-orange-400" };
    case "delivery_failed":
      return { icon: TriangleAlert, tone: "bg-red-500/15 text-red-600 dark:text-red-400" };
    default:
      return { icon: Info, tone: "bg-primary/15 text-primary" };
  }
});
</script>

<template>
  <div :class="['flex items-center justify-center shrink-0', visual.tone]">
    <component :is="visual.icon" :size="size" />
  </div>
</template>
