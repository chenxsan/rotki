import logging
from typing import TYPE_CHECKING, Optional

from rotkehlchen.errors.misc import RemoteError
from rotkehlchen.errors.serialization import DeserializationError
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.premium.premium import Premium
from rotkehlchen.serialization.deserialize import deserialize_evm_address
from rotkehlchen.types import ChecksumEvmAddress
from rotkehlchen.user_messages import MessagesAggregator
from rotkehlchen.utils.interfaces import EthereumModule
from rotkehlchen.utils.misc import ts_now

if TYPE_CHECKING:
    from rotkehlchen.chain.ethereum.node_inquirer import EthereumInquirer
    from rotkehlchen.db.dbhandler import DBHandler

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

DS_REQUERY_PERIOD = 7200  # Refresh queries every 2 hours


class HasDSProxy(EthereumModule):
    """
    Class to retrive information about DSProxy for defi addresses.
    It implements the EthereumModule interface to properly query proxies on account addition
    """

    def __init__(
            self,
            ethereum_inquirer: 'EthereumInquirer',
            database: 'DBHandler',
            premium: Optional[Premium],
            msg_aggregator: MessagesAggregator,
    ) -> None:
        self.premium = premium
        self.ethereum = ethereum_inquirer
        self.database = database
        self.msg_aggregator = msg_aggregator
        self.address_to_proxy: dict[ChecksumEvmAddress, ChecksumEvmAddress] = {}
        self.proxy_to_address: dict[ChecksumEvmAddress, ChecksumEvmAddress] = {}
        self.reset_last_query_ts()
        self.dsproxy_registry = self.ethereum.contracts.contract('DS_PROXY_REGISTRY')

    def reset_last_query_ts(self) -> None:
        """Reset the last query timestamps, effectively cleaning the caches"""
        self.last_proxy_mapping_query_ts = 0

    def _get_account_proxy(self, address: ChecksumEvmAddress) -> Optional[ChecksumEvmAddress]:
        """Checks if a DS proxy exists for the given address and returns it if it does

        May raise:
        - RemoteError if etherscan is used and there is a problem with
        reaching it or with the returned result. Also this error can be raised
        if there is a problem deserializing the result address.
        - BlockchainQueryError if an evm node is used and the contract call
        queries fail for some reason
        """
        result = self.dsproxy_registry.call(self.ethereum, 'proxies', arguments=[address])
        if int(result, 16) != 0:
            try:
                return deserialize_evm_address(result)
            except DeserializationError as e:
                msg = f'Failed to deserialize {result} DS proxy for address {address}'
                log.error(msg)
                raise RemoteError(msg) from e
        return None

    def _get_accounts_proxy(
        self,
        addresses: list[ChecksumEvmAddress],
    ) -> dict[ChecksumEvmAddress, ChecksumEvmAddress]:
        """
        Returns DSProxy if it exists for a list of addresses using only one call
        to the chain.

        May raise:
        - RemoteError if query to the node failed
        """
        output = self.ethereum.multicall(
            calls=[(
                self.dsproxy_registry.address,
                self.dsproxy_registry.encode(method_name='proxies', arguments=[address]),
            ) for address in addresses],
        )
        mapping = {}
        for idx, result_encoded in enumerate(output):
            address = addresses[idx]
            result = self.dsproxy_registry.decode(    # pylint: disable=unsubscriptable-object
                result_encoded,
                'proxies',
                arguments=[address],
            )[0]
            if int(result, 16) != 0:
                try:
                    proxy_address = deserialize_evm_address(result)
                    mapping[address] = proxy_address
                except DeserializationError as e:
                    msg = f'Failed to deserialize {result} DSproxy for address {address}. {str(e)}'
                    log.error(msg)
        return mapping

    def _get_accounts_having_proxy(self) -> dict[ChecksumEvmAddress, ChecksumEvmAddress]:
        """Returns a mapping of accounts that have DS proxies to their proxies

        If the proxy mappings have been queried in the past REQUERY_PERIOD
        seconds then the old result is used.

        May raise:
        - RemoteError if etherscan is used and there is a problem with
        reaching it or with the returned result.
        - BlockchainQueryError if an ethereum node is used and the contract call
        queries fail for some reason
        """
        now = ts_now()
        if now - self.last_proxy_mapping_query_ts < DS_REQUERY_PERIOD:
            return self.address_to_proxy

        with self.database.conn.read_ctx() as cursor:
            accounts = self.database.get_blockchain_accounts(cursor)
        eth_accounts = accounts.eth
        mapping = self._get_accounts_proxy(eth_accounts)

        self.last_proxy_mapping_query_ts = ts_now()
        self.address_to_proxy = mapping
        self.proxy_to_address = {v: k for k, v in mapping.items()}
        return mapping

    # -- Methods following the EthereumModule interface -- #
    def on_account_addition(self, address: ChecksumEvmAddress) -> None:
        self.reset_last_query_ts()
        # Get the proxy of the account
        proxy_result = self._get_account_proxy(address)
        if proxy_result is None:
            return None

        # add it to the mapping
        self.address_to_proxy[address] = proxy_result
        return None

    def on_account_removal(self, address: ChecksumEvmAddress) -> None:
        self.reset_last_query_ts()

    def deactivate(self) -> None:
        pass
