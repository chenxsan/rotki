<script setup lang="ts">
import { type DataTableHeader } from 'vuetify';
import BaseExternalLink from '@/components/base/BaseExternalLink.vue';
import BigDialog from '@/components/dialogs/BigDialog.vue';
import RowActions from '@/components/helper/RowActions.vue';
import ExchangeKeysForm from '@/components/settings/api-keys/ExchangeKeysForm.vue';

import { useExchangeBalancesStore } from '@/store/balances/exchanges';
import { useNotificationsStore } from '@/store/notifications';
import { useSettingsStore } from '@/store/settings';
import { useGeneralSettingsStore } from '@/store/settings/general';
import { type Writeable } from '@/types';
import {
  type Exchange,
  type ExchangePayload,
  SupportedExchange
} from '@/types/exchanges';
import { useTradeLocations } from '@/types/trades';
import { useConfirmStore } from '@/store/confirm';

const placeholder: () => ExchangePayload = () => ({
  location: SupportedExchange.KRAKEN,
  name: '',
  newName: null,
  apiKey: null,
  apiSecret: null,
  passphrase: null,
  krakenAccountType: 'starter',
  binanceMarkets: null,
  ftxSubaccount: null
});

const nonSyncingExchanges = ref<Exchange[]>([]);

const store = useExchangeBalancesStore();
const { setupExchange, removeExchange } = store;
const { connectedExchanges } = storeToRefs(store);

const exchange = ref<ExchangePayload>(placeholder());

const showForm = ref<boolean>(false);
const edit = ref<boolean>(false);
const valid = ref<boolean>(false);
const pending = ref<boolean>(false);

const { nonSyncingExchanges: current } = storeToRefs(useGeneralSettingsStore());
const { update } = useSettingsStore();

const { tc } = useI18n();
const { usageGuideURL } = useInterop();

const findNonSyncExchangeIndex = (exchange: Exchange) => {
  return get(nonSyncingExchanges).findIndex((item: Exchange) => {
    return item.name === exchange.name && item.location === exchange.location;
  });
};

const isNonSyncExchange = (exchange: Exchange) => {
  return findNonSyncExchangeIndex(exchange) > -1;
};

const resetNonSyncingExchanges = () => {
  set(nonSyncingExchanges, get(current));
};

const toggleSync = async (exchange: Exchange) => {
  const index = findNonSyncExchangeIndex(exchange);

  const data = [...get(nonSyncingExchanges)];

  let enable = true;

  if (index > -1) {
    enable = false;
    data.splice(index);
  } else {
    data.push({ location: exchange.location, name: exchange.name });
  }

  const status = await update({
    nonSyncingExchanges: data
  });

  if (!status.success) {
    const { notify } = useNotificationsStore();
    notify({
      title: tc('exchange_settings.sync.messages.title'),
      message: tc('exchange_settings.sync.messages.description', 0, {
        action: enable
          ? tc('exchange_settings.sync.messages.enable')
          : tc('exchange_settings.sync.messages.disable'),
        location: exchange.location,
        name: exchange.name,
        message: status.message
      }),
      display: true
    });
  }

  resetNonSyncingExchanges();
};

const { exchangeName } = useTradeLocations();

const addExchange = () => {
  set(edit, false);
  set(showForm, true);
  set(exchange, placeholder());
};

const editExchange = (exchangePayload: Exchange) => {
  set(edit, true);
  set(showForm, true);
  set(exchange, {
    ...placeholder(),
    ...exchangePayload,
    newName: exchangePayload.name
  });
};

const cancel = () => {
  set(showForm, false);
  set(exchange, placeholder());
};

