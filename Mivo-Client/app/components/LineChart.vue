<script setup lang="ts">
import { computed, ref } from "vue";

interface Point {
  label: string;
  value: number;
}

const props = withDefaults(
  defineProps<{
    data: Point[];
    color?: string; // CSS color for the line/area (defaults to brand primary)
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
const padX = 4;

const maxValue = computed(() => Math.max(...props.data.map((d) => d.value), 1));

const points = computed(() =>
  props.data.map((d, i) => {
    const x = props.data.length === 1 ? width / 2 : padX + (i / (props.data.length - 1)) * (width - padX * 2);
    const y = height - padY - (d.value / maxValue.value) * (height - padY * 2);
    return { x, y, ...d };
  })
);

const linePath = computed(() =>
  points.value.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(" ")
);

const areaPath = computed(() => {
  if (points.value.length === 0) return "";
  const first = points.value[0];
  const last = points.value[points.value.length - 1];
  return `${linePath.value} L ${last.x.toFixed(2)} ${height - padY} L ${first.x.toFixed(2)} ${height - padY} Z`;
});

const hoverIndex = ref<number | null>(null);
const hoverPoint = computed(() => (hoverIndex.value === null ? null : points.value[hoverIndex.value]));

function onMove(evt: MouseEvent, svgEl: SVGSVGElement) {
  const rect = svgEl.getBoundingClientRect();
  const relX = ((evt.clientX - rect.left) / rect.width) * width;
  let closest = 0;
  let closestDist = Infinity;
  points.value.forEach((p, i) => {
    const dist = Math.abs(p.x - relX);
    if (dist < closestDist) {
      closestDist = dist;
      closest = i;
    }
  });
  hoverIndex.value = closest;
}
</script>

<template>
  <div class="relative w-full">
    <svg
      :viewBox="`0 0 ${width} ${height}`"
      class="w-full h-auto overflow-visible"
      preserveAspectRatio="none"
      @mousemove="(e) => onMove(e, e.currentTarget as SVGSVGElement)"
      @mouseleave="hoverIndex = null"
    >
      <!-- Recessive baseline -->
      <line :x1="0" :x2="width" :y1="height - padY" :y2="height - padY" class="stroke-border" stroke-width="1" />

      <defs>
        <linearGradient :id="`area-fill`" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" :stop-color="color" stop-opacity="0.18" />
          <stop offset="100%" :stop-color="color" stop-opacity="0" />
        </linearGradient>
      </defs>

      <path :d="areaPath" :fill="`url(#area-fill)`" stroke="none" />
      <path :d="linePath" fill="none" :stroke="color" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />

      <!-- Crosshair + hover marker -->
      <g v-if="hoverPoint">
        <line
          :x1="hoverPoint.x" :x2="hoverPoint.x" :y1="padY / 2" :y2="height - padY"
          class="stroke-border" stroke-width="1" stroke-dasharray="3 3"
        />
        <circle :cx="hoverPoint.x" :cy="hoverPoint.y" r="4" :fill="color" class="stroke-card" stroke-width="2" />
      </g>
    </svg>

    <!-- Tooltip -->
    <div
      v-if="hoverPoint"
      class="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-md border border-border bg-card px-2.5 py-1.5 text-xs shadow-md whitespace-nowrap"
      :style="{ left: `${(hoverPoint.x / width) * 100}%`, top: `${(hoverPoint.y / height) * 100}%`, marginTop: '-8px' }"
    >
      <div class="font-semibold text-foreground">{{ valueFormatter(hoverPoint.value) }}</div>
      <div class="text-muted-foreground">{{ hoverPoint.label }}</div>
    </div>
  </div>
</template>
