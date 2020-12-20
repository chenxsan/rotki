import base64
from collections import namedtuple
from unittest.mock import patch

import pytest

import rotkehlchen.tests.utils.exchanges as exchange_tests
from rotkehlchen.db.settings import DBSettings
from rotkehlchen.history import PriceHistorian
from rotkehlchen.premium.premium import Premium, PremiumCredentials
from rotkehlchen.rotkehlchen import Rotkehlchen
from rotkehlchen.tests.utils.api import create_api_server
from rotkehlchen.tests.utils.database import (
    add_blockchain_accounts_to_db,
    add_manually_tracked_balances_to_test_db,
    add_settings_to_test_db,
    add_tags_to_test_db,
    maybe_include_cryptocompare_key,
    maybe_include_etherscan_key,
)
from rotkehlchen.tests.utils.ethereum import wait_until_all_nodes_connected
from rotkehlchen.tests.utils.factories import make_random_b64bytes
from rotkehlchen.tests.utils.history import maybe_mock_historical_price_queries


@pytest.fixture(name='start_with_logged_in_user')
def fixture_start_with_logged_in_user():
    return True


@pytest.fixture(scope='session', name='session_start_with_logged_in_user')
def fixture_session_start_with_logged_in_user():
    return True


@pytest.fixture(name='start_with_valid_premium')
def fixture_start_with_valid_premium():
    return False


@pytest.fixture(name='rotki_premium_credentials')
def fixture_rotki_premium_credentials() -> PremiumCredentials:
    return PremiumCredentials(
        given_api_key=base64.b64encode(make_random_b64bytes(128)).decode(),
        given_api_secret=base64.b64encode(make_random_b64bytes(128)).decode(),
    )


@pytest.fixture(name='cli_args')
def fixture_cli_args(data_dir, ethrpc_endpoint):
    args = namedtuple('args', [
        'sleep_secs',
        'data_dir',
        'zerorpc_port',
        'ethrpc_endpoint',
        'logfile',
        'logtarget',
        'loglevel',
        'logfromothermodules',
    ])
    args.loglevel = 'debug'
    args.logfromothermodules = False
    args.sleep_secs = 60
    args.data_dir = data_dir
    args.ethrpc_endpoint = ethrpc_endpoint
    return args


def initialize_mock_rotkehlchen_instance(
        rotki,
        start_with_logged_in_user,
        start_with_valid_premium,
        db_password,
        rotki_premium_credentials,
        username,
        blockchain_accounts,
        include_etherscan_key,
        include_cryptocompare_key,
        should_mock_price_queries,
        mocked_price_queries,
        ethereum_modules,
        db_settings,
        ignored_assets,
        tags,
        manually_tracked_balances,
        default_mock_price_value,
        ethereum_manager_connect_at_start,
        eth_rpc_endpoint,
        aave_use_graph,
):
    if not start_with_logged_in_user:
        return

    # Mock the initial get settings to include the specified ethereum modules
    def mock_get_settings() -> DBSettings:
        settings = DBSettings(active_modules=ethereum_modules, eth_rpc_endpoint=eth_rpc_endpoint)
        return settings
    settings_patch = patch.object(rotki, 'get_settings', side_effect=mock_get_settings)

    # Do not connect to the usual nodes at start by default. Do not want to spam
    # them during our tests. It's configurable per test, with the default being nothing
    rpcconnect_patch = patch(
        'rotkehlchen.rotkehlchen.ETHEREUM_NODES_TO_CONNECT_AT_START',
        new=ethereum_manager_connect_at_start,
    )

    with settings_patch, rpcconnect_patch:
        rotki.unlock_user(
            user=username,
            password=db_password,
            create_new=True,
            sync_approval='no',
            premium_credentials=None,
        )

    if start_with_valid_premium:
        rotki.premium = Premium(rotki_premium_credentials)
        rotki.premium_sync_manager.premium = rotki.premium

    # After unlocking when all objects are created we need to also include
    # customized fixtures that may have been set by the tests
    rotki.chain_manager.accounts = blockchain_accounts
    add_settings_to_test_db(rotki.data.db, db_settings, ignored_assets)
    maybe_include_etherscan_key(rotki.data.db, include_etherscan_key)
    maybe_include_cryptocompare_key(rotki.data.db, include_cryptocompare_key)
    add_blockchain_accounts_to_db(rotki.data.db, blockchain_accounts)
    add_tags_to_test_db(rotki.data.db, tags)
    add_manually_tracked_balances_to_test_db(rotki.data.db, manually_tracked_balances)
    maybe_mock_historical_price_queries(
        historian=PriceHistorian(),
        should_mock_price_queries=should_mock_price_queries,
        mocked_price_queries=mocked_price_queries,
        default_mock_value=default_mock_price_value,
    )
    wait_until_all_nodes_connected(
        ethereum_manager_connect_at_start=ethereum_manager_connect_at_start,
        ethereum=rotki.chain_manager.ethereum,
    )

    aave = rotki.chain_manager.aave
    if aave:
        aave.use_graph = aave_use_graph


