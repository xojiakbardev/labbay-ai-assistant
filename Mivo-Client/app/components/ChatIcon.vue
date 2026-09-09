<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 20 });
</script>
<template>
  <IconBase :size="size">
    <path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5c-1.35 0-2.62-.31-3.75-.86L3 20l1.02-4.5A8.5 8.5 0 1 1 21 11.5Z" />
  </IconBase>
</template>
