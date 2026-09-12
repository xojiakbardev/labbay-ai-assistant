<script setup lang="ts">
import "vue-sonner/style.css";
import { Toaster } from "~/components/ui/sonner";
</script>

<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
  <!-- One app-wide toast outlet for vue-sonner's toast() (errors and
       confirmations from every page). -->
  <Toaster position="top-center" rich-colors close-button />
</template>
