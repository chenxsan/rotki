import isEqual from 'lodash/isEqual';
import { useEthNamesApi } from '@/services/balances/ethereum-names';
import { useNotificationsStore } from '@/store/notifications';
import { useFrontendSettingsStore } from '@/store/settings/frontend';
import { useTasks } from '@/store/tasks';
import {
  type EthAddressBookLocation,
  type EthNames,
  type EthNamesEntries
} from '@/types/eth-names';
import { type TaskMeta } from '@/types/task';
import { TaskType } from '@/types/task-type';
import { uniqueStrings } from '@/utils/data';
import { logger } from '@/utils/logging';
import { isValidEthAddress } from '@/utils/text';

export const useEthNamesStore = defineStore('ethNames', () => {
  const { enableEthNames } = storeToRefs(useFrontendSettingsStore());

  const ensAddresses = ref<string[]>([]);
  const ethNames = ref<EthNames>({});

  const ethAddressBookGlobal = ref<EthNamesEntries>([]);
  const ethAddressBookPrivate = ref<EthNamesEntries>([]);

  const ethAddressBook = computed(() => {
    return {
      global: get(ethAddressBookGlobal),
      private: get(ethAddressBookPrivate)
    };
  });

  const { awaitTask } = useTasks();
  const { notify } = useNotificationsStore();
  const { t, tc } = useI18n();

  const {
    getEnsNames,
    getEnsNamesTask,
    getEthNames,
    getEthAddressBook,
    addEthAddressBook: addAddressBook,
    deleteEthAddressBook: deleteAddressBook,
    updateEthAddressBook: updateAddressBook
  } = useEthNamesApi();

  const updateEnsAddresses = (newAddresses: string[]): boolean => {
    const newEnsAddresses = [...get(ensAddresses), ...newAddresses]
      .filter(uniqueStrings)
      .filter(isValidEthAddress);

    const currentEnsAddresses = [...get(ensAddresses)];

    const changed = !isEqual(newEnsAddresses, currentEnsAddresses);

    if (changed) {
      set(ensAddresses, newEnsAddresses);
    }

    return changed;
  };

  const fetchEnsNames = async (
    addresses: string[],
    forceUpdate = false
  ): Promise<void> => {
    if (addresses.length === 0) return;

    const changed = updateEnsAddresses(addresses);

    // Don't fetch if not forceUpdate and no new ens names that need to be fetched.
    if (!forceUpdate && !changed) return;

    const latestEnsAddresses = get(ensAddresses);
    if (latestEnsAddresses.length > 0) {
      if (forceUpdate) {
        const taskType = TaskType.FETCH_ENS_NAMES;
        const { taskId } = await getEnsNamesTask(latestEnsAddresses);
        await awaitTask<EthNames, TaskMeta>(taskId, taskType, {
          title: tc('ens_names.task.title')
        });
      } else {
        await getEnsNames(latestEnsAddresses);
      }
      await fetchEthNames();
    }
  };

  const fetchEthNames = async (): Promise<void> => {
    const addresses = [
      ...get(ethAddressBookGlobal).map(item => item.address),
      ...get(ethAddressBookPrivate).map(item => item.address),
      ...get(ensAddresses)
    ].filter(uniqueStrings);

    try {
      const result = await getEthNames(addresses);
      set(ethNames, result);
    } catch (e: any) {
      logger.error(e);
      const message = e?.message ?? e ?? '';
      notify({
        title: tc('eth_names.error.title'),
        message: tc('eth_names.error.message', 0, { message }),
        display: true
      });
    }
  };

  const ethNameSelector = (address: string) =>
    computed<string | null>(() => {
      if (!get(enableEthNames)) return null;
      return get(ethNames)[address] ?? null;
    });

  const updateEthAddressBookState = async (
    location: EthAddressBookLocation,
    result: EthNamesEntries
  ) => {
    if (location === 'global') {
      set(ethAddressBookGlobal, result);
    } else {
      set(ethAddressBookPrivate, result);
    }

    await fetchEthNames();
  };

  const fetchEthAddressBook = async (
    location: EthAddressBookLocation,
    addresses?: string[]
  ) => {
    const notifyError = (error?: any) => {
      logger.error(error);
      const message = error?.message ?? error ?? '';
      notify({
        title: t('eth_address_book.actions.fetch.error.title').toString(),
        message: t('eth_address_book.actions.fetch.error.message', {
          message
        }).toString(),
        display: true
      });
    };
    try {
      const result = await getEthAddressBook(location, addresses);
      await updateEthAddressBookState(location, result);
    } catch (e: any) {
      notifyError(e);
    }
  };

  const addEthAddressBook = async (
    location: EthAddressBookLocation,
    entries: EthNamesEntries
  ) => {
    const result = await addAddressBook(location, entries);

    if (result) {
      await fetchEthAddressBook(location);
    }
  };

  const updateEthAddressBook = async (
    location: EthAddressBookLocation,
    entries: EthNamesEntries
  ) => {
    const result = await updateAddressBook(location, entries);

    if (result) {
      await fetchEthAddressBook(location);
    }
  };

  const deleteEthAddressBook = async (
    location: EthAddressBookLocation,
    addresses: string[]
  ) => {
    const result = await deleteAddressBook(location, addresses);

    if (result) {
      await fetchEthAddressBook(location);
    }
  };

  return {
    fetchEnsNames,
    ethNames,
    ethNameSelector,
    ethAddressBook,
    ensAddresses,
    fetchEthNames,
    fetchEthAddressBook,
    addEthAddressBook,
    updateEthAddressBook,
    deleteEthAddressBook
  };
});

if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useEthNamesStore, import.meta.hot));
}
