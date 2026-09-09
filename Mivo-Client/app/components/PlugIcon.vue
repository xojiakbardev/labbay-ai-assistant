<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 20 });
</script>
<template>
  <IconBase :size="size">
    <path d="M9 3v4M15 3v4M6 7h12l-1 5a5 5 0 0 1-5 4v4M6 7l1 5a5 5 0 0 0 5 4" />
  </IconBase>
</template>
