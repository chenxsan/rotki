from contextlib import ExitStack
from http import HTTPStatus
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
import requests
from polyleven import levenshtein

from rotkehlchen.accounting.structures.balance import BalanceType
from rotkehlchen.assets.asset import Asset, CustomAsset
from rotkehlchen.assets.types import AssetType
from rotkehlchen.balances.manual import ManuallyTrackedBalance
from rotkehlchen.constants.assets import A_BTC, A_DAI, A_EUR, A_SAI, A_USD
from rotkehlchen.db.custom_assets import DBCustomAssets
from rotkehlchen.db.settings import ModifiableDBSettings
from rotkehlchen.fval import FVal
from rotkehlchen.globaldb.handler import GlobalDBHandler
from rotkehlchen.tests.utils.api import (
    api_url_for,
    assert_error_response,
    assert_proper_response,
    assert_proper_response_with_result,
)
from rotkehlchen.tests.utils.checks import assert_asset_result_order
from rotkehlchen.tests.utils.constants import A_GNO, A_RDN
from rotkehlchen.tests.utils.factories import UNIT_BTC_ADDRESS1, UNIT_BTC_ADDRESS2
from rotkehlchen.tests.utils.mock import MockResponse
from rotkehlchen.tests.utils.rotkehlchen import setup_balances
from rotkehlchen.types import ChainID, Location

KICK_TOKEN = Asset('eip155:1/erc20:0x824a50dF33AC1B41Afc52f4194E2e8356C17C3aC')


def mock_cryptoscamdb_request():
    def mock_requests_get(url, *args, **kwargs):  # pylint: disable=unused-argument
        response = 'Error generating response'
        return MockResponse(200, response)

    return patch('requests.get', side_effect=mock_requests_get)


def assert_substring_in_search_result(
        data: list[dict[str, Any]],
        substring: str,
) -> None:
    """Asserts that a given substring is present in the search result."""
    for entry in data:
        substr_in_name = substr_in_symbol = None
        if entry['name'] is not None:
            substr_in_name = substring.casefold() in entry['name'].casefold()
        if entry['symbol'] is not None:
            substr_in_symbol = substring.casefold() in entry['symbol'].casefold()
        assert substr_in_name or substr_in_symbol, f'no match for {substring}'


def assert_asset_at_top_position(
        asset_id: str,
        max_position_index: int,
        result: list[dict[str, Any]],
) -> None:
    """Aserts that an asset appears at the top of the search results."""
    assert any([asset_id == entry['identifier'] for entry in result])
    for index, entry in enumerate(result):
        if entry['identifier'] == asset_id:
            assert index <= max_position_index


@pytest.mark.parametrize('number_of_eth_accounts', [2])
@pytest.mark.parametrize('btc_accounts', [[UNIT_BTC_ADDRESS1, UNIT_BTC_ADDRESS2]])
@pytest.mark.parametrize('added_exchanges', [(Location.BINANCE, Location.POLONIEX)])
def test_query_owned_assets(
        rotkehlchen_api_server_with_exchanges,
        ethereum_accounts,
        btc_accounts,
):
    """Test that using the query all owned assets endpoint works"""
    # Disable caching of query results
    rotki = rotkehlchen_api_server_with_exchanges.rest_api.rotkehlchen
    rotki.chains_aggregator.cache_ttl_secs = 0
    setup = setup_balances(
        rotki=rotki,
        ethereum_accounts=ethereum_accounts,
        btc_accounts=btc_accounts,
        manually_tracked_balances=[ManuallyTrackedBalance(
            id=-1,
            asset=A_EUR,
            label='My EUR bank',
            amount=FVal('1550'),
            location=Location.BANKS,
            tags=None,
            balance_type=BalanceType.ASSET,
        )],
    )

    # Get all our mocked balances and save them in the DB
    with ExitStack() as stack:
        setup.enter_all_patches(stack)
        response = requests.get(
            api_url_for(
                rotkehlchen_api_server_with_exchanges,
                'allbalancesresource',
            ), json={'save_data': True},
        )
    assert_proper_response(response)

    # And now check that the query owned assets endpoint works
    with ExitStack() as stack:
        setup.enter_all_patches(stack)
        response = requests.get(
            api_url_for(
                rotkehlchen_api_server_with_exchanges,
                'ownedassetsresource',
            ),
        )
    result = assert_proper_response_with_result(response)
    assert set(result) == {'ETH', 'BTC', 'EUR', A_RDN.identifier}


