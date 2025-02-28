import logging
from collections import defaultdict
from enum import Enum
from typing import TYPE_CHECKING, Any, DefaultDict, NamedTuple, Optional

from gevent.lock import Semaphore

from rotkehlchen.accounting.structures.balance import AssetBalance, Balance, BalanceSheet
from rotkehlchen.accounting.structures.defi import DefiEvent, DefiEventType
from rotkehlchen.assets.asset import CryptoAsset
from rotkehlchen.chain.ethereum.constants import RAY, RAY_DIGITS
from rotkehlchen.chain.ethereum.defi.defisaver_proxy import HasDSProxy
from rotkehlchen.chain.ethereum.utils import asset_normalized_value, token_normalized_value
from rotkehlchen.constants import ONE, ZERO
from rotkehlchen.constants.assets import (
    A_AAVE,
    A_BAL,
    A_BAT,
    A_COMP,
    A_DAI,
    A_ETH,
    A_GUSD,
    A_KNC,
    A_LINK,
    A_LRC,
    A_MANA,
    A_MATIC,
    A_PAX,
    A_RENBTC,
    A_TUSD,
    A_UNI,
    A_USDC,
    A_USDT,
    A_WBTC,
    A_YFI,
    A_ZRX,
)
from rotkehlchen.constants.timing import YEAR_IN_SECONDS
from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.fval import FVal
from rotkehlchen.history.price import query_usd_price_or_use_default
from rotkehlchen.inquirer import Inquirer
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.premium.premium import Premium
from rotkehlchen.serialization.deserialize import deserialize_evm_address
from rotkehlchen.types import ChecksumEvmAddress, EVMTxHash, Timestamp
from rotkehlchen.user_messages import MessagesAggregator
from rotkehlchen.utils.misc import address_to_bytes32, hexstr_to_int, shift_num_right_by, ts_now

from .constants import MAKERDAO_REQUERY_PERIOD, WAD

if TYPE_CHECKING:
    from rotkehlchen.chain.ethereum.node_inquirer import EthereumInquirer
    from rotkehlchen.db.dbhandler import DBHandler

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


def create_collateral_type_mapping() -> dict[str, CryptoAsset]:
    """Create a mapping with resolved assets for those used as collateral in maker"""
    return {
        'BAT-A': A_BAT.resolve_to_crypto_asset(),
        'ETH-A': A_ETH.resolve_to_crypto_asset(),
        'ETH-B': A_ETH.resolve_to_crypto_asset(),
        'ETH-C': A_ETH.resolve_to_crypto_asset(),
        'KNC-A': A_KNC.resolve_to_crypto_asset(),
        'TUSD-A': A_TUSD.resolve_to_crypto_asset(),
        'USDC-A': A_USDC.resolve_to_crypto_asset(),
        'USDC-B': A_USDC.resolve_to_crypto_asset(),
        'USDT-A': A_USDT.resolve_to_crypto_asset(),
        'WBTC-A': A_WBTC.resolve_to_crypto_asset(),
        'WBTC-B': A_WBTC.resolve_to_crypto_asset(),
        'WBTC-C': A_WBTC.resolve_to_crypto_asset(),
        'ZRX-A': A_ZRX.resolve_to_crypto_asset(),
        'MANA-A': A_MANA.resolve_to_crypto_asset(),
        'PAXUSD-A': A_PAX.resolve_to_crypto_asset(),
        'COMP-A': A_COMP.resolve_to_crypto_asset(),
        'LRC-A': A_LRC.resolve_to_crypto_asset(),
        'LINK-A': A_LINK.resolve_to_crypto_asset(),
        'BAL-A': A_BAL.resolve_to_crypto_asset(),
        'YFI-A': A_YFI.resolve_to_crypto_asset(),
        'GUSD-A': A_GUSD.resolve_to_crypto_asset(),
        'UNI-A': A_UNI.resolve_to_crypto_asset(),
        'RENBTC-A': A_RENBTC.resolve_to_crypto_asset(),
        'AAVE-A': A_AAVE.resolve_to_crypto_asset(),
        'MATIC-A': A_MATIC.resolve_to_crypto_asset(),
    }


