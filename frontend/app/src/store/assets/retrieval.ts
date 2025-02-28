import { type AssetInfo } from '@rotki/common/lib/data';
import { type MaybeRef } from '@vueuse/core';
import { type ComputedRef } from 'vue';
import { CUSTOM_ASSET } from '@/services/assets/consts';
import { useAssetInfoApi } from '@/services/assets/info';
import { useAssetCacheStore } from '@/store/assets/asset-cache';
import { type ERC20Token } from '@/store/balances/types';
import { useNotificationsStore } from '@/store/notifications';
import { useGeneralSettingsStore } from '@/store/settings/general';
import { useTasks } from '@/store/tasks';
import { type TaskMeta } from '@/types/task';
import { TaskType } from '@/types/task-type';
import { getAddressFromEvmIdentifier, isEvmIdentifier } from '@/utils/assets';

export const useAssetInfoRetrieval = defineStore(
  'assets/infoRetrievals',
  () => {
    const { erc20details } = useAssetInfoApi();
    const { retrieve, isPending } = useAssetCacheStore();
    const { treatEth2AsEth } = storeToRefs(useGeneralSettingsStore());
    const { tc } = useI18n();
    const { notify } = useNotificationsStore();
    const { awaitTask } = useTasks();

    const assetAssociationMap: ComputedRef<Record<string, string>> = computed(
      () => {
        const associationMap: Record<string, string> = {};
        if (get(treatEth2AsEth)) {
          associationMap.ETH2 = 'ETH';
        }
        return associationMap;
      }
    );

    const getAssociatedAssetIdentifier = (
      identifier: string
    ): ComputedRef<string> =>
      computed(() => {
        return get(assetAssociationMap)[identifier] ?? identifier;
      });

    const getAssetNameFallback = (id: string) => {
      if (isEvmIdentifier(id)) {
        const address = getAddressFromEvmIdentifier(id);
        return `EVM Token: ${address}`;
      }
      return '';
    };

    const assetInfo = (
      identifier: MaybeRef<string | undefined>,
      enableAssociation: MaybeRef<boolean> = true,
      isCollectionParent: MaybeRef<boolean> = true
    ): ComputedRef<AssetInfo | null> =>
      computed(() => {
        const id = get(identifier);
        if (!id) return null;

        if (get(isPending(id))) {
          return null;
        }

        const key = get(enableAssociation)
          ? get(getAssociatedAssetIdentifier(id))
          : id;

        const data = get(retrieve(key));

        const isCustomAsset =
          data?.isCustomAsset || data?.assetType === CUSTOM_ASSET;

        if (isCustomAsset) {
          return {
            ...data,
            symbol: data.name,
            isCustomAsset
          };
        }
        const { fetchedAssetCollections } = storeToRefs(useAssetCacheStore());
        const collectionData =
          get(isCollectionParent) && data?.collectionId
            ? get(fetchedAssetCollections)[data.collectionId]
            : null;

        const name =
          collectionData?.name || data?.name || getAssetNameFallback(id);
        const symbol =
          collectionData?.symbol || data?.symbol || getAssetNameFallback(id);

        return {
          ...data,
          isCustomAsset,
          name,
          symbol
        };
      });

    const assetSymbol = (
      identifier: MaybeRef<string | undefined>,
      enableAssociation: MaybeRef<boolean> = true
    ): ComputedRef<string> =>
      computed(() => {
        const id = get(identifier);
        if (!id) return '';

        const symbol = get(assetInfo(id, enableAssociation))?.symbol;
        if (symbol) return symbol;

        return '';
      });

    const assetName = (
      identifier: MaybeRef<string>,
      enableAssociation: MaybeRef<boolean> = true
    ): ComputedRef<string> =>
      computed(() => {
        const id = get(identifier);
        if (!id) return '';

        const name = get(assetInfo(id, enableAssociation))?.name;
        if (name) return name;

        return '';
      });

    const tokenAddress = (
      identifier: MaybeRef<string>,
      enableAssociation: MaybeRef<boolean> = true
    ): ComputedRef<string> =>
      computed(() => {
        const id = get(identifier);
        if (!id) return '';

        const key = get(enableAssociation)
          ? get(getAssociatedAssetIdentifier(id))
          : id;
        return getAddressFromEvmIdentifier(key);
      });

    const fetchTokenDetails = async (address: string): Promise<ERC20Token> => {
      try {
        const taskType = TaskType.ERC20_DETAILS;
        const { taskId } = await erc20details(address);
        const { result } = await awaitTask<ERC20Token, TaskMeta>(
          taskId,
          taskType,
          {
            title: tc('actions.assets.erc20.task.title', 0, { address })
          }
        );
        return result;
      } catch (e: any) {
        notify({
          title: tc('actions.assets.erc20.error.title', 0, { address }),
          message: tc('actions.assets.erc20.error.description', 0, {
            message: e.message
          }),
          display: true
        });
        return {};
      }
    };

    return {
      fetchTokenDetails,
      getAssociatedAssetIdentifier,
      assetInfo,
      assetSymbol,
      assetName,
      tokenAddress
    };
  }
);

if (import.meta.hot) {
  import.meta.hot.accept(
    acceptHMRUpdate(useAssetInfoRetrieval, import.meta.hot)
  );
}