@pytest.fixture(name='uninitialized_rotkehlchen')
def fixture_uninitialized_rotkehlchen(cli_args, inquirer, asset_resolver):  # pylint: disable=unused-argument  # noqa: E501
    """A rotkehlchen instance that has only had __init__ run but is not unlocked

    Adding the inquirer fixture as a requirement to make sure that any mocking that
    happens at the inquirer level is reflected in the tests.

    For this to happen inquirer fixture must be initialized before Rotkehlchen so
    that the inquirer initialization in Rotkehlchen's __init__ uses the fixture's instance

    Adding the AssetResolver as a requirement so that the first initialization happens here
    """
    # patch the constants to make sure that the periodic query for icons
    # does not run during tests
    size_patch = patch('rotkehlchen.rotkehlchen.ICONS_BATCH_SIZE', new=0)
    sleep_patch = patch('rotkehlchen.rotkehlchen.ICONS_QUERY_SLEEP', new=999999)
    with size_patch, sleep_patch:
        rotki = Rotkehlchen(cli_args)
    return rotki


@pytest.fixture(name='rotkehlchen_api_server')
def fixture_rotkehlchen_api_server(
        uninitialized_rotkehlchen,
        api_port,
        start_with_logged_in_user,
        start_with_valid_premium,
        db_password,
        rotki_premium_credentials,
        username,
        blockchain_accounts,
        include_etherscan_key,
        include_cryptocompare_key,
        should_mock_price_queries,
        mocked_price_queries,
        ethereum_modules,
        db_settings,
        ignored_assets,
        tags,
        manually_tracked_balances,
        default_mock_price_value,
        ethereum_manager_connect_at_start,
        ethrpc_endpoint,
        aave_use_graph,
):
    """A partially mocked rotkehlchen server instance"""

    api_server = create_api_server(rotki=uninitialized_rotkehlchen, port_number=api_port)

    initialize_mock_rotkehlchen_instance(
        rotki=api_server.rest_api.rotkehlchen,
        start_with_logged_in_user=start_with_logged_in_user,
        start_with_valid_premium=start_with_valid_premium,
        db_password=db_password,
        rotki_premium_credentials=rotki_premium_credentials,
        username=username,
        blockchain_accounts=blockchain_accounts,
        include_etherscan_key=include_etherscan_key,
        include_cryptocompare_key=include_cryptocompare_key,
        should_mock_price_queries=should_mock_price_queries,
        mocked_price_queries=mocked_price_queries,
        ethereum_modules=ethereum_modules,
        db_settings=db_settings,
        ignored_assets=ignored_assets,
        tags=tags,
        manually_tracked_balances=manually_tracked_balances,
        default_mock_price_value=default_mock_price_value,
        ethereum_manager_connect_at_start=ethereum_manager_connect_at_start,
        eth_rpc_endpoint=ethrpc_endpoint,
        aave_use_graph=aave_use_graph,
    )
    yield api_server
    api_server.stop()


@pytest.fixture()
def rotkehlchen_instance(
        uninitialized_rotkehlchen,
        start_with_logged_in_user,
        start_with_valid_premium,
        db_password,
        rotki_premium_credentials,
        username,
        blockchain_accounts,
        include_etherscan_key,
        include_cryptocompare_key,
        should_mock_price_queries,
        mocked_price_queries,
        ethereum_modules,
        db_settings,
        ignored_assets,
        tags,
        manually_tracked_balances,
        default_mock_price_value,
        ethereum_manager_connect_at_start,
        ethrpc_endpoint,
        aave_use_graph,
):
    """A partially mocked rotkehlchen instance"""

    initialize_mock_rotkehlchen_instance(
        rotki=uninitialized_rotkehlchen,
        start_with_logged_in_user=start_with_logged_in_user,
        start_with_valid_premium=start_with_valid_premium,
        db_password=db_password,
        rotki_premium_credentials=rotki_premium_credentials,
        username=username,
        blockchain_accounts=blockchain_accounts,
        include_etherscan_key=include_etherscan_key,
        include_cryptocompare_key=include_cryptocompare_key,
        should_mock_price_queries=should_mock_price_queries,
        mocked_price_queries=mocked_price_queries,
        ethereum_modules=ethereum_modules,
        db_settings=db_settings,
        ignored_assets=ignored_assets,
        tags=tags,
        manually_tracked_balances=manually_tracked_balances,
        default_mock_price_value=default_mock_price_value,
        ethereum_manager_connect_at_start=ethereum_manager_connect_at_start,
        eth_rpc_endpoint=ethrpc_endpoint,
        aave_use_graph=aave_use_graph,
    )
    return uninitialized_rotkehlchen


@pytest.fixture()
def rotkehlchen_api_server_with_exchanges(
        rotkehlchen_api_server,
        added_exchanges,
):
    """Adds mock exchange objects to the rotkehlchen_server fixture"""
    exchanges = rotkehlchen_api_server.rest_api.rotkehlchen.exchange_manager.connected_exchanges
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    for exchange_name in added_exchanges:

        if exchange_name in ('coinbasepro', 'coinbase', 'gemini'):
            # TODO: Add support for the above exchanges in tests too
            continue

        create_fn = getattr(exchange_tests, f'create_test_{exchange_name}')
        exchanges[exchange_name] = create_fn(
            database=rotki.data.db,
            msg_aggregator=rotki.msg_aggregator,
        )

    yield rotkehlchen_api_server
    rotkehlchen_api_server.stop()
