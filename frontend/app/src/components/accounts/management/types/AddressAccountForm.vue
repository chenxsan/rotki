<script setup lang="ts">
import { Blockchain } from '@rotki/common/lib/blockchain';
import AccountDataInput from '@/components/accounts/management/inputs/AccountDataInput.vue';
import { type Module } from '@/types/modules';
import { useMessageStore } from '@/store/message';
import { startPromise } from '@/utils';
import { deserializeApiErrorMessage } from '@/services/converters';
import { useBlockchainStore } from '@/store/blockchain';
import { useBlockchainAccountsStore } from '@/store/blockchain/accounts';
import { useEthNamesStore } from '@/store/balances/ethereum-names';
import AddressInput from '@/components/accounts/blockchain/AddressInput.vue';
import ModuleActivator from '@/components/accounts/ModuleActivator.vue';
import { type BlockchainAccountWithBalance } from '@/store/balances/types';

const props = defineProps<{ blockchain: Blockchain }>();

const { blockchain } = toRefs(props);

const addresses = ref<string[]>([]);
const label = ref('');
const tags = ref<string[]>([]);
const selectedModules = ref<Module[]>([]);

const allEvmChains = ref(true);
const errorMessages = ref<Record<string, string[]>>({});

const { addAccounts, addEvmAccounts, fetchAccounts } = useBlockchainStore();
const { editAccount } = useBlockchainAccountsStore();
const { setMessage } = useMessageStore();
const { fetchEnsNames } = useEthNamesStore();
const { isEvm } = useSupportedChains();
const { valid, setSave, pending, accountToEdit } = useAccountDialog();
const { loading } = useAccountLoading();
const { tc } = useI18n();

const evmChain = isEvm(blockchain);

const reset = () => {
  set(addresses, []);
  set(label, '');
  set(tags, []);
  set(selectedModules, []);
};

const save = async () => {
  const edit = !!get(accountToEdit);
  const chain = get(blockchain);
  const isEth = chain === Blockchain.ETH;

  try {
    const entries = get(addresses);
    if (edit) {
      const address = entries[0];
      await editAccount({
        blockchain: chain,
        address,
        label: get(label),
        tags: get(tags)
      });

      if (isEth) {
        await fetchEnsNames([address], true);
      }
      startPromise(fetchAccounts(chain));
    } else {
      const payload = entries.map(address => ({
        address,
        label: get(label),
        tags: get(tags)
      }));

      if (get(logicAnd(allEvmChains, isEvm(chain)))) {
        await addEvmAccounts({
          payload,
          modules: get(selectedModules)
        });
      } else {
        await addAccounts({
          blockchain: chain,
          payload,
          modules: isEth ? get(selectedModules) : undefined
        });
      }
    }
  } catch (e: any) {
    set(errorMessages, deserializeApiErrorMessage(e.message) || {});

    await setMessage({
      description: tc('account_form.error.description', 0, {
        error: e.message
      }).toString(),
      title: tc('account_form.error.title'),
      success: false
    });
    return false;
  } finally {
    set(pending, false);
  }
  return true;
};

const setAccount = (acc: BlockchainAccountWithBalance): void => {
  set(addresses, [acc.address]);
  set(label, acc.label);
  set(tags, acc.tags);
};

watch(accountToEdit, acc => {
  if (!acc) {
    reset();
  } else {
    setAccount(acc);
  }
});

onMounted(() => {
  setSave(save);
  const acc = get(accountToEdit);
  if (!acc) {
    reset();
  } else {
    setAccount(acc);
  }
});
</script>

<template>
  <v-form v-model="valid">
    <module-activator
      v-if="blockchain === Blockchain.ETH && !accountToEdit"
      @update:selection="selectedModules = $event"
    />

    <v-sheet v-if="evmChain && !accountToEdit" outlined rounded>
      <v-checkbox
        v-model="allEvmChains"
        class="py-2 px-4"
        :label="tc('address_account_form.label')"
        persistent-hint
        :hint="tc('address_account_form.hint')"
      />
    </v-sheet>

    <address-input
      :addresses="addresses"
      :error-messages="errorMessages"
      :disabled="loading"
      :multi="!accountToEdit"
      @update:addresses="addresses = $event"
    />
    <account-data-input
      :tags="tags"
      :label="label"
      :disabled="loading"
      @update:label="label = $event"
      @update:tags="tags = $event"
    />
  </v-form>
</template>
