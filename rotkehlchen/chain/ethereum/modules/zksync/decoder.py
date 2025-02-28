from typing import Any, Optional

from rotkehlchen.accounting.structures.base import HistoryBaseEntry
from rotkehlchen.accounting.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.chain.ethereum.utils import asset_raw_value
from rotkehlchen.chain.evm.decoding.interfaces import DecoderInterface
from rotkehlchen.chain.evm.decoding.structures import ActionItem
from rotkehlchen.chain.evm.structures import EvmTxReceiptLog
from rotkehlchen.chain.evm.types import string_to_evm_address
from rotkehlchen.types import ChecksumEvmAddress, EvmTransaction
from rotkehlchen.utils.misc import hex_or_bytes_to_address, hex_or_bytes_to_int

from .constants import CPT_ZKSYNC

DEPOSIT = b'\xb6\x86k\x02\x9f:\xa2\x9c\xd9\xe2\xbf\xf8\x15\x9a\x8c\xca\xa48\x9fz\x08|q\th\xe0\xb2\x00\xc0\xc7;\x08'  # noqa: E501

ZKSYNC_BRIDGE = string_to_evm_address('0xaBEA9132b05A70803a4E85094fD0e1800777fBEF')


class ZksyncDecoder(DecoderInterface):

    def _decode_event(  # pylint: disable=no-self-use
            self,
            tx_log: EvmTxReceiptLog,
            transaction: EvmTransaction,  # pylint: disable=unused-argument
            decoded_events: list[HistoryBaseEntry],
            all_logs: list[EvmTxReceiptLog],
            action_items: list[ActionItem],
    ) -> tuple[Optional[HistoryBaseEntry], list[ActionItem]]:
        if tx_log.topics[0] == DEPOSIT:
            return self._decode_deposit(tx_log, transaction, decoded_events, all_logs, action_items)  # noqa: E501

        return None, []

    def _decode_deposit(  # pylint: disable=no-self-use
            self,
            tx_log: EvmTxReceiptLog,
            transaction: EvmTransaction,  # pylint: disable=unused-argument
            decoded_events: list[HistoryBaseEntry],  # pylint: disable=unused-argument
            all_logs: list[EvmTxReceiptLog],  # pylint: disable=unused-argument
            action_items: list[ActionItem],  # pylint: disable=unused-argument
    ) -> tuple[Optional[HistoryBaseEntry], list[ActionItem]]:
        """Match a zksync deposit with the transfer to decode it

        TODO: This is now quite bad. We don't use the token id of zksync as we should.
        Example: https://etherscan.io/tx/0xdd6d1f92980faf622c09acd84dbff4fe0bd7ae466a23c2479df709f8996d250e#eventlog
        We should include the zksync api querying module which is in this PR:
        https://github.com/rotki/rotki/pull/3985/files
        to get the ids of tokens and then match them to what is deposited.
        """  # noqa: E501
        user_address = hex_or_bytes_to_address(tx_log.topics[1])
        amount_raw = hex_or_bytes_to_int(tx_log.data)

        for event in decoded_events:
            if event.event_type == HistoryEventType.SPEND and event.location_label == user_address:
                resolved_event_asset = event.asset.resolve_to_crypto_asset()
                event_raw_amount = asset_raw_value(
                    amount=event.balance.amount,
                    asset=resolved_event_asset,
                )
                if event_raw_amount != amount_raw:
                    continue

                # found the deposit transfer
                event.event_type = HistoryEventType.DEPOSIT
                event.event_subtype = HistoryEventSubType.BRIDGE
                event.counterparty = CPT_ZKSYNC
                crypto_asset = resolved_event_asset
                event.notes = f'Deposit {event.balance.amount} {crypto_asset.symbol} to zksync'  # noqa: E501
                break

        return None, []

    # -- DecoderInterface methods

    def addresses_to_decoders(self) -> dict[ChecksumEvmAddress, tuple[Any, ...]]:
        return {
            ZKSYNC_BRIDGE: (self._decode_event,),
        }

    def counterparties(self) -> list[str]:
        return [CPT_ZKSYNC]