@pytest.mark.parametrize('perform_migrations_at_unlock', [True])
@pytest.mark.parametrize('data_migration_version', [0])
def test_ignored_assets_modification(rotkehlchen_api_server_with_exchanges):
    """Test that using the ignored assets endpoint to modify the ignored assets list works fine"""
    rotki = rotkehlchen_api_server_with_exchanges.rest_api.rotkehlchen

    # add three assets to ignored assets
    kick_token_id = KICK_TOKEN.identifier
    ignored_assets = [A_GNO.identifier, A_RDN.identifier, 'XMR']
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server_with_exchanges,
            'ignoredassetsresource',
        ), json={'assets': ignored_assets},
    )
    result = assert_proper_response_with_result(response)
    expected_ignored_assets = set(ignored_assets + [KICK_TOKEN.identifier])
    assert expected_ignored_assets <= set(result)

    with rotki.data.db.conn.read_ctx() as cursor:
        # check they are there
        assert set(rotki.data.db.get_ignored_assets(cursor)) >= expected_ignored_assets
        # Query for ignored assets and check that the response returns them
        response = requests.get(
            api_url_for(
                rotkehlchen_api_server_with_exchanges,
                'ignoredassetsresource',
            ),
        )
        result = assert_proper_response_with_result(response)
        assert expected_ignored_assets <= set(result)

        # remove 3 assets from ignored assets
        response = requests.delete(
            api_url_for(
                rotkehlchen_api_server_with_exchanges,
                'ignoredassetsresource',
            ), json={'assets': [A_GNO.identifier, 'XMR', kick_token_id]},
        )
        assets_after_deletion = {A_RDN.identifier}
        result = assert_proper_response_with_result(response)
        assert assets_after_deletion <= set(result)

        # check that the changes are reflected
        assert set(rotki.data.db.get_ignored_assets(cursor)) >= assets_after_deletion
        # Query for ignored assets and check that the response returns them
        response = requests.get(
            api_url_for(
                rotkehlchen_api_server_with_exchanges,
                'ignoredassetsresource',
            ),
        )
        result = assert_proper_response_with_result(response)
        assert assets_after_deletion <= set(result)

        # Fetch remote assets to be ignored
        response = requests.post(
            api_url_for(
                rotkehlchen_api_server_with_exchanges,
                'ignoredassetsresource',
            ),
        )
        result = assert_proper_response_with_result(response)
        assert result >= 1
        assert len(rotki.data.db.get_ignored_assets(cursor)) > len(assets_after_deletion)

    # Simulate remote error from cryptoscamdb
    with mock_cryptoscamdb_request():
        response = requests.post(
            api_url_for(
                rotkehlchen_api_server_with_exchanges,
                'ignoredassetsresource',
            ),
        )
        assert response.status_code == HTTPStatus.BAD_GATEWAY


