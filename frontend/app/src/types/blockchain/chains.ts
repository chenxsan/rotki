import { type Nullable } from '@rotki/common';
import { Blockchain } from '@rotki/common/lib/blockchain';
import { EvmChain } from '@rotki/common/lib/data';

const BtcChains = [Blockchain.BTC, Blockchain.BCH] as const;
const EthChains = [Blockchain.ETH, Blockchain.ETH2] as const;
const RestChains = [
  Blockchain.KSM,
  Blockchain.DOT,
  Blockchain.AVAX,
  Blockchain.OPTIMISM
] as const;
const TokenChains = [Blockchain.ETH, Blockchain.OPTIMISM] as const;

export type BtcChains = typeof BtcChains[number];
export type EthChains = typeof EthChains[number];
export type RestChains = typeof RestChains[number];
export type TokenChains = typeof TokenChains[number];

export const isBtcChain = (chain: Blockchain): chain is BtcChains =>
  BtcChains.includes(chain as any);
export const isEthChain = (chain: Blockchain): chain is EthChains =>
  EthChains.includes(chain as any);
export const isRestChain = (chain: Blockchain): chain is RestChains =>
  RestChains.includes(chain as any);
export const isTokenChain = (chain: Blockchain): chain is TokenChains =>
  TokenChains.includes(chain as any);

export const isBlockchain = (chain: string): chain is Blockchain =>
  Object.values(Blockchain).includes(chain as any);

const chainIcons: Record<EvmChain, string> = {
  [EvmChain.ETHEREUM]: './assets/images/modules/eth.svg',
  [EvmChain.ARBITRUM]: './assets/images/chains/arbitrum.svg',
  [EvmChain.BINANCE]: './assets/images/chains/binance.svg',
  [EvmChain.FANTOM]: './assets/images/chains/fantom.svg',
  [EvmChain.GNOSIS]: './assets/images/chains/gnosis.svg',
  [EvmChain.AVALANCHE]: './assets/images/chains/avalanche.svg',
  [EvmChain.MATIC]: './assets/images/chains/polygon.svg',
  [EvmChain.OPTIMISM]: './assets/images/chains/optimism.svg',
  [EvmChain.CELO]: './assets/images/chains/celo.svg'
};

export const getChainIcon = (chain?: Nullable<EvmChain>): string | null => {
  if (!chain) return null;
  return chainIcons[chain];
};