class VaultEventType(Enum):
    DEPOSIT_COLLATERAL = 1
    WITHDRAW_COLLATERAL = 2
    GENERATE_DEBT = 3
    PAYBACK_DEBT = 4
    LIQUIDATION = 5

    def __str__(self) -> str:
        if self == VaultEventType.DEPOSIT_COLLATERAL:
            return 'deposit'
        if self == VaultEventType.WITHDRAW_COLLATERAL:
            return 'withdraw'
        if self == VaultEventType.GENERATE_DEBT:
            return 'generate'
        if self == VaultEventType.PAYBACK_DEBT:
            return 'payback'
        if self == VaultEventType.LIQUIDATION:
            return 'liquidation'
        # else
        raise RuntimeError(f'Corrupt value {self} for VaultEventType -- Should never happen')


class VaultEvent(NamedTuple):
    event_type: VaultEventType
    value: Balance
    timestamp: Timestamp
    tx_hash: EVMTxHash

    def __str__(self) -> str:
        """Used in DefiEvent processing during accounting"""
        result = f'Makerdao Vault {self.event_type}'
        if self.event_type in (VaultEventType.GENERATE_DEBT, VaultEventType.PAYBACK_DEBT):
            result += ' debt'
        return result


class MakerdaoVault(NamedTuple):
    identifier: int
    # The type of collateral used for the vault. asset + set of parameters.
    # e.g. ETH-A. Various types can be seen here: https://catflip.co/
    collateral_type: str
    owner: ChecksumEvmAddress
    collateral_asset: CryptoAsset
    # The amount/usd_value of collateral tokens locked
    collateral: Balance
    # amount/usd value of DAI drawn
    debt: Balance
    # The current collateralization_ratio of the Vault. None if nothing is locked in.
    collateralization_ratio: Optional[str]
    # The ratio at which the vault is open for liquidation. (e.g. 1.5 for 150%)
    liquidation_ratio: FVal
    # The USD price of collateral at which the Vault becomes unsafe. None if nothing is locked in.
    liquidation_price: Optional[FVal]
    urn: ChecksumEvmAddress
    stability_fee: FVal

    def serialize(self) -> dict[str, Any]:
        result = self._asdict()  # pylint: disable=no-member
        # But make sure to turn liquidation ratio and stability fee to a percentage
        result['collateral_asset'] = self.collateral_asset.identifier
        result['liquidation_ratio'] = self.liquidation_ratio.to_percentage(2)
        result['stability_fee'] = self.stability_fee.to_percentage(2)
        result['collateral'] = self.collateral.serialize()
        result['debt'] = self.debt.serialize()
        result['liquidation_price'] = (
            str(self.liquidation_price) if self.liquidation_price else None
        )
        # And don't send unneeded data
        del result['urn']
        return result

    @property
    def ilk(self) -> bytes:
        """Returns the collateral type string encoded into bytes32, known as ilk in makerdao"""
        return self.collateral_type.encode('utf-8').ljust(32, b'\x00')

    def get_balance(self) -> BalanceSheet:
        starting_assets = {self.collateral_asset: self.collateral} if self.collateral.amount != ZERO else {}  # noqa: E501
        starting_liabilities = {A_DAI: self.debt} if self.debt.amount != ZERO else {}
        return BalanceSheet(
            assets=defaultdict(Balance, starting_assets),  # type: ignore # Doesn't recognize that the defaultdict CryptoAsset is an Asset  # noqa: E501
            liabilities=defaultdict(Balance, starting_liabilities),
        )


class MakerdaoVaultDetails(NamedTuple):
    identifier: int
    collateral_asset: CryptoAsset  # the vault's collateral asset
    creation_ts: Timestamp
    # Total amount of DAI owed to the vault, past and future as interest rate
    # Will be negative if vault has been liquidated. If it's negative then this
    # is the amount of DAI you managed to keep after liquidation.
    total_interest_owed: FVal
    # The total amount/usd_value of collateral that got liquidated
    total_liquidated: Balance
    events: list[VaultEvent]


