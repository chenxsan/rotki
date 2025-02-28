<script setup lang="ts">
import { type AssetBalanceWithPrice } from '@rotki/common';
import { type PropType, type Ref } from 'vue';
import { type DataTableHeader } from 'vuetify';
import AmountDisplay from '@/components/display/AmountDisplay.vue';
import DataTable from '@/components/helper/DataTable.vue';
import RowAppend from '@/components/helper/RowAppend.vue';
import { bigNumberSum } from '@/filters';
import { useAssetInfoRetrieval } from '@/store/assets/retrieval';
import { useGeneralSettingsStore } from '@/store/settings/general';
import { getSortItems } from '@/utils/assets';

const props = defineProps({
  balances: {
    required: true,
    type: Array as PropType<AssetBalanceWithPrice[]>
  },
  loading: {
    required: false,
    type: Boolean,
    default: false
  }
});

const { balances } = toRefs(props);
const expanded: Ref<AssetBalanceWithPrice[]> = ref([]);

const { t, tc } = useI18n();
const total = computed(() => {
  return bigNumberSum(balances.value.map(({ usdValue }) => usdValue));
});

const { currencySymbol } = storeToRefs(useGeneralSettingsStore());
const { assetInfo } = useAssetInfoRetrieval();

const tableHeaders = computed<DataTableHeader[]>(() => {
  return [
    {
      text: t('common.asset').toString(),
      value: 'asset',
      class: 'text-no-wrap'
    },
    {
      text: t('common.price_in_symbol', {
        symbol: get(currencySymbol)
      }).toString(),
      value: 'usdPrice',
      align: 'end',
      class: 'text-no-wrap'
    },
    {
      text: t('common.amount').toString(),
      value: 'amount',
      align: 'end',
      width: '50%'
    },
    {
      text: t('common.value_in_symbol', {
        symbol: get(currencySymbol)
      }).toString(),
      value: 'usdValue',
      align: 'end',
      class: 'text-no-wrap'
    },
    {
      text: '',
      width: '48px',
      value: 'expand',
      sortable: false
    }
  ];
});

const sortItems = getSortItems(asset => get(assetInfo(asset)));
</script>

<template>
  <data-table
    :headers="tableHeaders"
    :items="balances"
    :loading="loading"
    single-expand
    :expanded="expanded"
    :loading-text="tc('asset_balances.loading')"
    :custom-sort="sortItems"
    sort-by="usdValue"
    item-key="asset"
  >
    <template #item.asset="{ item }">
      <asset-details
        opens-details
        :asset="item.asset"
        :is-collection-parent="!!item.breakdown"
      />
    </template>
    <template #item.usdPrice="{ item }">
      <amount-display
        v-if="item.usdPrice && item.usdPrice.gte(0)"
        show-currency="symbol"
        :price-asset="item.asset"
        :price-of-asset="item.usdPrice"
        fiat-currency="USD"
        :value="item.usdPrice"
      />
      <div v-else class="d-flex justify-end">
        <v-skeleton-loader width="70" type="text" />
      </div>
    </template>
    <template #item.amount="{ item }">
      <amount-display :value="item.amount" />
    </template>
    <template #item.usdValue="{ item }">
      <amount-display
        show-currency="symbol"
        :amount="item.amount"
        :price-asset="item.asset"
        :price-of-asset="item.usdPrice"
        fiat-currency="USD"
        :value="item.usdValue"
      />
    </template>
    <template v-if="balances.length > 0" #body.append="{ isMobile }">
      <row-append
        label-colspan="3"
        :label="tc('common.total')"
        :is-mobile="isMobile"
      >
        <amount-display
          fiat-currency="USD"
          show-currency="symbol"
          :value="total"
        />
      </row-append>
    </template>
    <template #expanded-item="{ item }">
      <table-expand-container visible :colspan="tableHeaders.length">
        <asset-balances v-bind="props" :balances="item.breakdown" />
      </table-expand-container>
    </template>
    <template #item.expand="{ item }">
      <row-expander
        v-if="item.breakdown"
        :expanded="expanded.includes(item)"
        @click="expanded = expanded.includes(item) ? [] : [item]"
      />
    </template>
  </data-table>
</template>