@pytest.mark.parametrize('perform_migrations_at_unlock', [True])
@pytest.mark.parametrize('method', ['put', 'delete'])
@pytest.mark.parametrize('data_migration_version', [0])
def test_ignored_assets_endpoint_errors(rotkehlchen_api_server_with_exchanges, method):
    """Test errors are handled properly at the ignored assets endpoint"""
    rotki = rotkehlchen_api_server_with_exchanges.rest_api.rotkehlchen

    # add three assets to ignored assets
    ignored_assets = [A_GNO.identifier, A_RDN.identifier, 'XMR']
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server_with_exchanges,
            'ignoredassetsresource',
        ), json={'assets': ignored_assets},
    )
    assert_proper_response(response)

    # Test that omitting the assets argument is an error
    response = getattr(requests, method)(
        api_url_for(
            rotkehlchen_api_server_with_exchanges,
            'ignoredassetsresource',
        ),
    )
    assert_error_response(
        response=response,
        contained_in_msg='"assets": ["Missing data for required field',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Test that invalid type for assets list is an error
    response = getattr(requests, method)(
        api_url_for(
            rotkehlchen_api_server_with_exchanges,
            'ignoredassetsresource',
        ), json={'assets': 'foo'},
    )
    assert_error_response(
        response=response,
        contained_in_msg='"assets": ["Not a valid list."',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Test that list with invalid asset is an error
    response = getattr(requests, method)(
        api_url_for(
            rotkehlchen_api_server_with_exchanges,
            'ignoredassetsresource',
        ), json={'assets': ['notanasset']},
    )
    assert_error_response(
        response=response,
        contained_in_msg='Unknown asset notanasset provided',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Test that list with one valid and one invalid is rejected and not even the
    # valid one is processed
    if method == 'put':
        asset = 'ETH'
    else:
        asset = 'XMR'
    response = getattr(requests, method)(
        api_url_for(
            rotkehlchen_api_server_with_exchanges,
            'ignoredassetsresource',
        ), json={'assets': [asset, 'notanasset']},
    )
    assert_error_response(
        response=response,
        contained_in_msg='Unknown asset notanasset provided',
        status_code=HTTPStatus.BAD_REQUEST,
    )
    # Check that assets did not get modified
    with rotki.data.db.conn.read_ctx() as cursor:
        expected_tokens = set(
            ignored_assets +
            [KICK_TOKEN],
        )
        assert set(rotki.data.db.get_ignored_assets(cursor)) >= expected_tokens

        # Test the adding an already existing asset or removing a non-existing asset is an error
        if method == 'put':
            asset = A_RDN.identifier
            expected_msg = f'{A_RDN.identifier} is already in ignored assets'
        else:
            asset = 'ETH'
            expected_msg = 'ETH is not in ignored assets'
        response = getattr(requests, method)(
            api_url_for(
                rotkehlchen_api_server_with_exchanges,
                'ignoredassetsresource',
            ), json={'assets': [asset]},
        )
        assert_error_response(
            response=response,
            contained_in_msg=expected_msg,
            status_code=HTTPStatus.CONFLICT,
        )
        # Check that assets did not get modified
        assert set(rotki.data.db.get_ignored_assets(cursor)) >= expected_tokens


def test_get_all_assets(rotkehlchen_api_server):
    """Test that fetching all assets returns a paginated result."""
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'limit': 20,
            'offset': 0,
            'asset_type': 'fiat',
            'order_by_attributes': ['name'],
            'ascending': [True],
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result['entries']) <= 20
    assert 'entries_found' in result
    assert 'entries_total' in result
    assert 'entries_limit' in result
    for entry in result['entries']:
        assert entry['type'] == 'fiat'
    assert_asset_result_order(data=result['entries'], is_ascending=True, order_field='name')

    # use a different filter
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'limit': 50,
            'offset': 0,
            'name': 'Uniswap',
            'order_by_attributes': ['symbol'],
            'ascending': [False],
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result['entries']) <= 50
    assert 'entries_found' in result
    assert 'entries_total' in result
    assert 'entries_limit' in result
    for entry in result['entries']:
        assert 'Uniswap' in entry['name']
        if entry['type'] == AssetType.EVM_TOKEN.serialize():
            assert entry['evm_chain'] in [x.to_name() for x in ChainID]
    assert_asset_result_order(data=result['entries'], is_ascending=False, order_field='symbol')

    # test that ignored assets filter works
    with rotkehlchen_api_server.rest_api.rotkehlchen.data.db.user_write() as write_cursor:
        rotkehlchen_api_server.rest_api.rotkehlchen.data.db.add_to_ignored_assets(
            write_cursor=write_cursor,
            asset=A_USD,
        )
        rotkehlchen_api_server.rest_api.rotkehlchen.data.db.add_to_ignored_assets(
            write_cursor=write_cursor,
            asset=A_EUR,
        )
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'limit': 20,
            'offset': 0,
            'asset_type': 'fiat',
            'order_by_attributes': ['name'],
            'ascending': [True],
            'ignored_assets_handling': 'exclude',
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result['entries']) <= 20
    assert 'entries_found' in result
    assert 'entries_total' in result
    assert 'entries_limit' in result
    for entry in result['entries']:
        assert entry['type'] == 'fiat'
        assert entry['symbol'] != A_USD.resolve_to_asset_with_symbol().symbol and entry['symbol'] != A_EUR.resolve_to_asset_with_symbol().symbol  # noqa: E501
    assert_asset_result_order(data=result['entries'], is_ascending=True, order_field='name')

    # test that user owned assets filter works
    GlobalDBHandler().add_user_owned_assets([A_BTC, A_DAI, A_SAI])
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'limit': 2,
            'offset': 0,
            'order_by_attributes': ['name'],
            'ascending': [True],
            'show_user_owned_assets_only': True,
        },
    )
    result = assert_proper_response_with_result(response)
    assets_names = {r['name'] for r in result['entries']}
    assets_chain = {r.get('evm_chain', None) for r in result['entries']}
    assert result['entries_found'] == 3
    assert assets_chain.issubset({*[x.to_name() for x in ChainID], None})
    assert A_BTC.resolve_to_asset_with_name_and_type().name in assets_names
    assert A_DAI.resolve_to_asset_with_name_and_type().name in assets_names
    assert A_SAI.resolve_to_asset_with_name_and_type().name not in assets_names
    assert_asset_result_order(data=result['entries'], is_ascending=True, order_field='name')

    # add custom asset and filter results using `custom asset` type.
    db_custom_assets = DBCustomAssets(
        db_handler=rotkehlchen_api_server.rest_api.rotkehlchen.data.db,
    )
    custom_asset_id = str(uuid4())
    db_custom_assets.add_custom_asset(CustomAsset.initialize(
        identifier=custom_asset_id,
        name='My Custom Prop',
        custom_asset_type='random',
    ))
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'limit': 10,
            'offset': 0,
            'order_by_attributes': ['name'],
            'asset_type': 'custom asset',
            'ascending': [True],
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result['entries']) == 1
    assert result['entries'][0]['identifier'] == custom_asset_id
    assert result['entries'][0]['type'] == 'custom asset'

    # filter by name & symbol
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'limit': 50,
            'offset': 0,
            'name': 'Uniswap',
            'symbol': 'UNI',
            'order_by_attributes': ['symbol'],
            'ascending': [False],
        },
    )
    result = assert_proper_response_with_result(response)
    assert 50 >= len(result['entries']) > 2
    for entry in result['entries']:
        assert 'Uniswap' in entry['name']
        assert 'UNI' in entry['symbol']
        if entry['type'] == AssetType.EVM_TOKEN.serialize():
            assert entry['evm_chain'] in [x.to_name() for x in ChainID]

    # check that providing multiple order_by_attributes fails
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'limit': 20,
            'offset': 0,
            'asset_type': 'fiat',
            'order_by_attributes': ['name', 'symbol'],
            'ascending': [True, False],
        },
    )
    assert_error_response(response, contained_in_msg='Multiple fields ordering is not allowed.')

    # test asking for a single evm token
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'identifiers': [A_DAI.identifier],
        },
    )
    result = assert_proper_response_with_result(response)
    assert result['entries'][0]['identifier'] == A_DAI
    assert 'address' in result['entries'][0]
    assert 'decimals' in result['entries'][0]
    assert result['entries'][0]['evm_chain'] == 'ethereum'
    assert result['entries'][0]['type'] == AssetType.EVM_TOKEN.serialize()

    # ask for a crypto asset and a fiat asset (test multiple asset query)
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'identifiers': [A_BTC.identifier, A_USD.identifier, custom_asset_id],
        },
    )
    result = assert_proper_response_with_result(response)
    assert result['entries'][0]['identifier'] == A_USD
    assert result['entries'][1]['identifier'] == custom_asset_id
    assert result['entries'][2]['identifier'] == A_BTC

    # ask for a non existent asset
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'identifiers': ['my_life'],
        },
    )
    assert_error_response(
        response=response,
        contained_in_msg='Queried identifiers my_life are not present in the database',
        status_code=HTTPStatus.CONFLICT,
    )

    # check that evm tokens with underlying tokens are shown
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={'asset_type': 'evm token'},
    )
    result = assert_proper_response_with_result(response)
    for entry in result['entries']:
        assert 'underlying_tokens' in entry
        assert entry['evm_chain'] in [x.to_name() for x in ChainID]