class MakerdaoVaults(HasDSProxy):

    def __init__(
            self,
            ethereum_inquirer: 'EthereumInquirer',
            database: 'DBHandler',
            premium: Optional[Premium],
            msg_aggregator: MessagesAggregator,
    ) -> None:

        super().__init__(
            ethereum_inquirer=ethereum_inquirer,
            database=database,
            premium=premium,
            msg_aggregator=msg_aggregator,
        )
        self.reset_last_query_ts()
        self.lock = Semaphore()
        self.usd_price: dict[str, FVal] = defaultdict(FVal)
        self.vault_mappings: dict[ChecksumEvmAddress, list[MakerdaoVault]] = defaultdict(list)
        self.ilk_to_stability_fee: dict[bytes, FVal] = {}
        self.vault_details: list[MakerdaoVaultDetails] = []
        self.collateral_type_mapping = create_collateral_type_mapping()
        self.dai = A_DAI.resolve_to_evm_token()
        self.gemjoin_mapping = {
            'BAT-A': self.ethereum.contracts.contract('MAKERDAO_BAT_A_JOIN'),
            'ETH-A': self.ethereum.contracts.contract('MAKERDAO_ETH_A_JOIN'),
            'ETH-B': self.ethereum.contracts.contract('MAKERDAO_ETH_B_JOIN'),
            'ETH-C': self.ethereum.contracts.contract('MAKERDAO_ETH_C_JOIN'),
            'KNC-A': self.ethereum.contracts.contract('MAKERDAO_KNC_A_JOIN'),
            'TUSD-A': self.ethereum.contracts.contract('MAKERDAO_TUSD_A_JOIN'),
            'USDC-A': self.ethereum.contracts.contract('MAKERDAO_USDC_A_JOIN'),
            'USDC-B': self.ethereum.contracts.contract('MAKERDAO_USDC_B_JOIN'),
            'USDT-A': self.ethereum.contracts.contract('MAKERDAO_USDT_A_JOIN'),
            'WBTC-A': self.ethereum.contracts.contract('MAKERDAO_WBTC_A_JOIN'),
            'WBTC-B': self.ethereum.contracts.contract('MAKERDAO_WBTC_B_JOIN'),
            'WBTC-C': self.ethereum.contracts.contract('MAKERDAO_WBTC_C_JOIN'),
            'ZRX-A': self.ethereum.contracts.contract('MAKERDAO_ZRX_A_JOIN'),
            'MANA-A': self.ethereum.contracts.contract('MAKERDAO_MANA_A_JOIN'),
            'PAXUSD-A': self.ethereum.contracts.contract('MAKERDAO_PAXUSD_A_JOIN'),
            'COMP-A': self.ethereum.contracts.contract('MAKERDAO_COMP_A_JOIN'),
            'LRC-A': self.ethereum.contracts.contract('MAKERDAO_LRC_A_JOIN'),
            'LINK-A': self.ethereum.contracts.contract('MAKERDAO_LINK_A_JOIN'),
            'BAL-A': self.ethereum.contracts.contract('MAKERDAO_BAL_A_JOIN'),
            'YFI-A': self.ethereum.contracts.contract('MAKERDAO_YFI_A_JOIN'),
            'GUSD-A': self.ethereum.contracts.contract('MAKERDAO_GUSD_A_JOIN'),
            'UNI-A': self.ethereum.contracts.contract('MAKERDAO_UNI_A_JOIN'),
            'RENBTC-A': self.ethereum.contracts.contract('MAKERDAO_RENBTC_A_JOIN'),
            'AAVE-A': self.ethereum.contracts.contract('MAKERDAO_AAVE_A_JOIN'),
            'MATIC-A': self.ethereum.contracts.contract('MAKERDAO_MATIC_A_JOIN'),
        }
        self.makerdao_jug = self.ethereum.contracts.contract('MAKERDAO_JUG')
        self.makerdao_vat = self.ethereum.contracts.contract('MAKERDAO_VAT')
        self.makerdao_cdp_manager = self.ethereum.contracts.contract('MAKERDAO_CDP_MANAGER')
        self.makerdao_get_cdps = self.ethereum.contracts.contract('MAKERDAO_GET_CDPS')
        self.makerdao_dai_join = self.ethereum.contracts.contract('MAKERDAO_DAI_JOIN')
        self.makerdao_cat = self.ethereum.contracts.contract('MAKERDAO_CAT')
        self.makerdao_spot = self.ethereum.contracts.contract('MAKERDAO_SPOT')

    def reset_last_query_ts(self) -> None:
        """Reset the last query timestamps, effectively cleaning the caches"""
        super().reset_last_query_ts()
        self.last_vault_mapping_query_ts = 0
        self.last_vault_details_query_ts = 0

    def get_stability_fee(self, ilk: bytes) -> FVal:
        """If we already know the current stability_fee for ilk return it. If not query it"""
        if ilk in self.ilk_to_stability_fee:
            return self.ilk_to_stability_fee[ilk]

        result = self.makerdao_jug.call(self.ethereum, 'ilks', arguments=[ilk])
        # result[0] is the duty variable of the ilks in the contract
        stability_fee = FVal(result[0] / RAY) ** (YEAR_IN_SECONDS) - 1
        return stability_fee

    def _query_vault_data(
            self,
            identifier: int,
            owner: ChecksumEvmAddress,
            urn: ChecksumEvmAddress,
            ilk: bytes,
    ) -> Optional[MakerdaoVault]:
        collateral_type = ilk.split(b'\0', 1)[0].decode()
        asset = self.collateral_type_mapping.get(collateral_type, None)
        if asset is None:
            self.msg_aggregator.add_warning(
                f'Detected vault with collateral_type {collateral_type}. That '
                f'is not yet supported by rotki. Skipping...',
            )
            return None

        result = self.makerdao_vat.call(self.ethereum, 'urns', arguments=[ilk, urn])
        # also known as ink in their contract
        collateral_amount = FVal(result[0] / WAD)
        normalized_debt = result[1]  # known as art in their contract
        result = self.makerdao_vat.call(self.ethereum, 'ilks', arguments=[ilk])
        rate = result[1]  # Accumulated Rates
        spot = FVal(result[2])  # Price with Safety Margin
        # How many DAI owner needs to pay back to the vault
        debt_value = FVal(((normalized_debt / WAD) * rate) / RAY)
        result = self.makerdao_spot.call(self.ethereum, 'ilks', arguments=[ilk])
        mat = result[1]
        liquidation_ratio = FVal(mat / RAY)
        price = FVal((spot / RAY) * liquidation_ratio)
        self.usd_price[asset.identifier] = price
        collateral_value = FVal(price * collateral_amount)
        if debt_value == 0:
            collateralization_ratio = None
        else:
            collateralization_ratio = FVal(collateral_value / debt_value).to_percentage(2)

        collateral_usd_value = price * collateral_amount
        if collateral_amount == 0:
            liquidation_price = None
        else:
            liquidation_price = (debt_value * liquidation_ratio) / collateral_amount

        dai_usd_price = Inquirer().find_usd_price(A_DAI)
        return MakerdaoVault(
            identifier=identifier,
            owner=owner,
            collateral_type=collateral_type,
            collateral_asset=asset,
            collateral=Balance(collateral_amount, collateral_usd_value),
            debt=Balance(debt_value, dai_usd_price * debt_value),
            liquidation_ratio=liquidation_ratio,
            collateralization_ratio=collateralization_ratio,
            liquidation_price=liquidation_price,
            urn=urn,
            stability_fee=self.get_stability_fee(ilk),
        )

    def _query_vault_details(
            self,
            vault: MakerdaoVault,
            proxy: ChecksumEvmAddress,
            urn: ChecksumEvmAddress,
    ) -> Optional[MakerdaoVaultDetails]:
        # They can raise:
        # DeserializationError due to hex_or_bytes_to_address, hexstr_to_int
        # RemoteError due to external query errors
        events = self.makerdao_cdp_manager.get_logs_since_deployment(
            node_inquirer=self.ethereum,
            event_name='NewCdp',
            argument_filters={'cdp': vault.identifier},
        )
        if len(events) == 0:
            self.msg_aggregator.add_error(
                'No events found for a Vault creation. This should never '
                'happen. Please open a bug report: https://github.com/rotki/rotki/issues',
            )
            return None
        if len(events) != 1:
            log.error(
                f'Multiple events found for a Vault creation: {events}. Taking '
                f'only the first. This should not happen. Something is wrong',
            )
            self.msg_aggregator.add_error(
                'Multiple events found for a Vault creation. This should never '
                'happen. Please open a bug report: https://github.com/rotki/rotki/issues',
            )
        creation_ts = self.ethereum.get_event_timestamp(events[0])

        # get vat frob events for cross-checking
        argument_filters = {
            'sig': '0x76088703',  # frob
            'arg1': '0x' + vault.ilk.hex(),  # ilk
            'arg2': address_to_bytes32(urn),  # urn
            # arg3 can be urn for the 1st deposit, and proxy/owner for the next ones
            # so don't filter for it
            # 'arg3': address_to_bytes32(proxy),  # proxy - owner
        }
        frob_events = self.makerdao_vat.get_logs_since_deployment(
            node_inquirer=self.ethereum,
            event_name='LogNote',
            argument_filters=argument_filters,
        )
        frob_event_tx_hashes = [x['transactionHash'] for x in frob_events]

        gemjoin = self.gemjoin_mapping.get(vault.collateral_type, None)
        if gemjoin is None:
            self.msg_aggregator.add_warning(
                f'Unknown makerdao vault collateral type detected {vault.collateral_type}.'
                'Skipping ...',
            )
            return None

        vault_events = []
        # Get the collateral deposit events
        argument_filters = {
            'sig': '0x3b4da69f',  # join
            # In cases where a CDP has been migrated from a SAI CDP to a DAI
            # Vault the usr in the first deposit will be the old address. To
            # detect the first deposit in these cases we need to check for
            # arg1 being the urn so we skip: 'usr': proxy,
            'arg1': address_to_bytes32(urn),
        }
        events = self.ethereum.get_logs(
            contract_address=gemjoin.address,
            abi=gemjoin.abi,
            event_name='LogNote',
            argument_filters=argument_filters,
            from_block=gemjoin.deployed_block,
        )
        # all subsequent deposits should have the proxy as a usr
        # but for non-migrated CDPS the previous query would also work
        # so in those cases we will have the first deposit 2 times
        argument_filters = {
            'sig': '0x3b4da69f',  # join
            'usr': proxy,
        }
        events.extend(self.ethereum.get_logs(
            contract_address=gemjoin.address,
            abi=gemjoin.abi,
            event_name='LogNote',
            argument_filters=argument_filters,
            from_block=gemjoin.deployed_block,
        ))
        deposit_tx_hashes = set()
        for event in events:
            tx_hash = event['transactionHash']
            if tx_hash in deposit_tx_hashes:
                # Skip duplicate deposit that would be detected in non migrated CDP case
                continue

            if tx_hash not in frob_event_tx_hashes:
                # If there is no corresponding frob event then skip
                continue

            deposit_tx_hashes.add(tx_hash)
            amount = asset_normalized_value(
                amount=hexstr_to_int(event['topics'][3]),
                asset=vault.collateral_asset,
            )
            timestamp = self.ethereum.get_event_timestamp(event)
            usd_price = query_usd_price_or_use_default(
                asset=vault.collateral_asset,
                time=timestamp,
                default_value=ZERO,
                location='vault collateral deposit',
            )
            vault_events.append(VaultEvent(
                event_type=VaultEventType.DEPOSIT_COLLATERAL,
                value=Balance(amount, amount * usd_price),
                timestamp=timestamp,
                tx_hash=tx_hash,
            ))

        # Get the collateral withdrawal events
        argument_filters = {
            'sig': '0xef693bed',  # exit
            'usr': proxy,
        }
        events = self.ethereum.get_logs(
            contract_address=gemjoin.address,
            abi=gemjoin.abi,
            event_name='LogNote',
            argument_filters=argument_filters,
            from_block=gemjoin.deployed_block,
        )
        for event in events:
            tx_hash = event['transactionHash']
            if tx_hash not in frob_event_tx_hashes:
                # If there is no corresponding frob event then skip
                continue
            amount = asset_normalized_value(
                amount=hexstr_to_int(event['topics'][3]),
                asset=vault.collateral_asset,
            )
            timestamp = self.ethereum.get_event_timestamp(event)
            usd_price = query_usd_price_or_use_default(
                asset=vault.collateral_asset,
                time=timestamp,
                default_value=ZERO,
                location='vault collateral withdrawal',
            )
            vault_events.append(VaultEvent(
                event_type=VaultEventType.WITHDRAW_COLLATERAL,
                value=Balance(amount, amount * usd_price),
                timestamp=timestamp,
                tx_hash=event['transactionHash'],
            ))

        total_dai_wei = 0
        # Get the dai generation events
        argument_filters = {
            'sig': '0xbb35783b',  # move
            'arg1': address_to_bytes32(urn),
            # For CDPs that were created by migrating from SAI the first DAI generation
            # during vault creation will have the old owner as arg2. So we can't
            # filter for it here. Still seems like the urn as arg1 is sufficient
            # so we skip: 'arg2': address_to_bytes32(proxy),
        }
        events = self.makerdao_vat.get_logs_since_deployment(
            node_inquirer=self.ethereum,
            event_name='LogNote',
            argument_filters=argument_filters,
        )
        for event in events:
            given_amount = shift_num_right_by(hexstr_to_int(event['topics'][3]), RAY_DIGITS)
            total_dai_wei += given_amount
            amount = token_normalized_value(
                token_amount=given_amount,
                token=self.dai,
            )
            timestamp = self.ethereum.get_event_timestamp(event)
            usd_price = query_usd_price_or_use_default(
                asset=A_DAI,
                time=timestamp,
                default_value=ONE,
                location='vault debt generation',
            )
            vault_events.append(VaultEvent(
                event_type=VaultEventType.GENERATE_DEBT,
                value=Balance(amount, amount * usd_price),
                timestamp=timestamp,
                tx_hash=event['transactionHash'],
            ))

        # Get the dai payback events
        argument_filters = {
            'sig': '0x3b4da69f',  # join
            'usr': proxy,
            'arg1': address_to_bytes32(urn),
        }
        events = self.makerdao_dai_join.get_logs_since_deployment(
            node_inquirer=self.ethereum,
            event_name='LogNote',
            argument_filters=argument_filters,
        )
        for event in events:
            given_amount = hexstr_to_int(event['topics'][3])
            total_dai_wei -= given_amount
            amount = token_normalized_value(
                token_amount=given_amount,
                token=self.dai,
            )
            if amount == ZERO:
                # it seems there is a zero DAI value transfer from the urn when
                # withdrawing ETH. So we should ignore these as events
                continue

            timestamp = self.ethereum.get_event_timestamp(event)
            usd_price = query_usd_price_or_use_default(
                asset=A_DAI,
                time=timestamp,
                default_value=ONE,
                location='vault debt payback',
            )

            vault_events.append(VaultEvent(
                event_type=VaultEventType.PAYBACK_DEBT,
                value=Balance(amount, amount * usd_price),
                timestamp=timestamp,
                tx_hash=event['transactionHash'],
            ))

        # Get the liquidation events
        argument_filters = {'urn': urn}
        events = self.makerdao_cat.get_logs_since_deployment(
            node_inquirer=self.ethereum,
            event_name='Bite',
            argument_filters=argument_filters,
        )
        sum_liquidation_amount = ZERO
        sum_liquidation_usd = ZERO
        for event in events:
            if isinstance(event['data'], str):
                lot = event['data'][:66]
            else:  # bytes
                lot = event['data'][:32]
            amount = asset_normalized_value(
                amount=hexstr_to_int(lot),
                asset=vault.collateral_asset,
            )
            timestamp = self.ethereum.get_event_timestamp(event)
            sum_liquidation_amount += amount
            usd_price = query_usd_price_or_use_default(
                asset=vault.collateral_asset,
                time=timestamp,
                default_value=ZERO,
                location='vault collateral liquidation',
            )
            amount_usd_value = amount * usd_price
            sum_liquidation_usd += amount_usd_value
            vault_events.append(VaultEvent(
                event_type=VaultEventType.LIQUIDATION,
                value=Balance(amount, amount_usd_value),
                timestamp=timestamp,
                tx_hash=event['transactionHash'],
            ))

        total_interest_owed = vault.debt.amount - token_normalized_value(
            token_amount=total_dai_wei,
            token=self.dai,
        )
        # sort vault events by timestamp
        vault_events.sort(key=lambda event: event.timestamp)

        return MakerdaoVaultDetails(
            identifier=vault.identifier,
            collateral_asset=vault.collateral_asset,
            total_interest_owed=total_interest_owed,
            creation_ts=creation_ts,
            total_liquidated=Balance(sum_liquidation_amount, sum_liquidation_usd),
            events=vault_events,
        )

    def _get_vaults_of_address(
            self,
            user_address: ChecksumEvmAddress,
            proxy_address: ChecksumEvmAddress,
    ) -> list[MakerdaoVault]:
        """Gets the vaults of a single address

        May raise:
        - RemoteError if etherscan is used and there is a problem with
        reaching it or with the returned result.
        - BlockchainQueryError if an ethereum node is used and the contract call
        queries fail for some reason
        """
        result = self.makerdao_get_cdps.call(
            node_inquirer=self.ethereum,
            method_name='getCdpsAsc',
            arguments=[self.makerdao_cdp_manager.address, proxy_address],
        )

        vaults = []
        for idx, identifier in enumerate(result[0]):
            try:
                urn = deserialize_evm_address(result[1][idx])
            except DeserializationError as e:
                raise RemoteError(
                    f'Failed to deserialize address {result[1][idx]} '
                    f'when processing vaults of {user_address}',
                ) from e
            vault = self._query_vault_data(
                identifier=identifier,
                owner=user_address,
                urn=urn,
                ilk=result[2][idx],
            )
            if vault:
                vaults.append(vault)
                self.vault_mappings[user_address].append(vault)

        return vaults

    def get_vaults(self) -> list[MakerdaoVault]:
        """Detects vaults the user has and returns basic info about each one

        If the vaults have been queried in the past REQUERY_PERIOD
        seconds then the old result is used.

        May raise:
        - RemoteError if etherscan is used and there is a problem with
        reaching it or with the returned result.
        - BlockchainQueryError if an ethereum node is used and the contract call
        queries fail for some reason
        """
        now = ts_now()
        if now - self.last_vault_mapping_query_ts < MAKERDAO_REQUERY_PERIOD:
            prequeried_vaults = []
            for _, vaults in self.vault_mappings.items():
                prequeried_vaults.extend(vaults)

            prequeried_vaults.sort(key=lambda vault: vault.identifier)
            return prequeried_vaults

        with self.lock:
            self.vault_mappings = defaultdict(list)
            proxy_mappings = self._get_accounts_having_proxy()
            vaults = []
            for user_address, proxy in proxy_mappings.items():
                vaults.extend(
                    self._get_vaults_of_address(user_address=user_address, proxy_address=proxy),
                )

            self.last_vault_mapping_query_ts = ts_now()
            # Returns vaults sorted. Oldest identifier first
            vaults.sort(key=lambda vault: vault.identifier)
        return vaults

    def get_vault_details(self) -> list[MakerdaoVaultDetails]:
        """Queries vault details for the auto detected vaults of the user

        This is a premium only call. Check happens only at the API level.

        If the details have been queried in the past REQUERY_PERIOD
        seconds then the old result is used.

        May raise:
        - RemoteError if etherscan is used and there is a problem with
        reaching it or with the returned result.
        - BlockchainQueryError if an ethereum node is used and the contract call
        queries fail for some reason
        """
        now = ts_now()
        if now - self.last_vault_details_query_ts < MAKERDAO_REQUERY_PERIOD:
            return self.vault_details

        self.vault_details = []
        proxy_mappings = self._get_accounts_having_proxy()
        # Make sure that before querying vault details there has been a recent vaults call
        vaults = self.get_vaults()
        for vault in vaults:
            proxy = proxy_mappings[vault.owner]
            vault_detail = self._query_vault_details(vault, proxy, vault.urn)
            if vault_detail:
                self.vault_details.append(vault_detail)

        # Returns vault details sorted. Oldest identifier first
        self.vault_details.sort(key=lambda details: details.identifier)
        self.last_vault_details_query_ts = ts_now()
        return self.vault_details

    def get_history_events(
            self,
            from_timestamp: Timestamp,
            to_timestamp: Timestamp,
    ) -> list[DefiEvent]:
        """Gets the history events from maker vaults for accounting

            This is a premium only call. Check happens only in the API level.
        """
        vault_details = self.get_vault_details()
        events = []
        for detail in vault_details:
            total_vault_dai_balance = Balance()
            realized_vault_dai_loss = Balance()
            for event in detail.events:
                timestamp = event.timestamp
                if timestamp < from_timestamp:
                    continue
                if timestamp > to_timestamp:
                    break

                got_asset: Optional[CryptoAsset]
                spent_asset: Optional[CryptoAsset]
                pnl = got_asset = got_balance = spent_asset = spent_balance = None
                count_spent_got_cost_basis = False
                if event.event_type == VaultEventType.GENERATE_DEBT:
                    count_spent_got_cost_basis = True
                    got_asset = A_DAI.resolve_to_crypto_asset()
                    got_balance = event.value
                    total_vault_dai_balance += event.value
                elif event.event_type == VaultEventType.PAYBACK_DEBT:
                    count_spent_got_cost_basis = True
                    spent_asset = A_DAI.resolve_to_crypto_asset()
                    spent_balance = event.value
                    total_vault_dai_balance -= event.value
                    if total_vault_dai_balance.amount + realized_vault_dai_loss.amount < ZERO:
                        pnl_balance = total_vault_dai_balance + realized_vault_dai_loss
                        realized_vault_dai_loss += -pnl_balance
                        pnl = [AssetBalance(asset=A_DAI, balance=pnl_balance)]

                elif event.event_type == VaultEventType.DEPOSIT_COLLATERAL:
                    spent_asset = detail.collateral_asset
                    spent_balance = event.value
                elif event.event_type == VaultEventType.WITHDRAW_COLLATERAL:
                    got_asset = detail.collateral_asset
                    got_balance = event.value
                elif event.event_type == VaultEventType.LIQUIDATION:
                    count_spent_got_cost_basis = True
                    # TODO: Don't you also get the dai here -- but how to calculate it?
                    spent_asset = detail.collateral_asset
                    spent_balance = event.value
                    pnl = [AssetBalance(asset=detail.collateral_asset, balance=-spent_balance)]
                else:
                    raise AssertionError(f'Invalid Makerdao vault event type {event.event_type}')

                events.append(DefiEvent(
                    timestamp=timestamp,
                    wrapped_event=event,
                    event_type=DefiEventType.MAKERDAO_VAULT_EVENT,
                    got_asset=got_asset,
                    got_balance=got_balance,
                    spent_asset=spent_asset,
                    spent_balance=spent_balance,
                    pnl=pnl,
                    # Depositing and withdrawing from a vault is not counted in
                    # cost basis. Assets were always yours, you did not rebuy them.
                    # Other actions are counted though to track debt and liquidations
                    count_spent_got_cost_basis=count_spent_got_cost_basis,
                    tx_hash=event.tx_hash,
                ))

        return events

    def get_balances(self) -> dict[ChecksumEvmAddress, BalanceSheet]:
        """Return a mapping of all assets locked as collateral in the vaults and
        all DAI owed as debt
        """
        balances: DefaultDict[ChecksumEvmAddress, BalanceSheet] = defaultdict(BalanceSheet)
        for vault in self.get_vaults():
            balances[vault.owner] += vault.get_balance()
        return balances

    # -- Methods following the EthereumModule interface -- #
    def on_account_addition(self, address: ChecksumEvmAddress) -> None:  # pylint: disable=useless-return  # noqa: E501
        super().on_account_addition(address)
        # Check if it has been added to the mapping
        proxy_address = self.address_to_proxy.get(address)
        if proxy_address:
            # get any vaults the proxy owns
            self._get_vaults_of_address(user_address=address, proxy_address=proxy_address)
        return None
