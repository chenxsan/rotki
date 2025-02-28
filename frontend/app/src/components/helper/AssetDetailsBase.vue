<script setup lang="ts">
import { type ComputedRef, type PropType } from 'vue';
import AssetIcon from '@/components/helper/display/icons/AssetIcon.vue';
import ListItem from '@/components/helper/ListItem.vue';
import { Routes } from '@/router/routes';
import { useAssetCacheStore } from '@/store/assets/asset-cache';
import { type NftAsset } from '@/store/assets/nft';

const props = defineProps({
  asset: {
    required: true,
    type: Object as PropType<NftAsset>
  },
  assetStyled: { required: false, type: Object, default: () => null },
  opensDetails: { required: false, type: Boolean, default: false },
  changeable: { required: false, type: Boolean, default: false },
  hideName: { required: false, type: Boolean, default: false },
  dense: { required: false, type: Boolean, default: false },
  enableAssociation: { required: false, type: Boolean, default: true },
  showChain: { required: false, type: Boolean, default: true },
  isCollectionParent: { required: false, type: Boolean, default: false }
});

const { asset, opensDetails, isCollectionParent } = toRefs(props);
const rootAttrs = useAttrs();

const symbol: ComputedRef<string> = computed(() => get(asset).symbol ?? '');
const name: ComputedRef<string> = computed(() => get(asset).name ?? '');

const router = useRouter();
const navigate = async () => {
  if (!get(opensDetails)) {
    return;
  }
  const id = encodeURIComponent(get(asset).identifier);
  const collectionParent = get(isCollectionParent);

  await router.push({
    path: Routes.ASSETS.replace(':identifier', id),
    query: !collectionParent
      ? {}
      : {
          collectionParent: 'true'
        }
  });
};

const { isPending } = useAssetCacheStore();
const loading: ComputedRef<boolean> = computed(() =>
  get(isPending(get(asset).identifier))
);
</script>

<template>
  <list-item
    v-bind="rootAttrs"
    :class="opensDetails ? 'asset-details-base--link' : null"
    :dense="dense"
    :loading="loading"
    :title="asset.isCustomAsset ? name : symbol"
    :subtitle="asset.isCustomAsset ? asset.customAssetType : name"
    @click="navigate"
  >
    <template #icon>
      <v-img
        v-if="asset.imageUrl"
        contain
        height="26px"
        width="26px"
        max-width="26px"
        :src="asset.imageUrl"
      />
      <asset-icon
        v-else
        :changeable="changeable"
        size="26px"
        :styled="assetStyled"
        :identifier="asset.identifier"
        :enable-association="enableAssociation"
        :show-chain="showChain"
      />
    </template>
  </list-item>
</template>

<style scoped lang="scss">
.asset-details-base {
  &--link {
    cursor: pointer;
  }
}
</style>
