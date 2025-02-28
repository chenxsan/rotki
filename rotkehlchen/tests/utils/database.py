import os
import random
from dataclasses import asdict
from pathlib import Path
from shutil import copyfile
from typing import Any, Optional
from unittest.mock import _patch, patch

from rotkehlchen.assets.asset import Asset
from rotkehlchen.balances.manual import ManuallyTrackedBalance
from rotkehlchen.chain.accounts import BlockchainAccountData, BlockchainAccounts
from rotkehlchen.db.dbhandler import DBHandler
from rotkehlchen.db.settings import ModifiableDBSettings
from rotkehlchen.errors.misc import InputError
from rotkehlchen.tests.utils.constants import DEFAULT_TESTS_MAIN_CURRENCY
from rotkehlchen.types import (
    ApiKey,
    ExternalService,
    ExternalServiceApiCredentials,
    SupportedBlockchain,
)


def maybe_include_etherscan_key(db: DBHandler, include_etherscan_key: bool) -> None:
    if not include_etherscan_key:
        return
    # Add the tests only etherscan API key
    db.add_external_service_credentials([ExternalServiceApiCredentials(
        service=ExternalService.ETHERSCAN,
        api_key=ApiKey('8JT7WQBB2VQP5C3416Y8X3S8GBA3CVZKP4'),
    )])
    db.add_external_service_credentials([ExternalServiceApiCredentials(
        service=ExternalService.OPTIMISM_ETHERSCAN,
        api_key=ApiKey('IK3GCCMPQXTRIEGBAXQ9DUFWIUR524K3MW'),
    )])


def maybe_include_cryptocompare_key(db: DBHandler, include_cryptocompare_key: bool) -> None:
    if not include_cryptocompare_key:
        return
    keys = [
        'a4a36d7fd1835cc1d757186de8e7357b4478b73923933d09d3689140ecc23c03',
        'e929bcf68fa28715fa95f3bfa3baa3b9a6bc8f12112835586c705ab038ee06aa',
        '5159ca00f2579ef634b7f210ad725550572afbfb44e409460dd8a908d1c6416a',
        '6781b638eca6c3ca51a87efcdf0b9032397379a0810c5f8198a25493161c318d',
    ]
    # Add the tests only etherscan API key
    db.add_external_service_credentials([ExternalServiceApiCredentials(
        service=ExternalService.CRYPTOCOMPARE,
        api_key=ApiKey(random.choice(keys)),
    )])


def add_blockchain_accounts_to_db(db: DBHandler, blockchain_accounts: BlockchainAccounts) -> None:
    try:
        with db.user_write() as cursor:
            for name, value in asdict(blockchain_accounts).items():
                db.add_blockchain_accounts(
                    write_cursor=cursor,
                    account_data=[BlockchainAccountData(
                        chain=SupportedBlockchain(name.upper()),
                        address=x,
                    ) for x in value],
                )
    except InputError as e:
        raise AssertionError(
            f'Got error at test setup blockchain account addition: {str(e)} '
            f'Probably using two different databases or too many fixtures initialized. '
            f'For example do not initialize both a rotki api server and another DB at same time',
        ) from e


def add_settings_to_test_db(
        db: DBHandler,
        db_settings: Optional[dict[str, Any]],
        ignored_assets: Optional[list[Asset]],
        data_migration_version: Optional[int],
) -> None:
    settings = {
        # DO not submit usage analytics during tests
        'submit_usage_analytics': False,
        'main_currency': DEFAULT_TESTS_MAIN_CURRENCY,
    }
    # Set the given db_settings. The pre-set values have priority unless overriden here
    if db_settings is not None:
        for key, value in db_settings.items():
            settings[key] = value
    with db.user_write() as cursor:
        db.set_settings(cursor, ModifiableDBSettings(**settings))  # type: ignore

    if ignored_assets:
        for asset in ignored_assets:
            db.add_to_ignored_assets(asset)

    if data_migration_version is not None:
        db.conn.cursor().execute(
            'INSERT OR REPLACE INTO settings(name, value) VALUES(?, ?)',
            ('last_data_migration', data_migration_version),
        )
        db.conn.commit()


def add_tags_to_test_db(db: DBHandler, tags: list[dict[str, Any]]) -> None:
    with db.user_write() as cursor:
        for tag in tags:
            db.add_tag(
                cursor,
                name=tag['name'],
                description=tag.get('description', None),
                background_color=tag['background_color'],
                foreground_color=tag['foreground_color'],
            )


def add_manually_tracked_balances_to_test_db(
        db: DBHandler,
        balances: list[ManuallyTrackedBalance],
) -> None:
    with db.user_write() as cursor:
        db.add_manually_tracked_balances(cursor, balances)


def mock_dbhandler_update_owned_assets() -> _patch:
    """Just make sure update owned assets does nothing for older DB tests"""
    return patch(
        'rotkehlchen.db.dbhandler.DBHandler.update_owned_assets_in_globaldb',
        lambda x, y: None,
    )


def mock_dbhandler_add_globaldb_assetids() -> _patch:
    """Just make sure add globalds assetids does nothing for older DB tests"""
    return patch(
        'rotkehlchen.db.dbhandler.DBHandler.add_globaldb_assetids',
        lambda x, y: None,
    )


def mock_db_schema_sanity_check() -> _patch:
    return patch(
        'rotkehlchen.db.drivers.gevent.DBConnection.schema_sanity_check',
        new=lambda x: None,
    )


def _use_prepared_db(user_data_dir: Path, filename: str) -> None:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    copyfile(
        os.path.join(os.path.dirname(dir_path), 'data', filename),
        user_data_dir / 'rotkehlchen.db',
    )