def test_get_assets_mappings(rotkehlchen_api_server):
    """Test that providing a list of asset identifiers, the appropriate assets mappings are returned."""  # noqa: E501
    queried_assets = ('BTC', 'TRY', 'EUR', A_DAI.identifier)
    # add custom asset
    db_custom_assets = DBCustomAssets(
        db_handler=rotkehlchen_api_server.rest_api.rotkehlchen.data.db,
    )
    custom_asset_id = str(uuid4())
    db_custom_assets.add_custom_asset(CustomAsset.initialize(
        identifier=custom_asset_id,
        name='My Custom Prop',
        custom_asset_type='random',
    ))
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetsmappingresource',
        ),
        json={'identifiers': queried_assets},
    )
    result = assert_proper_response_with_result(response)
    assets = result['assets']
    assert len(assets) == len(queried_assets)
    for identifier, details in assets.items():
        assert identifier in queried_assets
        if identifier == A_DAI.identifier:
            assert details['evm_chain'] == 'ethereum'
            assert 'custom_asset_type' not in details.keys()
            assert details['asset_type'] != 'custom asset'
            assert details['collection_id'] == '23'
        elif identifier == custom_asset_id:
            assert details['custom_asset_type'] == 'random'
            assert details['asset_type'] == 'custom asset'
        else:
            assert 'evm_chain' not in details.keys()
            assert 'custom_asset_type' not in details.keys()
            assert details['asset_type'] != 'custom asset'

    assert result['asset_collections'] == {
        '23': {
            'name': 'Multi Collateral Dai',
            'symbol': 'DAI',
        },
    }

    # check that providing an invalid identifier returns only valid ones if any.
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetsmappingresource',
        ),
        json={'identifiers': ['BTC', 'TRY', 'invalid']},
    )
    result = assert_proper_response_with_result(response)
    assets = result['assets']
    assert len(assets) == 2
    assert all([identifier in ('BTC', 'TRY') for identifier in assets.keys()])