const setup = async () => {
  set(pending, true);
  const writeableExchange: Writeable<ExchangePayload> = { ...get(exchange) };
  if (writeableExchange.name === writeableExchange.newName) {
    writeableExchange.newName = null;
  }

  if (
    !!writeableExchange.ftxSubaccount &&
    writeableExchange.ftxSubaccount.trim().length === 0
  ) {
    writeableExchange.ftxSubaccount = null;
  }

  const success = await setupExchange({
    exchange: writeableExchange,
    edit: get(edit)
  });
  set(pending, false);
  if (success) {
    cancel();
  }
};

const remove = async (item: Exchange) => {
  const success = await removeExchange(item);

  if (success) {
    set(exchange, placeholder());
  }
};

onBeforeMount(() => {
  resetNonSyncingExchanges();
});

const router = useRouter();
onMounted(async () => {
  const { currentRoute } = router;
  if (currentRoute.query.add) {
    addExchange();
    await router.replace({ query: {} });
  }
});

const headers: DataTableHeader[] = [
  {
    text: tc('common.location'),
    value: 'location',
    width: '120px',
    align: 'center'
  },
  {
    text: tc('common.name'),
    value: 'name'
  },
  {
    text: tc('exchange_settings.header.sync_enabled'),
    value: 'syncEnabled'
  },
  {
    text: tc('exchange_settings.header.actions'),
    value: 'actions',
    width: '105px',
    align: 'center',
    sortable: false
  }
];

const { show } = useConfirmStore();

const showRemoveConfirmation = (item: Exchange) => {
  show(
    {
      title: tc('exchange_settings.confirmation.title'),
      message: tc('exchange_settings.confirmation.message', 0, {
        name: item?.name ?? '',
        location: item ? exchangeName(item.location) : ''
      })
    },
    () => remove(item)
  );
};
</script>

<template>
  <div class="exchange-settings" data-cy="exchanges">
    <card outlined-body>
      <template #title>
        {{ tc('exchange_settings.title') }}
      </template>
      <template #subtitle>
        <i18n path="exchange_settings.subtitle" tag="div">
          <base-external-link
            :text="tc('exchange_settings.usage_guide')"
            :href="usageGuideURL + '#adding-an-exchange'"
          />
        </i18n>
      </template>
      <v-btn
        absolute
        fab
        top
        right
        color="primary"
        data-cy="add-exchange"
        @click="addExchange()"
      >
        <v-icon> mdi-plus </v-icon>
      </v-btn>
      <data-table
        key="index"
        data-cy="exchange-table"
        :items="connectedExchanges"
        :headers="headers"
        sort-by="name"
      >
        <template #item.location="{ item }">
          <location-display :identifier="item.location" />
        </template>
        <template #item.syncEnabled="{ item }">
          <v-switch
            :input-value="!isNonSyncExchange(item)"
            @change="toggleSync(item)"
          />
        </template>
        <template #item.actions="{ item }">
          <row-actions
            :delete-tooltip="tc('exchange_settings.delete.tooltip')"
            :edit-tooltip="tc('exchange_settings.edit.tooltip')"
            @delete-click="showRemoveConfirmation(item)"
            @edit-click="editExchange(item)"
          />
        </template>
      </data-table>
    </card>

    <big-dialog
      :display="showForm"
      :title="
        edit
          ? tc('exchange_settings.dialog.edit.title')
          : tc('exchange_settings.dialog.add.title')
      "
      :primary-action="tc('common.actions.save')"
      :secondary-action="tc('common.actions.cancel')"
      :action-disabled="!valid || pending"
      :loading="pending"
      @confirm="setup"
      @cancel="cancel"
    >
      <exchange-keys-form
        v-model="valid"
        :exchange="exchange"
        :edit="edit"
        @update:exchange="exchange = $event"
      />
    </big-dialog>
  </div>
</template>

<style scoped lang="scss">
.exchange-settings {
  &__connected-exchanges {
    display: flex;
    flex-direction: row;
    justify-content: flex-start;
    padding: 8px;
  }

  &__fields {
    &__exchange {
      :deep() {
        .v-select {
          &__selections {
            height: 36px;
          }
        }
      }
    }
  }
}
</style>
