<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 20 });
</script>
<template>
  <IconBase :size="size">
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />
    <circle cx="12" cy="12" r="3" />
  </IconBase>
</template>