def test_search_assets(rotkehlchen_api_server):
    """Test that searching for assets using a keyword works."""
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchresource',
        ),
        json={
            'value': 'Bitcoin',
            'search_column': 'name',
            'limit': 50,
            'order_by_attributes': ['name'],
            'ascending': [True],
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result) <= 50
    for entry in result:
        assert 'bitcoin' in entry['name'].lower()
    assert_asset_result_order(data=result, is_ascending=True, order_field='name')
    assert all(['custom_asset_type' not in entry.keys() and not entry['is_custom_asset'] for entry in result])  # noqa: E501

    # use a different keyword
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchresource',
        ),
        json={
            'value': 'eth',
            'search_column': 'symbol',
            'limit': 10,
            'order_by_attributes': ['symbol'],
            'ascending': [False],
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result) <= 10
    for entry in result:
        assert 'eth' in entry['symbol'].lower()
    assert_asset_result_order(data=result, is_ascending=False, order_field='symbol')
    assert all(['custom_asset_type' not in entry.keys() and not entry['is_custom_asset'] for entry in result])  # noqa: E501

    # check that searching for a non-existent asset returns nothing
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchresource',
        ),
        json={
            'value': 'idontexist',
            'search_column': 'name',
            'limit': 50,
            'order_by_attributes': ['name'],
            'ascending': [True],
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result) == 0

    # use the return_exact_matches flag
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchresource',
        ),
        json={
            'value': 'ETH',
            'search_column': 'symbol',
            'limit': 10,
            'return_exact_matches': True,
            'order_by_attributes': ['name'],
            'ascending': [False],
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result) == 3
    assert any([entry['name'] == 'Ethereum' for entry in result])
    for entry in result:
        assert entry['symbol'] == 'ETH'
    assert_asset_result_order(data=result, is_ascending=False, order_field='name')
    assert all(['custom_asset_type' not in entry.keys() and not entry['is_custom_asset'] for entry in result])  # noqa: E501

    # check that treat_eth2_as_eth` setting is respected
    # using the test above.
    db = rotkehlchen_api_server.rest_api.rotkehlchen.data.db
    with db.user_write() as cursor:
        db.set_settings(cursor, ModifiableDBSettings(treat_eth2_as_eth=True))

    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchresource',
        ),
        json={
            'value': 'ETH',
            'search_column': 'symbol',
            'limit': 10,
            'return_exact_matches': True,
            'order_by_attributes': ['name'],
            'ascending': [True],
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result) == 2
    assert any([entry['name'] == 'Ethereum' for entry in result])
    for entry in result:
        assert entry['symbol'] == 'ETH'
        assert entry['identifier'] != 'ETH2'
        if entry['name'] != 'Binance-Peg Ethereum Token':
            assert 'evm_chain' not in entry
        else:
            assert entry['evm_chain'] == 'binance'
    assert_asset_result_order(data=result, is_ascending=True, order_field='name')
    assert all(['custom_asset_type' not in entry.keys() and not entry['is_custom_asset'] for entry in result])  # noqa: E501

    # search using a column that is not allowed
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchresource',
        ),
        json={
            'value': 'idontexist',
            'search_column': 'identifier',
            'limit': 50,
            'order_by_attributes': ['name'],
            'ascending': [True],
        },
    )
    assert_error_response(response, contained_in_msg='Must be one of: name, symbol.')

    # test that the evm_chain column is included
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchresource',
        ),
        json={
            'value': 'DAI',
            'search_column': 'symbol',
            'limit': 10,
            'return_exact_matches': True,
            'order_by_attributes': ['name'],
            'ascending': [True],
        },
    )
    result = assert_proper_response_with_result(response)
    assert {asset['evm_chain'] for asset in result} == {
        'matic',
        'optimism',
        'ethereum',
        'arbitrum',
        'binance',
    }

    # check that using evm_chain filter works.
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchresource',
        ),
        json={
            'value': 'DAI',
            'search_column': 'symbol',
            'limit': 50,
            'evm_chain': 'ethereum',
            'order_by_attributes': ['name'],
            'ascending': [True],
        },
    )
    result = assert_proper_response_with_result(response)
    assert 50 >= len(result) > 10
    assert all([entry['evm_chain'] == 'ethereum' and 'DAI' in entry['symbol'] for entry in result])
    assert_asset_result_order(data=result, is_ascending=True, order_field='name')

    # check that using an unsupported evm_chain fails
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchresource',
        ),
        json={
            'value': 'dai',
            'search_column': 'symbol',
            'limit': 50,
            'evm_chain': 'prettychain',
            'order_by_attributes': ['name'],
            'ascending': [True],
        },
    )
    assert_error_response(response, contained_in_msg='Failed to deserialize evm chain value prettychain')  # noqa: E501


