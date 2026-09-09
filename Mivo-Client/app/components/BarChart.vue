<script setup lang="ts">
import { computed, ref } from "vue";

interface Point {
  label: string;
  value: number;
}

const props = withDefaults(
  defineProps<{
    data: Point[];
    color?: string;
    valueFormatter?: (v: number) => string;
  }>(),
  {
    color: "hsl(var(--primary))",
    valueFormatter: (v: number) => String(v),
  }
);

const width = 560;
const height = 180;
const padY = 14;
const gap = 8;

const maxValue = computed(() => Math.max(...props.data.map((d) => d.value), 1));
const barWidth = computed(() => (width - gap * (props.data.length + 1)) / props.data.length);

const bars = computed(() =>
  props.data.map((d, i) => {
    const barHeight = Math.max((d.value / maxValue.value) * (height - padY * 2), d.value > 0 ? 3 : 0);
    return {
      x: gap + i * (barWidth.value + gap),
      y: height - padY - barHeight,
      h: barHeight,
      ...d,
    };
  })
);

const hoverIndex = ref<number | null>(null);
</script>

<template>
  <div class="relative w-full">
    <svg :viewBox="`0 0 ${width} ${height}`" class="w-full h-auto overflow-visible" preserveAspectRatio="none">
      <line :x1="0" :x2="width" :y1="height - padY" :y2="height - padY" class="stroke-border" stroke-width="1" />

      <g v-for="(bar, i) in bars" :key="bar.label">
        <rect
          :x="bar.x" :y="bar.y" :width="barWidth" :height="bar.h" rx="3"
          :fill="color" :opacity="hoverIndex === i ? 1 : 0.85"
          class="transition-opacity duration-150 cursor-pointer"
          @mouseenter="hoverIndex = i"
          @mouseleave="hoverIndex = null"
        />
      </g>
    </svg>

    <div
      v-if="hoverIndex !== null"
      class="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-md border border-border bg-card px-2.5 py-1.5 text-xs shadow-md whitespace-nowrap"
      :style="{
        left: `${((bars[hoverIndex].x + barWidth / 2) / width) * 100}%`,
        top: `${(bars[hoverIndex].y / height) * 100}%`,
        marginTop: '-8px',
      }"
    >
      <div class="font-semibold text-foreground">{{ valueFormatter(bars[hoverIndex].value) }}</div>
      <div class="text-muted-foreground">{{ bars[hoverIndex].label }}</div>
    </div>
  </div>
</template>
