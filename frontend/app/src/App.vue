<script setup lang="ts">
import { startPromise } from '@/utils';
import '@/utils/chartjs-adapter-dayjs';

useSessionStateCleaner();

const { setupBackend } = useBackendManagement();
startPromise(setupBackend());
</script>

<template>
  <router-view />
</template>
