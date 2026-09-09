<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 20 });
</script>
<template>
  <IconBase :size="size">
    <path d="m21 3-8.5 18-3.5-7L2 10.5 21 3Z" />
    <path d="M9 14l9-9" />
  </IconBase>
</template>
