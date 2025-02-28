import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any, DefaultDict, NamedTuple, Optional

from gevent.lock import Semaphore

from rotkehlchen.accounting.structures.balance import AssetBalance, Balance
from rotkehlchen.chain.ethereum.defi.defisaver_proxy import HasDSProxy
from rotkehlchen.chain.ethereum.utils import token_normalized_value_decimals
from rotkehlchen.constants.assets import A_ETH, A_LQTY, A_LUSD
from rotkehlchen.errors.misc import BlockchainQueryError, RemoteError
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.fval import FVal
from rotkehlchen.inquirer import Inquirer
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.premium.premium import Premium
from rotkehlchen.serialization.deserialize import deserialize_asset_amount
from rotkehlchen.types import ChecksumEvmAddress
from rotkehlchen.user_messages import MessagesAggregator


if TYPE_CHECKING:
    from rotkehlchen.assets.asset import Asset
    from rotkehlchen.chain.ethereum.node_inquirer import EthereumInquirer
    from rotkehlchen.chain.evm.contracts import EvmContract
    from rotkehlchen.db.dbhandler import DBHandler

MIN_COLL_RATE = '1.1'

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


class Trove(NamedTuple):
    collateral: AssetBalance
    debt: AssetBalance
    collateralization_ratio: Optional[FVal]
    liquidation_price: Optional[FVal]
    active: bool
    trove_id: int

    def serialize(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        result['collateral'] = self.collateral.serialize()
        result['debt'] = self.debt.serialize()
        result['collateralization_ratio'] = self.collateralization_ratio
        result['liquidation_price'] = self.liquidation_price
        result['active'] = self.active
        result['trove_id'] = self.trove_id
        return result


class Liquity(HasDSProxy):

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
        self.history_lock = Semaphore()
        self.trove_manager_contract = self.ethereum.contracts.contract('LIQUITY_TROVE_MANAGER')
        self.stability_pool_contract = self.ethereum.contracts.contract('LIQUITY_STABILITY_POOL')
        self.staking_contract = self.ethereum.contracts.contract('LIQUITY_STAKING')

    def get_positions(
            self,
            addresses_list: list[ChecksumEvmAddress],
    ) -> dict[ChecksumEvmAddress, Trove]:
        """Query liquity contract to detect open troves"""
        # make a copy of the list to avoid modifications in the list that is passed as argument
        addresses = addresses_list.copy()
        proxied_addresses = self._get_accounts_having_proxy()
        proxies_to_address = {v: k for k, v in proxied_addresses.items()}
        addresses += proxied_addresses.values()

        calls = [
            (self.trove_manager_contract.address, self.trove_manager_contract.encode(method_name='Troves', arguments=[x]))  # noqa: E501
            for x in addresses
        ]
        outputs = self.ethereum.multicall_2(
            require_success=False,
            calls=calls,
        )

        data: dict[ChecksumEvmAddress, Trove] = {}
        eth_price = Inquirer().find_usd_price(A_ETH)
        lusd_price = Inquirer().find_usd_price(A_LUSD)
        for idx, output in enumerate(outputs):
            status, result = output
            if status is True:
                try:
                    trove_info = self.trove_manager_contract.decode(result, 'Troves', arguments=[addresses[idx]])  # noqa: E501
                    trove_is_active = bool(trove_info[3])  # pylint: disable=unsubscriptable-object
                    if not trove_is_active:
                        continue
                    collateral = deserialize_asset_amount(
                        token_normalized_value_decimals(trove_info[1], 18),  # noqa: E501 pylint: disable=unsubscriptable-object
                    )
                    debt = deserialize_asset_amount(
                        token_normalized_value_decimals(trove_info[0], 18),  # noqa: E501 pylint: disable=unsubscriptable-object
                    )
                    collateral_balance = AssetBalance(
                        asset=A_ETH,
                        balance=Balance(
                            amount=collateral,
                            usd_value=eth_price * collateral,
                        ),
                    )
                    debt_balance = AssetBalance(
                        asset=A_LUSD,
                        balance=Balance(
                            amount=debt,
                            usd_value=lusd_price * debt,
                        ),
                    )
                    # Avoid division errors
                    collateralization_ratio: Optional[FVal]
                    liquidation_price: Optional[FVal]
                    if debt > 0:
                        collateralization_ratio = eth_price * collateral / debt * 100
                    else:
                        collateralization_ratio = None
                    if collateral > 0:
                        liquidation_price = debt * lusd_price * FVal(MIN_COLL_RATE) / collateral
                    else:
                        liquidation_price = None

                    account_address = addresses[idx]
                    if account_address in proxies_to_address:
                        account_address = proxies_to_address[account_address]
                    data[account_address] = Trove(
                        collateral=collateral_balance,
                        debt=debt_balance,
                        collateralization_ratio=collateralization_ratio,
                        liquidation_price=liquidation_price,
                        active=trove_is_active,
                        trove_id=trove_info[4],  # pylint: disable=unsubscriptable-object
                    )
                except DeserializationError as e:
                    self.msg_aggregator.add_warning(
                        f'Ignoring Liquity trove information. '
                        f'Failed to decode contract information. {str(e)}.',
                    )
        return data

    def _query_deposits_and_rewards(
            self,
            contract: 'EvmContract',
            addresses: list[ChecksumEvmAddress],
            methods: tuple[str, str, str],
            keys: tuple[str, str, str],
            assets: tuple['Asset', 'Asset', 'Asset'],
    ) -> dict[ChecksumEvmAddress, dict[str, AssetBalance]]:
        """
        For Liquity staking contracts there is always one asset that we stake and two other assets
        for rewards. This method abstracts the logic of querying the staked amount and the
        rewards for both the stability pool and the LQTY staking.

        - addresses: The addresses that will be queried
        - methods: the methods that need to be queried to get the staked amount and rewards
        - keys: the keys used in the dict response to map each method
        - assets: the asset associated with each method called
        """
        # make a copy of the list to avoid modifications in the list that is passed as argument
        addresses = addresses.copy()
        proxied_addresses = self._get_accounts_having_proxy()
        addresses += proxied_addresses.values()

        # Build the calls that need to be made in order to get the status in the SP
        calls = []
        for address in addresses:
            for method in methods:
                calls.append(
                    (contract.address, contract.encode(method_name=method, arguments=[address])),
                )

        try:
            outputs = self.ethereum.multicall_2(
                require_success=False,
                calls=calls,
            )
        except (RemoteError, BlockchainQueryError) as e:
            self.msg_aggregator.add_error(
                f'Failed to query information about stability pool {str(e)}',
            )
            return {}

        # the structure of the queried data is:
        # staked address 1, reward 1 of address 1, reward 2 of address 1, staked address 2, reward 1 of address 2, ...  # noqa: E501
        data: DefaultDict[ChecksumEvmAddress, dict[str, AssetBalance]] = defaultdict(dict)
        for idx, output in enumerate(outputs):
            # depending on the output index get the address we are tracking
            current_address = addresses[idx // 3]
            status, result = output
            if status is False:
                continue

            # make sure that variables always have a value set. It is guaranteed that the response
            # will have the desired format because we include and process failed queries.
            key, asset, gain_info = keys[0], assets[0], 0
            for method_idx, (method, _asset, _key) in enumerate(zip(methods, assets, keys)):
                # get the asset, key used in the response and the amount based on the index
                # for this address
                if idx % 3 == method_idx:
                    asset = _asset
                    key = _key
                    gain_info = contract.decode(result, method, arguments=[current_address])[0]    # pylint: disable=unsubscriptable-object  # noqa: E501
                    break

            # get price information for the asset and deserialize the amount
            asset_price = Inquirer().find_usd_price(asset)
            amount = deserialize_asset_amount(
                token_normalized_value_decimals(gain_info, 18),
            )
            data[current_address][key] = AssetBalance(
                asset=asset,
                balance=Balance(
                    amount=amount,
                    usd_value=asset_price * amount,
                ),
            )

        return data

    def get_stability_pool_balances(
            self,
            addresses: list[ChecksumEvmAddress],
    ) -> dict[ChecksumEvmAddress, dict[str, AssetBalance]]:
        return self._query_deposits_and_rewards(
            contract=self.stability_pool_contract,
            addresses=addresses,
            methods=('getDepositorETHGain', 'getDepositorLQTYGain', 'getCompoundedLUSDDeposit'),
            keys=('gains', 'rewards', 'deposited'),
            assets=(A_ETH, A_LQTY, A_LUSD),
        )

    def liquity_staking_balances(
            self,
            addresses: list[ChecksumEvmAddress],
    ) -> dict[ChecksumEvmAddress, dict[str, AssetBalance]]:
        """
        Query the ethereum chain to retrieve information about staked assets
        """
        return self._query_deposits_and_rewards(
            contract=self.staking_contract,
            addresses=addresses,
            methods=('stakes', 'getPendingLUSDGain', 'getPendingETHGain'),
            keys=('staked', 'lusd_rewards', 'eth_rewards'),
            assets=(A_LQTY, A_LUSD, A_ETH),
        )