def test_search_assets_with_levenshtein(rotkehlchen_api_server):
    """Test that searching for assets using a keyword works(levenshtein approach)."""
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchlevenshteinresource',
        ),
        json={
            'value': 'Bitcoin',
            'limit': 50,
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result) <= 50
    # check that Bitcoin(BTC) appears at the top of result.
    assert_asset_at_top_position('BTC', max_position_index=1, result=result)
    assert_substring_in_search_result(result, 'Bitcoin')
    assert all(['custom_asset_type' not in entry.keys() and entry['asset_type'] != 'custom asset' for entry in result])  # noqa: E501

    # use a different keyword
    # but add assets without name/symbol and see that nothing breaks
    asset_without_name_id = str(uuid4())
    asset_without_symbol_id = str(uuid4())
    GlobalDBHandler().add_asset(
        asset_id=asset_without_name_id,
        asset_type=AssetType.OWN_CHAIN,
        data={'symbol': 'ETH'},
    )
    GlobalDBHandler().add_asset(
        asset_id=asset_without_symbol_id,
        asset_type=AssetType.OWN_CHAIN,
        data={'name': 'ETH'},
    )
    GlobalDBHandler().add_asset(
        asset_id=str(uuid4()),
        asset_type=AssetType.OWN_CHAIN,
        data={},
    )
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchlevenshteinresource',
        ),
        json={
            'value': 'ETH',
            'limit': 50,
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result) <= 50
    assert_substring_in_search_result(result, 'ETH')
    # check that Ethereum(ETH) appear at the top of result.
    assert_asset_at_top_position('ETH', max_position_index=1, result=result)
    assert any([asset_without_name_id == entry['identifier'] for entry in result])
    assert any([asset_without_symbol_id == entry['identifier'] for entry in result])
    assert all(['custom_asset_type' not in entry.keys() and entry['asset_type'] != 'custom asset' for entry in result])  # noqa: E501

    # check that treat_eth2_as_eth` setting is respected
    # using the test above.
    db = rotkehlchen_api_server.rest_api.rotkehlchen.data.db
    with db.user_write() as cursor:
        db.set_settings(cursor, ModifiableDBSettings(treat_eth2_as_eth=True))

    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchlevenshteinresource',
        ),
        json={
            'value': 'ETH',
            'limit': 50,
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result) <= 50
    assert_substring_in_search_result(result, 'ETH')
    # check that Ethereum(ETH) appears at the top of result.
    assert_asset_at_top_position('ETH', max_position_index=1, result=result)
    assert all([entry['identifier'] != 'ETH2' and entry['asset_type'] != 'custom asset' and 'custom_asset_type' not in entry.keys() for entry in result])  # noqa: E501

    # check that searching for a non-existent asset returns nothing
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchlevenshteinresource',
        ),
        json={
            'value': 'idontexist',
            'limit': 50,
        },
    )
    result = assert_proper_response_with_result(response)
    assert len(result) == 0

    # check that using evm_chain filter works.
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchlevenshteinresource',
        ),
        json={
            'value': 'dai',
            'limit': 50,
            'evm_chain': 'ethereum',
        },
    )
    result = assert_proper_response_with_result(response)
    assert 50 >= len(result) > 10
    assert all([entry['evm_chain'] == 'ethereum' and entry['asset_type'] != 'custom asset' and 'custom_asset_type' not in entry.keys() for entry in result])  # noqa: E501

    assert_substring_in_search_result(result, 'dai')
    # check that Dai(DAI) appears at the top of result.
    assert_asset_at_top_position(
        asset_id=A_DAI.identifier,
        max_position_index=0,
        result=result,
    )

    # check that searching for assets with long name works
    db_custom_assets = DBCustomAssets(
        db_handler=rotkehlchen_api_server.rest_api.rotkehlchen.data.db,
    )
    custom_asset_id = str(uuid4())
    db_custom_assets.add_custom_asset(CustomAsset.initialize(
        identifier=custom_asset_id,
        name='My Custom Prop that has a very long name haha',
        custom_asset_type='random',
    ))
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchlevenshteinresource',
        ),
        json={
            'value': 'my custom',
            'limit': 50,
        },
    )
    result = assert_proper_response_with_result(response)
    assert_substring_in_search_result(result, 'my custom')
    assert all([custom_asset_id == entry['identifier'] and entry['asset_type'] == 'custom asset' and entry['custom_asset_type'] == 'random' for entry in result])  # noqa: E501

    # check that using an unsupported evm_chain fails
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchlevenshteinresource',
        ),
        json={
            'value': 'dai',
            'limit': 50,
            'evm_chain': 'charlesfarm',
        },
    )
    assert_error_response(response, contained_in_msg='Failed to deserialize evm chain value charlesfarm')  # noqa: E501


