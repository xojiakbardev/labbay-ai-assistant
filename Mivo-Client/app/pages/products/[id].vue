<script setup lang="ts">
definePageMeta({ layout: "dashboard" });

const route = useRoute();
const productId = computed(() => route.params.id as string);
</script>

<template>
  <ProductForm mode="edit" :product-id="productId" />
</template>
