<script setup lang="ts">
import PrioritizedList from '@/components/helper/PrioritizedList.vue';
import SettingsOption from '@/components/settings/controls/SettingsOption.vue';
import { useEthNamesStore } from '@/store/balances/ethereum-names';
import { useGeneralSettingsStore } from '@/store/settings/general';
import {
  PrioritizedListData,
  type PrioritizedListItemData
} from '@/types/prioritized-list-data';
import {
  BLOCKCHAIN_ACCOUNT_PRIO_LIST_ITEM,
  ENS_NAMES_PRIO_LIST_ITEM,
  ETHEREUM_TOKENS_PRIO_LIST_ITEM,
  GLOBAL_ADDRESSBOOK_PRIO_LIST_ITEM,
  HARDCODED_MAPPINGS_PRIO_LIST_ITEM,
  PRIVATE_ADDRESSBOOK_PRIO_LIST_ITEM,
  type PrioritizedListId
} from '@/types/prioritized-list-id';

const currentAddressNamePriorities = ref<PrioritizedListId[]>([]);
const { addressNamePriority } = storeToRefs(useGeneralSettingsStore());
const { fetchEthNames } = useEthNamesStore();

const finishEditing = async () => {
  resetCurrentAddressNamePriorities();
  await fetchEthNames();
};

const resetCurrentAddressNamePriorities = () => {
  set(currentAddressNamePriorities, get(addressNamePriority));
};

const availableCurrentAddressNamePriorities =
  (): PrioritizedListData<PrioritizedListId> => {
    const itemData: Array<PrioritizedListItemData<PrioritizedListId>> = [
      BLOCKCHAIN_ACCOUNT_PRIO_LIST_ITEM,
      ENS_NAMES_PRIO_LIST_ITEM,
      ETHEREUM_TOKENS_PRIO_LIST_ITEM,
      GLOBAL_ADDRESSBOOK_PRIO_LIST_ITEM,
      HARDCODED_MAPPINGS_PRIO_LIST_ITEM,
      PRIVATE_ADDRESSBOOK_PRIO_LIST_ITEM
    ];

    return new PrioritizedListData(itemData);
  };

onMounted(() => {
  resetCurrentAddressNamePriorities();
});
const { t } = useI18n();
</script>

<template>
  <div>
    <div
      class="text-subtitle-1"
      v-text="t('eth_address_book.hint.priority.title')"
    />
    <settings-option
      #default="{ error, success, update }"
      setting="addressNamePriority"
      @finished="finishEditing"
    >
      <prioritized-list
        :value="currentAddressNamePriorities"
        :all-items="availableCurrentAddressNamePriorities()"
        :item-data-name="
          t('address_name_priority_setting.data_name').toString()
        "
        :disable-add="true"
        :disable-delete="true"
        :status="{ error, success }"
        @input="update"
      />
    </settings-option>
  </div>
</template>
