<script setup lang="ts">
import { type Blockchain } from '@rotki/common/lib/blockchain';
import { type PropType } from 'vue';
import ListItem from '@/components/helper/ListItem.vue';

defineProps({
  item: {
    required: true,
    type: Object as PropType<{ symbol: Blockchain; name: string }>
  }
});
</script>
<template>
  <list-item :title="item.symbol" :subtitle="item.name">
    <template #icon>
      <asset-icon size="26px" :identifier="item.symbol" />
    </template>
  </list-item>
</template>