def test_search_nfts_with_levenshtein(rotkehlchen_api_server):
    with rotkehlchen_api_server.rest_api.rotkehlchen.data.db.user_write() as cursor:
        cursor.execute('INSERT INTO assets VALUES (?)', ('my-nft-identifier',))
        cursor.execute(
            'INSERT INTO nfts(identifier, name, collection_name, manual_price, is_lp) '
            'VALUES (?, ?, ?, ?, ?)',
            ('my-nft-identifier', 'super-duper-nft', 'Bitcoin smth', False, False),
        )

    # check that searching by nft name works
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchlevenshteinresource',
        ),
        json={
            'value': 'super-duper',
            'limit': 50,
            'search_nfts': True,
        },
    )
    result = assert_proper_response_with_result(response)
    assert result == [{
        'identifier': 'my-nft-identifier',
        'name': 'super-duper-nft',
        'collection_name': 'Bitcoin smth',
        'asset_type': 'nft',
    }]

    # Check that:
    # 1. Searching by nft collection name works
    # 2. Nfts are searched only with search_nfts set to True
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchlevenshteinresource',
        ),
        json={
            'value': 'Bitcoin',
            'limit': 50,
        },
    )
    results_without_nfts = [x['identifier'] for x in assert_proper_response_with_result(response)]

    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'assetssearchlevenshteinresource',
        ),
        json={
            'value': 'Bitcoin',
            'limit': 50,
            'search_nfts': True,
        },
    )
    result = assert_proper_response_with_result(response)
    results_with_nfts = [x['identifier'] for x in result]
    assert set(results_with_nfts) - set(results_without_nfts) == {'my-nft-identifier'}

    # Check that the order makes sense
    previous_levenshtein_distance = 0
    for entry in result:
        if entry['asset_type'] == 'nft':
            current_levenshtein_distance = min(
                levenshtein('bitcoin', entry['name']),
                levenshtein('bitcoin', entry['collection_name']),
            )
        else:
            current_levenshtein_distance = min(
                levenshtein('bitcoin', entry['name']),
                levenshtein('bitcoin', entry['symbol']),
            )
        assert current_levenshtein_distance >= previous_levenshtein_distance


def test_only_ignored_assets(rotkehlchen_api_server):
    """Test it's possible to ask to only see the ignored assets"""
    ignored_assets = [A_GNO.identifier, A_RDN.identifier]
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'ignoredassetsresource',
        ), json={'assets': ignored_assets},
    )
    assert_proper_response(response)
    response = requests.post(
        api_url_for(
            rotkehlchen_api_server,
            'allassetsresource',
        ),
        json={
            'ignored_assets_handling': 'show_only',
        },
    )
    result = assert_proper_response_with_result(response)
    assert {entry['identifier'] for entry in result['entries']} == set(ignored_assets)
