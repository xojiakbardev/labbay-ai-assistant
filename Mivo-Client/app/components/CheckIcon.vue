<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 20 });
</script>
<template>
  <IconBase :size="size">
    <path d="M20 6 9 17l-5-5" />
  </IconBase>
</template>
