<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 20 });
</script>
<template>
  <IconBase :size="size">
    <path d="M12 2s3 3.5 3 7c0 1.2-.6 2-1.3 2.7.9-.2 1.8-1 2.3-2 1.3 1.7 2 3.4 2 5a6 6 0 1 1-12 0c0-2.5 1.2-4 2-5.5.4.9 1 1.5 1.7 1.8C9 9.5 9 7 12 2Z" />
  </IconBase>
</template>
