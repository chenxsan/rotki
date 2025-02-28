import logging
import random
from contextlib import ExitStack
from http import HTTPStatus
from unittest.mock import patch

import gevent
import pytest
import requests

from rotkehlchen.accounting.structures.balance import Balance
from rotkehlchen.chain.accounts import SingleBlockchainAccountData
from rotkehlchen.chain.ethereum.defi.structures import (
    DefiBalance,
    DefiProtocol,
    DefiProtocolBalances,
)
from rotkehlchen.constants.assets import A_DAI, A_USDT
from rotkehlchen.constants.misc import ONE
from rotkehlchen.fval import FVal
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.tests.utils.api import (
    ASYNC_TASK_WAIT_TIMEOUT,
    api_url_for,
    assert_error_response,
    assert_ok_async_response,
    assert_proper_response,
    assert_proper_response_with_result,
    wait_for_async_task_with_result,
)
from rotkehlchen.tests.utils.blockchain import (
    assert_btc_balances_result,
    assert_eth_balances_result,
    compare_account_data,
)
from rotkehlchen.tests.utils.constants import A_RDN
from rotkehlchen.tests.utils.factories import (
    UNIT_BTC_ADDRESS1,
    UNIT_BTC_ADDRESS2,
    UNIT_BTC_ADDRESS3,
    make_evm_address,
)
from rotkehlchen.tests.utils.rotkehlchen import setup_balances
from rotkehlchen.types import SupportedBlockchain

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


@pytest.mark.parametrize('number_of_eth_accounts', [0])
def test_query_empty_blockchain_balances(rotkehlchen_api_server):
    """Make sure that querying balances for all blockchains works when no accounts are tracked

    Regression test for https://github.com/rotki/rotki/issues/848
    """
    response = requests.get(api_url_for(
        rotkehlchen_api_server,
        'named_blockchain_balances_resource',
        blockchain='ETH',
    ))
    assert_proper_response(response)
    data = response.json()
    assert data['message'] == ''
    assert data['result'] == {'per_account': {}, 'totals': {'assets': {}, 'liabilities': {}}}

    response = requests.get(api_url_for(
        rotkehlchen_api_server,
        "named_blockchain_balances_resource",
        blockchain='BTC',
    ))
    assert_proper_response(response)
    data = response.json()
    assert data['message'] == ''
    assert data['result'] == {'per_account': {}, 'totals': {'assets': {}, 'liabilities': {}}}

    response = requests.get(api_url_for(
        rotkehlchen_api_server,
        "blockchainbalancesresource",
    ))
    assert_proper_response(response)
    data = response.json()
    assert data['message'] == ''
    assert data['result'] == {'per_account': {}, 'totals': {'assets': {}, 'liabilities': {}}}


@pytest.mark.parametrize('number_of_eth_accounts', [0])
@pytest.mark.parametrize('btc_accounts', [[
    UNIT_BTC_ADDRESS1,
    UNIT_BTC_ADDRESS2,
    'bc1qhkje0xfvhmgk6mvanxwy09n45df03tj3h3jtnf',
]])
def test_query_bitcoin_blockchain_bech32_balances(
        rotkehlchen_api_server,
        ethereum_accounts,
        btc_accounts,
        caplog,
):
    """Test that querying Bech32 bitcoin addresses works fine"""
    caplog.set_level(logging.DEBUG)
    # Disable caching of query results
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    rotki.chains_aggregator.cache_ttl_secs = 0

    btc_balances = ['111110', '3232223', '555555333']
    setup = setup_balances(
        rotki,
        ethereum_accounts=ethereum_accounts,
        btc_accounts=btc_accounts,
        btc_balances=btc_balances,
    )

    # query all balances
    with ExitStack() as stack:
        setup.enter_blockchain_patches(stack)
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            "blockchainbalancesresource",
        ))
    result = assert_proper_response_with_result(response)
    assert_btc_balances_result(
        result=result,
        btc_accounts=btc_accounts,
        btc_balances=setup.btc_balances,
        also_eth=False,
    )


@pytest.mark.parametrize('number_of_eth_accounts', [2])
@pytest.mark.parametrize('btc_accounts', [[UNIT_BTC_ADDRESS1, UNIT_BTC_ADDRESS2]])
@pytest.mark.parametrize('mocked_current_prices', [{
    'RDN': FVal('0.1135'),
    'ETH': FVal('212.92'),
    'BTC': FVal('8849.04'),
}])
def test_query_blockchain_balances(
        rotkehlchen_api_server,
        ethereum_accounts,
        btc_accounts,
):
    """Test that the query blockchain balances endpoint works when queried asynchronously
    """
    # Disable caching of query results
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    rotki.chains_aggregator.cache_ttl_secs = 0

    async_query = random.choice([False, True])
    setup = setup_balances(rotki, ethereum_accounts=ethereum_accounts, btc_accounts=btc_accounts)

    # First query only ETH and token balances
    with ExitStack() as stack:
        setup.enter_ethereum_patches(stack)
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            'named_blockchain_balances_resource',
            blockchain='ETH',
        ), json={'async_query': async_query})
        if async_query:
            task_id = assert_ok_async_response(response)
            outcome = wait_for_async_task_with_result(
                server=rotkehlchen_api_server,
                task_id=task_id,
                timeout=ASYNC_TASK_WAIT_TIMEOUT * 5,
            )
        else:
            outcome = assert_proper_response_with_result(response)

    assert_eth_balances_result(
        rotki=rotki,
        result=outcome,
        eth_accounts=ethereum_accounts,
        eth_balances=setup.eth_balances,
        token_balances=setup.token_balances,
        also_btc=False,
    )

    # Then query only BTC balances
    with setup.bitcoin_patch:
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            "named_blockchain_balances_resource",
            blockchain='BTC',
        ), json={'async_query': async_query})
        if async_query:
            task_id = assert_ok_async_response(response)
            outcome = wait_for_async_task_with_result(rotkehlchen_api_server, task_id)
        else:
            outcome = assert_proper_response_with_result(response)

    assert_btc_balances_result(
        result=outcome,
        btc_accounts=btc_accounts,
        btc_balances=setup.btc_balances,
        also_eth=False,
    )

    # Finally query all balances
    with ExitStack() as stack:
        setup.enter_blockchain_patches(stack)
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            'blockchainbalancesresource',
        ), json={'async_query': async_query})
        if async_query:
            task_id = assert_ok_async_response(response)
            outcome = wait_for_async_task_with_result(
                server=rotkehlchen_api_server,
                task_id=task_id,
                timeout=ASYNC_TASK_WAIT_TIMEOUT * 5,
            )
        else:
            outcome = assert_proper_response_with_result(response)

    assert_eth_balances_result(
        rotki=rotki,
        result=outcome,
        eth_accounts=ethereum_accounts,
        eth_balances=setup.eth_balances,
        token_balances=setup.token_balances,
        also_btc=True,
    )
    assert_btc_balances_result(
        result=outcome,
        btc_accounts=btc_accounts,
        btc_balances=setup.btc_balances,
        also_eth=True,
    )


@pytest.mark.parametrize('number_of_eth_accounts', [2])
def test_query_blockchain_balances_ignore_cache(
        rotkehlchen_api_server,
        ethereum_accounts,
        btc_accounts,
):
    """Test that the query blockchain balances endpoint can ignore the cache"""
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen

    setup = setup_balances(rotki, ethereum_accounts=ethereum_accounts, btc_accounts=btc_accounts)
    eth_query = patch.object(
        rotki.chains_aggregator,
        'query_eth_balances',
        wraps=rotki.chains_aggregator.query_eth_balances,
    )
    tokens_query = patch.object(
        rotki.chains_aggregator,
        'query_evm_tokens',
        wraps=rotki.chains_aggregator.query_evm_tokens,
    )

    with ExitStack() as stack:
        setup.enter_blockchain_patches(stack)
        eth_mock = stack.enter_context(eth_query)
        tokens_mock = stack.enter_context(tokens_query)
        # Query ETH and token balances once
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            "named_blockchain_balances_resource",
            blockchain='ETH',
        ))
        result = assert_proper_response_with_result(response)
        assert_eth_balances_result(
            rotki=rotki,
            result=result,
            eth_accounts=ethereum_accounts,
            eth_balances=setup.eth_balances,
            token_balances=setup.token_balances,
            also_btc=False,
        )
        assert eth_mock.call_count == 1
        assert tokens_mock.call_count == 1

        # Query again and make sure this time cache is used
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            "named_blockchain_balances_resource",
            blockchain='ETH',
        ))
        result = assert_proper_response_with_result(response)
        assert_eth_balances_result(
            rotki=rotki,
            result=result,
            eth_accounts=ethereum_accounts,
            eth_balances=setup.eth_balances,
            token_balances=setup.token_balances,
            also_btc=False,
        )
        assert eth_mock.call_count == 1
        assert tokens_mock.call_count == 1

        # Finally query with ignoring the cache
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            "named_blockchain_balances_resource",
            blockchain='ETH',
        ), json={'ignore_cache': True})
        result = assert_proper_response_with_result(response)
        assert_eth_balances_result(
            rotki=rotki,
            result=result,
            eth_accounts=ethereum_accounts,
            eth_balances=setup.eth_balances,
            token_balances=setup.token_balances,
            also_btc=False,
        )
        assert eth_mock.call_count == 2
        assert tokens_mock.call_count == 2


def _add_blockchain_accounts_test_start(
        api_server,
        query_balances_before_first_modification,
        ethereum_accounts,
        btc_accounts,
        async_query,
):
    # Disable caching of query results
    rotki = api_server.rest_api.rotkehlchen
    rotki.chains_aggregator.cache_ttl_secs = 0

    if query_balances_before_first_modification:
        # Also test by having balances queried before adding an account
        eth_balances = ['1000000', '2000000']
        token_balances = {A_RDN: ['0', '4000000']}
        setup = setup_balances(
            rotki,
            ethereum_accounts=ethereum_accounts,
            btc_accounts=btc_accounts,
            eth_balances=eth_balances,
            token_balances=token_balances,
        )
        with ExitStack() as stack:
            setup.enter_blockchain_patches(stack)
            requests.get(api_url_for(
                api_server,
                "blockchainbalancesresource",
            ))

    new_eth_accounts = [make_evm_address(), make_evm_address()]
    all_eth_accounts = ethereum_accounts + new_eth_accounts
    eth_balances = ['1000000', '2000000', '3000000', '4000000']
    token_balances = {A_RDN: ['0', '4000000', '0', '250000000']}
    setup = setup_balances(
        rotki,
        ethereum_accounts=all_eth_accounts,
        btc_accounts=btc_accounts,
        eth_balances=eth_balances,
        token_balances=token_balances,
    )

    # The application has started only with 2 ethereum accounts. Let's add two more
    data = {'accounts': [{'address': x} for x in new_eth_accounts]}
    if async_query:
        data['async_query'] = True
    with ExitStack() as stack:
        setup.enter_ethereum_patches(stack)
        response = requests.put(api_url_for(
            api_server,
            'blockchainsaccountsresource',
            blockchain='ETH',
        ), json=data)

        if async_query:
            task_id = assert_ok_async_response(response)
            result = wait_for_async_task_with_result(
                api_server,
                task_id,
                timeout=ASYNC_TASK_WAIT_TIMEOUT * 4,
            )
        else:
            result = assert_proper_response_with_result(response)

        assert result == new_eth_accounts
        response = requests.get(api_url_for(
            api_server,
            'blockchainbalancesresource',
        ))
        result = assert_proper_response_with_result(response)

    assert_eth_balances_result(
        rotki=rotki,
        result=result,
        eth_accounts=all_eth_accounts,
        eth_balances=setup.eth_balances,
        token_balances=setup.token_balances,
        also_btc=True,
    )
    # Also make sure they are added in the DB
    with rotki.data.db.conn.read_ctx() as cursor:
        accounts = rotki.data.db.get_blockchain_accounts(cursor)
    assert len(accounts.eth) == 4
    assert all(acc in accounts.eth for acc in all_eth_accounts)
    assert len(accounts.btc) == 2
    assert all(acc in accounts.btc for acc in btc_accounts)

    # Now try to query all balances to make sure the result is the stored
    with ExitStack() as stack:
        setup.enter_blockchain_patches(stack)
        response = requests.get(api_url_for(
            api_server,
            "blockchainbalancesresource",
        ))
    result = assert_proper_response_with_result(response)
    assert_eth_balances_result(
        rotki=rotki,
        result=result,
        eth_accounts=all_eth_accounts,
        eth_balances=setup.eth_balances,
        token_balances=setup.token_balances,
        also_btc=True,
    )

    return all_eth_accounts, eth_balances, token_balances


@pytest.mark.parametrize('number_of_eth_accounts', [2])
@pytest.mark.parametrize('btc_accounts', [[UNIT_BTC_ADDRESS1, UNIT_BTC_ADDRESS2]])
@pytest.mark.parametrize('query_balances_before_first_modification', [True, False])
def test_add_blockchain_accounts(
        rotkehlchen_api_server,
        ethereum_accounts,
        btc_accounts,
        query_balances_before_first_modification,
):
    """Test that the endpoint adding blockchain accounts works properly"""

    async_query = random.choice([False, True])
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    all_eth_accounts, eth_balances, token_balances = _add_blockchain_accounts_test_start(
        api_server=rotkehlchen_api_server,
        query_balances_before_first_modification=query_balances_before_first_modification,
        ethereum_accounts=ethereum_accounts,
        btc_accounts=btc_accounts,
        async_query=async_query,
    )
    # Now we will try to add a new BTC account. Setup the mocking infrastructure again
    all_btc_accounts = btc_accounts + [UNIT_BTC_ADDRESS3]
    setup = setup_balances(
        rotki,
        ethereum_accounts=all_eth_accounts,
        btc_accounts=all_btc_accounts,
        eth_balances=eth_balances,
        token_balances=token_balances,
        btc_balances=['3000000', '5000000', '600000000'],
    )
    # add the new BTC account
    with ExitStack() as stack:
        setup.enter_blockchain_patches(stack)
        response = requests.put(api_url_for(
            rotkehlchen_api_server,
            'blockchainsaccountsresource',
            blockchain='BTC',
        ), json={
            'accounts': [{'address': UNIT_BTC_ADDRESS3}],
            'async_query': async_query,
        })
        if async_query:
            task_id = assert_ok_async_response(response)
            result = wait_for_async_task_with_result(rotkehlchen_api_server, task_id)
        else:
            result = assert_proper_response_with_result(response)
        assert result == [UNIT_BTC_ADDRESS3]
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            'blockchainbalancesresource',
            blockchain=SupportedBlockchain.BITCOIN.value,
        ))
        result = assert_proper_response_with_result(response)

    assert_btc_balances_result(
        result=result,
        btc_accounts=all_btc_accounts,
        btc_balances=setup.btc_balances,
        also_eth=False,
    )

    assert rotki.chains_aggregator.accounts.btc[-1] == UNIT_BTC_ADDRESS3
    # Also make sure it's added in the DB
    with rotki.data.db.conn.read_ctx() as cursor:
        accounts = rotki.data.db.get_blockchain_accounts(cursor)
    assert len(accounts.eth) == 4
    assert all(acc in accounts.eth for acc in all_eth_accounts)
    assert len(accounts.btc) == 3
    assert all(acc in accounts.btc for acc in all_btc_accounts)

    # Now try to query all balances to make sure the result is also stored
    with ExitStack() as stack:
        setup.enter_blockchain_patches(stack)
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            'blockchainbalancesresource',
        ), json={'async_query': async_query})
        if async_query:
            task_id = assert_ok_async_response(response)
            outcome = wait_for_async_task_with_result(
                server=rotkehlchen_api_server,
                task_id=task_id,
                timeout=ASYNC_TASK_WAIT_TIMEOUT * 3,
            )
        else:
            outcome = assert_proper_response_with_result(response)

    assert_btc_balances_result(
        result=outcome,
        btc_accounts=all_btc_accounts,
        btc_balances=setup.btc_balances,
        also_eth=True,
    )

    # now try to add an already existing account and see an error is returned
    with ExitStack() as stack:
        setup.enter_blockchain_patches(stack)
        response = requests.put(api_url_for(
            rotkehlchen_api_server,
            'blockchainsaccountsresource',
            blockchain='ETH',
        ), json={'accounts': [{'address': ethereum_accounts[0]}]})
        assert_error_response(
            response=response,
            status_code=HTTPStatus.BAD_REQUEST,
            contained_in_msg=f'Blockchain account/s {ethereum_accounts[0]} already exist',
        )

    # Add a BCH account
    response = requests.put(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='BCH',
    ), json={'accounts': [
        {'address': 'prettyirrelevant.eth'},
        {'address': '12tkqA9xSoowkzoERHMWNKsTey55YEBqkv'},
        {'address': 'pp8skudq3x5hzw8ew7vzsw8tn4k8wxsqsv0lt0mf3g'},
    ]})
    expected_bch_accounts = [
        '1H9EndxvYSibvnDSsxZRYvuqZaCcRXdRcB',
        '12tkqA9xSoowkzoERHMWNKsTey55YEBqkv',
        'pp8skudq3x5hzw8ew7vzsw8tn4k8wxsqsv0lt0mf3g',
    ]
    assert rotki.chains_aggregator.accounts.bch == expected_bch_accounts
    assert_proper_response(response)

    # Check that the BCH accounts are present in the DB
    with rotki.data.db.conn.read_ctx() as cursor:
        accounts = rotki.data.db.get_blockchain_accounts(cursor)
    assert len(accounts.bch) == 3

    # Try adding an already saved BCH address in different format
    response = requests.put(api_url_for(
        rotkehlchen_api_server,
        "blockchainsaccountsresource",
        blockchain='BCH',
    ), json={'accounts': [
        # 12tkqA9xSoowkzoERHMWNKsTey55YEBqkv
        {'address': 'bitcoincash:qq2vrmtj6zg4pw897jwef4fswrfvruwmxcfxq3r9dt'},
        # pp8skudq3x5hzw8ew7vzsw8tn4k8wxsqsv0lt0mf3g
        {'address': '38ty1qB68gHsiyZ8k3RPeCJ1wYQPrUCPPr'},
    ]})
    assert_error_response(response, 'Blockchain account/s bitcoincash:qq2vrmtj6zg4pw897jwef4fswrfvruwmxcfxq3r9dt,38ty1qB68gHsiyZ8k3RPeCJ1wYQPrUCPPr already exist')  # noqa: E501

    # Try adding a segwit BTC address
    response = requests.put(api_url_for(
        rotkehlchen_api_server,
        "blockchainsaccountsresource",
        blockchain='BCH',
    ), json={'accounts': [
        {'address': 'bc1qazcm763858nkj2dj986etajv6wquslv8uxwczt'},
    ]})
    assert_error_response(response, 'Given value bc1qazcm763858nkj2dj986etajv6wquslv8uxwczt is not a valid bitcoin cash address')  # noqa: E501

    # Try adding same BCH address but in different formats
    response = requests.put(api_url_for(
        rotkehlchen_api_server,
        "blockchainsaccountsresource",
        blockchain='BCH',
    ), json={
        'accounts': [
            {'address': '1Mnwij9Zkk6HtmdNzyEUFgp6ojoLaZekP8'},
            {'address': 'bitcoincash:qrjp962nn74p57w0gaf77d335upghk220yceaxqxwa'},
        ],
    })
    assert_error_response(response, 'appears multiple times in the request data')

    # adding a taproot btc address
    response = requests.put(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='BTC',
    ), json={'accounts': [
        {'address': 'bc1pqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqsyjer9e'},
    ]})
    assert_proper_response(response)


@pytest.mark.parametrize('number_of_eth_accounts', [0])
@pytest.mark.parametrize('btc_accounts', [[]])
def test_add_blockchain_accounts_concurrent(rotkehlchen_api_server):
    """Test that if we add blockchain accounts concurrently we won't get any duplicates"""
    ethereum_accounts = [make_evm_address(), make_evm_address(), make_evm_address()]
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen

    query_accounts = ethereum_accounts.copy()
    for _ in range(5):
        query_accounts.extend(ethereum_accounts)
    # Fire all requests almost concurrently. And don't wait for them
    for idx, account in enumerate(query_accounts):
        gevent.spawn_later(
            0.01 * idx,
            requests.put,
            api_url_for(
                rotkehlchen_api_server,
                'blockchainsaccountsresource',
                blockchain='ETH',
            ),
            json={'accounts': [{'address': account}], 'async_query': True},
        )
    # We are making an assumption of sequential ids here. This may not always
    # be the case so for that later down the test we will skip the task check
    # if this happens. Can't think of a better way to do this at the moment
    task_ids = {idx: account for idx, account in enumerate(query_accounts)}

    with gevent.Timeout(ASYNC_TASK_WAIT_TIMEOUT):
        while len(task_ids) != 0:
            task_id, account = random.choice(list(task_ids.items()))
            response = requests.get(
                api_url_for(
                    rotkehlchen_api_server,
                    'specific_async_tasks_resource',
                    task_id=task_id,
                ),
            )
            if response.status_code == HTTPStatus.NOT_FOUND:
                gevent.sleep(.1)  # not started yet
                continue

            assert_proper_response(response, status_code=None)  # do not check status code here
            result = response.json()['result']
            status = result['status']
            if status == 'pending':
                gevent.sleep(.1)
                continue
            if status == 'completed':
                result = result['outcome']
            else:
                raise AssertionError('Should not happen at this point')

            task_ids.pop(task_id)
            if result['result'] is None:
                assert 'already exist' in result['message']
                continue

    assert set(rotki.chains_aggregator.accounts.eth) == set(ethereum_accounts)


@pytest.mark.parametrize('include_etherscan_key', [False])
@pytest.mark.parametrize('number_of_eth_accounts', [0])
def test_no_etherscan_is_detected(rotkehlchen_api_server):
    """Make sure that interacting with ethereum without an etherscan key is given a warning"""
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    new_address = make_evm_address()
    setup = setup_balances(rotki, ethereum_accounts=[new_address], btc_accounts=None)

    with ExitStack() as stack:
        setup.enter_ethereum_patches(stack)
        response = requests.put(api_url_for(
            rotkehlchen_api_server,
            "blockchainsaccountsresource",
            blockchain='ETH',
        ), json={'accounts': [{'address': new_address}]})
        assert_proper_response(response)
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            'blockchainbalancesresource',
        ))
        assert_proper_response(response)

    warnings = rotki.msg_aggregator.consume_warnings()
    assert len(warnings) == 1
    assert 'You do not have an ethereum Etherscan API key configured' in warnings[0]


@pytest.mark.parametrize('method', ['PUT', 'DELETE'])
def test_blockchain_accounts_endpoint_errors(rotkehlchen_api_server, rest_api_port, method):
    """
    Test /api/(version)/blockchains/(name) for edge cases and errors.

    Test for errors when both adding and removing a blockchain account. Both put/delete
    """
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    rotki.chains_aggregator.cache_ttl_secs = 0

    # Provide unsupported blockchain name
    account = '0x00d74c25bbf93df8b2a41d82b0076843b4db0349'
    data = {'accounts': [account]}
    response = requests.request(
        method,
        api_url_for(rotkehlchen_api_server, "blockchainsaccountsresource", blockchain='DDASDAS'),
        json=data,
    )
    assert_error_response(
        response=response,
        contained_in_msg='Failed to deserialize SupportedBlockchain value DDASDAS',
    )

    # Provide no blockchain name
    response = requests.request(
        method,
        f'http://localhost:{rest_api_port}/api/1/blockchains',
        json=data,
    )
    assert_error_response(
        response=response,
        status_code=HTTPStatus.NOT_FOUND,
    )

    # Do not provide accounts
    data = {'dsadsad': 'foo'}
    response = requests.request(
        method,
        api_url_for(rotkehlchen_api_server, 'blockchainsaccountsresource', blockchain='ETH'),
        json=data,
    )
    assert_error_response(
        response=response,
        contained_in_msg='Missing data for required field',
    )

    # Provide wrong type of account
    data = {'accounts': 'foo'}
    response = requests.request(
        method,
        api_url_for(rotkehlchen_api_server, 'blockchainsaccountsresource', blockchain='ETH'),
        json=data,
    )
    if method == 'GET':
        message = "'accounts': ['Not a valid list.'"
    elif method == 'DELETE':
        message = 'Given value foo is not an evm address'
    else:
        message = '"accounts": {"0": {"_schema": ["Invalid input type.'
    assert_error_response(
        response=response,
        contained_in_msg=message,
    )
    assert 'foo' not in rotki.chains_aggregator.accounts.eth

    # Provide empty list
    data = {'accounts': []}
    response = requests.request(
        method,
        api_url_for(rotkehlchen_api_server, "blockchainsaccountsresource", blockchain='ETH'),
        json=data,
    )
    verb = 'add' if method == 'PUT' else 'remove'
    assert_error_response(
        response=response,
        contained_in_msg=f'Empty list of blockchain accounts to {verb} was given',
    )

    # Provide invalid ETH account (more bytes)
    invalid_eth_account = '0x554FFc77f4251a9fB3c0E3590a6a205f8d4e067d01'
    msg = f'Given value {invalid_eth_account} is not an evm address'
    if method == 'PUT':
        data = {'accounts': [{'address': invalid_eth_account}]}
    else:
        data = {'accounts': [invalid_eth_account]}
    response = requests.request(
        method,
        api_url_for(rotkehlchen_api_server, 'blockchainsaccountsresource', blockchain='ETH'),
        json=data,
    )
    assert_error_response(
        response=response,
        contained_in_msg=msg,
    )

    # Provide invalid BTC account
    invalid_btc_account = '18ddjB7HWTaxzvTbLp1nWvaixU3U2oTZ1'
    if method == 'PUT':
        data = {'accounts': [{'address': invalid_btc_account}]}
    else:
        data = {'accounts': [invalid_btc_account]}
    response = requests.request(
        method,
        api_url_for(rotkehlchen_api_server, 'blockchainsaccountsresource', blockchain='BTC'),
        json=data,
    )

    msg = f'Given value {invalid_btc_account} is not a valid bitcoin address'
    assert_error_response(
        response=response,
        contained_in_msg=msg,
    )
    assert_msg = 'Invalid BTC account should not have been added'
    assert invalid_btc_account not in rotki.chains_aggregator.accounts.btc, assert_msg

    # Provide not existing but valid ETH account for removal
    unknown_account = make_evm_address()
    data = {'accounts': [unknown_account]}
    response = requests.delete(
        api_url_for(rotkehlchen_api_server, 'blockchainsaccountsresource', blockchain='ETH'),
        json=data,
    )
    assert_error_response(
        response=response,
        contained_in_msg=f'Tried to remove unknown ETH accounts {unknown_account}',
    )

    # Provide not existing but valid BTC account for removal
    unknown_btc_account = '18ddjB7HWTVxzvTbLp1nWvaBxU3U2oTZF2'
    data = {'accounts': [unknown_btc_account]}
    response = requests.delete(
        api_url_for(rotkehlchen_api_server, 'blockchainsaccountsresource', blockchain='BTC'),
        json=data,
    )
    assert_error_response(
        response=response,
        contained_in_msg=f'Tried to remove unknown BTC accounts {unknown_btc_account}',
    )

    # Provide list with one valid and one invalid account and make sure that nothing
    # is added / removed and the valid one is skipped
    msg = 'Given value 142 is not an evm address'
    if method == 'DELETE':
        # Account should be an existing account
        account = rotki.chains_aggregator.accounts.eth[0]
        data = {'accounts': ['142', account]}
    else:
        # else keep the new account to add
        data = {'accounts': [{'address': '142'}, {'address': account}]}

    response = requests.request(
        method,
        api_url_for(rotkehlchen_api_server, 'blockchainsaccountsresource', blockchain='ETH'),
        json=data,
    )
    assert_error_response(
        response=response,
        contained_in_msg=msg,
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Provide invalid type for accounts
    if method == 'PUT':
        data = {'accounts': [{'address': 15}]}
    else:
        data = {'accounts': [15]}
    response = requests.request(
        method,
        api_url_for(rotkehlchen_api_server, 'blockchainsaccountsresource', blockchain='ETH'),
        json=data,
    )
    assert_error_response(
        response=response,
        contained_in_msg='Not a valid string',
    )

    # Test that providing an account more than once in request data is an error
    account = '0x7BD904A3Db59fA3879BD4c246303E6Ef3aC3A4C6'
    if method == 'PUT':
        data = {'accounts': [{'address': account}, {'address': account}]}
    else:
        data = {'accounts': [account, account]}
    response = requests.request(method, api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=data)
    assert_error_response(
        response=response,
        contained_in_msg=f'Address {account} appears multiple times in the request data',
        status_code=HTTPStatus.BAD_REQUEST,
    )


@pytest.mark.parametrize('number_of_eth_accounts', [0])
def test_add_blockchain_accounts_with_tags_and_label_and_querying_them(rotkehlchen_api_server):
    """Test that adding account with labels and tags works correctly"""
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen

    # Add three tags
    tag1 = {
        'name': 'public',
        'description': 'My public accounts',
        'background_color': 'ffffff',
        'foreground_color': '000000',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag1,
    )
    assert_proper_response(response)
    tag2 = {
        'name': 'desktop',
        'description': 'Accounts that are stored in the desktop PC',
        'background_color': '000000',
        'foreground_color': 'ffffff',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag2,
    )
    assert_proper_response(response)
    tag3 = {
        'name': 'hardware',
        'description': 'hardware wallets',
        'background_color': '000000',
        'foreground_color': 'ffffff',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag3,
    )
    assert_proper_response(response)

    # Now add 3 accounts. Some of them use these tags, some dont
    new_eth_accounts = [make_evm_address(), make_evm_address(), make_evm_address()]
    accounts_data = [{
        'address': new_eth_accounts[0],
        'label': 'my metamask',
        'tags': ['public', 'desktop'],
    }, {
        'address': new_eth_accounts[1],
        'label': 'geth account',
    }, {
        'address': new_eth_accounts[2],
        'tags': ['public', 'hardware'],
    }]
    # Make sure that even adding accounts with label and tags, balance query works fine
    response = requests.put(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json={'accounts': accounts_data})
    assert_proper_response(response)
    with rotki.data.db.conn.read_ctx() as cursor:
        accounts_in_db = rotki.data.db.get_blockchain_accounts(cursor).eth
        assert set(accounts_in_db) == set(new_eth_accounts)

    # Now query the ethereum account data to see that tags and labels are added
    response = requests.get(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ))
    response_data = assert_proper_response_with_result(response)
    assert len(response_data) == len(accounts_data)
    for entry in response_data:
        # find the corresponding account in accounts data
        compare_account = None
        for account in accounts_data:
            if entry['address'] == account['address']:
                compare_account = account
                break
        assert compare_account, 'Found unexpected address {entry["address"]} in response'

        assert entry['address'] == compare_account['address']
        assert entry['label'] == compare_account.get('label', None)
        if entry['tags'] is not None:
            assert set(entry['tags']) == set(compare_account['tags'])
        else:
            assert 'tags' not in compare_account


@pytest.mark.parametrize('number_of_eth_accounts', [3])
@pytest.mark.parametrize('btc_accounts', [[
    UNIT_BTC_ADDRESS1,
    UNIT_BTC_ADDRESS2,
]])
def test_edit_blockchain_accounts(
        rotkehlchen_api_server,
        ethereum_accounts,
):
    """Test that the endpoint editing blockchain accounts works properly"""
    # Add 3 tags
    tag1 = {
        'name': 'public',
        'description': 'My public accounts',
        'background_color': 'ffffff',
        'foreground_color': '000000',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag1,
    )
    assert_proper_response(response)
    tag2 = {
        'name': 'desktop',
        'description': 'Accounts that are stored in the desktop PC',
        'background_color': '000000',
        'foreground_color': 'ffffff',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag2,
    )
    assert_proper_response(response)
    tag3 = {
        'name': 'hardware',
        'description': 'hardware wallets',
        'background_color': '000000',
        'foreground_color': 'ffffff',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag3,
    )
    assert_proper_response(response)

    # Edit 2 out of the 3 accounts so that they have tags
    request_data = {'accounts': [{
        'address': ethereum_accounts[1],
        'label': 'Second account in the array',
        'tags': ['public'],
    }, {
        'address': ethereum_accounts[2],
        'label': 'Thirds account in the array',
        'tags': ['public', 'desktop'],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)

    result = assert_proper_response_with_result(response)
    expected_result = request_data['accounts'] + [
        {'address': ethereum_accounts[0]},
    ]
    compare_account_data(result, expected_result)

    # Also make sure that when querying the endpoint we get the edited account data
    response = requests.get(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ))
    result = assert_proper_response_with_result(response)
    compare_account_data(result, expected_result)

    # Edit 1 account so that both a label is edited but also a tag is removed and a tag is edited
    request_data = {'accounts': [{
        'address': ethereum_accounts[2],
        'label': 'Edited label',
        'tags': ['hardware', 'desktop'],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    response = requests.get(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ))
    result = assert_proper_response_with_result(response)
    for result_entry in result:  # order of return is not guaranteed
        if result_entry['address'] == ethereum_accounts[2]:
            assert result_entry['address'] == request_data['accounts'][0]['address']
            assert result_entry['label'] == request_data['accounts'][0]['label']
            assert set(result_entry['tags']) == set(request_data['accounts'][0]['tags'])
            break
    else:  # did not find account in the for
        raise AssertionError('Edited account not returned in the result')

    # Edit a BTC account
    request_data = {'accounts': [{
        'address': UNIT_BTC_ADDRESS1,
        'label': 'BTC account label',
        'tags': ['public'],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='BTC',
    ), json=request_data)
    result = assert_proper_response_with_result(response)
    assert len(result) == 2
    # Assert the result is in the expected format and is edited
    standalone = result['standalone']
    assert len(standalone) == 2
    assert standalone[0] == {
        'address': UNIT_BTC_ADDRESS1,
        'label': 'BTC account label',
        'tags': ['public'],
    }
    assert standalone[1] == {
        'address': UNIT_BTC_ADDRESS2,
        'label': None,
        'tags': None,
    }
    assert len(result['xpubs']) == 0


@pytest.mark.parametrize('number_of_eth_accounts', [2])
def test_edit_blockchain_account_errors(
        rotkehlchen_api_server,
        ethereum_accounts,
):
    """Test that errors are handled properly in the edit accounts endpoint"""
    # Add two tags
    tag1 = {
        'name': 'public',
        'description': 'My public accounts',
        'background_color': 'ffffff',
        'foreground_color': '000000',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag1,
    )
    assert_proper_response(response)
    tag2 = {
        'name': 'desktop',
        'description': 'Accounts that are stored in the desktop PC',
        'background_color': '000000',
        'foreground_color': 'ffffff',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag2,
    )
    assert_proper_response(response)

    request_data = {'accounts': [{
        'address': ethereum_accounts[0],
        'label': 'Second account in the array',
        'tags': ['public'],
    }, {
        'address': ethereum_accounts[1],
        'label': 'Thirds account in the array',
        'tags': ['public', 'desktop'],
    }]}

    # Missing accounts
    request_data = {'foo': ['a']}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='"accounts": ["Missing data for required field',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Invalid type for accounts
    request_data = {'accounts': 142}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='Invalid input type',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Missing address for an account
    request_data = {'accounts': [{
        'label': 'Second account in the array',
        'tags': ['public'],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='address": ["Missing data for required field',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Invalid type for an account's address
    request_data = {'accounts': [{
        'address': 55,
        'label': 'Second account in the array',
        'tags': ['public'],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='address": ["Not a valid string',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Invalid address for an account's address
    request_data = {'accounts': [{
        'address': 'dsadsd',
        'label': 'Second account in the array',
        'tags': ['public'],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='Given value dsadsd is not an evm address',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Invalid type for label
    request_data = {'accounts': [{
        'address': ethereum_accounts[1],
        'label': 55,
        'tags': ['public'],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='label": ["Not a valid string',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Empty list for tags
    request_data = {'accounts': [{
        'address': ethereum_accounts[1],
        'label': 'a label',
        'tags': [],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='Provided empty list for tags. Use null',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Invalid type for tags
    request_data = {'accounts': [{
        'address': ethereum_accounts[1],
        'label': 'a label',
        'tags': 231,
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='tags": ["Not a valid list',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # Invalid type for tags list entry
    request_data = {'accounts': [{
        'address': ethereum_accounts[1],
        'label': 'a label',
        'tags': [55.221],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='tags": {"0": ["Not a valid string',
        status_code=HTTPStatus.BAD_REQUEST,
    )

    # One non existing tag
    request_data = {'accounts': [{
        'address': ethereum_accounts[1],
        'label': 'a label',
        'tags': ['nonexistant'],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='When editing blockchain accounts, unknown tags nonexistant were found',
        status_code=HTTPStatus.CONFLICT,
    )

    # Mix of existing and non-existing tags
    request_data = {'accounts': [{
        'address': ethereum_accounts[1],
        'label': 'a label',
        'tags': ['a', 'public', 'b', 'desktop', 'c'],
    }]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg='When editing blockchain accounts, unknown tags ',
        status_code=HTTPStatus.CONFLICT,
    )

    # Provide same account multiple times in request data
    request_data = {'accounts': [{
        'address': ethereum_accounts[1],
        'label': 'a label',
        'tags': ['a', 'public', 'b', 'desktop', 'c'],
    }, {
        'address': ethereum_accounts[1],
        'label': 'a label',
        'tags': ['a', 'public', 'b', 'desktop', 'c'],
    }]}
    msg = f'Address {ethereum_accounts[1]} appears multiple times in the request data'
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='ETH',
    ), json=request_data)
    assert_error_response(
        response=response,
        contained_in_msg=msg,
        status_code=HTTPStatus.BAD_REQUEST,
    )


def _remove_blockchain_accounts_test_start(
        api_server,
        query_balances_before_first_modification,
        ethereum_accounts,
        btc_accounts,
        async_query,
):
    # Disable caching of query results
    rotki = api_server.rest_api.rotkehlchen
    rotki.chains_aggregator.cache_ttl_secs = 0
    removed_eth_accounts = [ethereum_accounts[0], ethereum_accounts[2]]
    eth_accounts_after_removal = [ethereum_accounts[1], ethereum_accounts[3]]
    all_eth_balances = ['1000000', '2000000', '3000000', '4000000']
    token_balances = {A_RDN: ['0', '0', '450000000', '0']}
    eth_balances_after_removal = ['2000000', '4000000']
    token_balances_after_removal = {}
    starting_liabilities = {A_DAI: ['5555555', '1000000', '0', '99999999']}
    after_liabilities = {A_DAI: ['1000000', '99999999']}
    # in this part of the test we also check that defi balances for a particular
    # account are deleted when we remove the account
    defi_balances = {
        ethereum_accounts[0]: [
            DefiProtocolBalances(
                protocol=DefiProtocol(
                    name='TEST_PROTOCOL',
                    description='very descriptive description',
                    url='',
                    version=0,
                ),
                balance_type='Debt',
                base_balance=DefiBalance(
                    token_address=A_USDT.resolve_to_evm_token().evm_address,
                    token_name='USDT',
                    token_symbol='USDT',
                    balance=Balance(
                        amount=ONE,
                        usd_value=ONE,
                    ),
                ),
                underlying_balances=[],
            ),
        ],
        ethereum_accounts[1]: [
            DefiProtocolBalances(
                protocol=DefiProtocol(
                    name='TEST_PROTOCOL',
                    description='very descriptive description',
                    url='',
                    version=0,
                ),
                balance_type='Debt',
                base_balance=DefiBalance(
                    token_address=A_USDT.resolve_to_evm_token().evm_address,
                    token_name='USDT',
                    token_symbol='USDT',
                    balance=Balance(
                        amount=ONE,
                        usd_value=ONE,
                    ),
                ),
                underlying_balances=[],
            ),
        ],
    }

    if query_balances_before_first_modification:
        # Also test by having balances queried before removing an account
        setup = setup_balances(
            rotki,
            ethereum_accounts=ethereum_accounts,
            btc_accounts=btc_accounts,
            eth_balances=all_eth_balances,
            token_balances=token_balances,
            liabilities=starting_liabilities,
            defi_balances=defi_balances,
        )
        with ExitStack() as stack:
            setup.enter_blockchain_patches(stack)
            assert_proper_response(requests.get(api_url_for(
                api_server,
                'blockchainbalancesresource',
            )))
        assert rotki.chains_aggregator.defi_balances == defi_balances  # check that defi balances were populated  # noqa: E501

    setup = setup_balances(
        rotki,
        ethereum_accounts=ethereum_accounts,
        btc_accounts=btc_accounts,
        eth_balances=all_eth_balances,
        token_balances=token_balances,
        liabilities=starting_liabilities,
        defi_balances=defi_balances,
    )

    # The application has started with 4 ethereum accounts. Remove two and see that balances match
    with ExitStack() as stack:
        setup.enter_ethereum_patches(stack)
        response = requests.delete(api_url_for(
            api_server,
            "blockchainsaccountsresource",
            blockchain='ETH',
        ), json={'accounts': removed_eth_accounts, 'async_query': async_query})
        if async_query:
            task_id = assert_ok_async_response(response)
            result = wait_for_async_task_with_result(api_server, task_id)
        else:
            result = assert_proper_response_with_result(response)

    if query_balances_before_first_modification is True:
        assert_eth_balances_result(
            rotki=rotki,
            result=result,
            eth_accounts=eth_accounts_after_removal,
            eth_balances=eth_balances_after_removal,
            token_balances=token_balances_after_removal,
            also_btc=True,
            expected_liabilities=after_liabilities,
        )

    # check that after removing ethereum account defi balances were updated
    if query_balances_before_first_modification:
        assert rotki.chains_aggregator.defi_balances == {eth_accounts_after_removal[0]: defi_balances[eth_accounts_after_removal[0]]}  # noqa: E501

    # Also make sure they are removed from the DB
    with rotki.data.db.conn.read_ctx() as cursor:
        accounts = rotki.data.db.get_blockchain_accounts(cursor)
    assert len(accounts.eth) == 2
    assert all(acc in accounts.eth for acc in eth_accounts_after_removal)
    assert len(accounts.btc) == 2
    assert all(acc in accounts.btc for acc in btc_accounts)

    # Now try to query all balances to make sure the result is the stored
    with ExitStack() as stack:
        setup.enter_blockchain_patches(stack)
        response = requests.get(api_url_for(
            api_server,
            "blockchainbalancesresource",
        ))
    result = assert_proper_response_with_result(response)
    assert_eth_balances_result(
        rotki=rotki,
        result=result,
        eth_accounts=eth_accounts_after_removal,
        eth_balances=eth_balances_after_removal,
        token_balances=token_balances_after_removal,
        also_btc=True,
        expected_liabilities=after_liabilities,
    )

    return eth_accounts_after_removal, eth_balances_after_removal, token_balances_after_removal


@pytest.mark.parametrize('number_of_eth_accounts', [4])
@pytest.mark.parametrize('btc_accounts', [[UNIT_BTC_ADDRESS1, UNIT_BTC_ADDRESS2]])
@pytest.mark.parametrize('query_balances_before_first_modification', [True, False])
def test_remove_blockchain_accounts(
        rotkehlchen_api_server,
        ethereum_accounts,
        btc_accounts,
        query_balances_before_first_modification,
):
    """Test that the endpoint removing blockchain accounts works properly"""

    async_query = random.choice([False, True])
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen
    (
        eth_accounts_after_removal,
        eth_balances_after_removal,
        token_balances_after_removal,
    ) = _remove_blockchain_accounts_test_start(
        api_server=rotkehlchen_api_server,
        query_balances_before_first_modification=query_balances_before_first_modification,
        ethereum_accounts=ethereum_accounts,
        btc_accounts=btc_accounts,
        async_query=async_query,
    )

    # Now we will try to remove a BTC account. Setup the mocking infrastructure again
    all_btc_accounts = [UNIT_BTC_ADDRESS1, UNIT_BTC_ADDRESS2]
    btc_accounts_after_removal = [UNIT_BTC_ADDRESS2]
    setup = setup_balances(
        rotki,
        ethereum_accounts=eth_accounts_after_removal,
        btc_accounts=all_btc_accounts,
        eth_balances=eth_balances_after_removal,
        token_balances=token_balances_after_removal,
        btc_balances=['3000000', '5000000'],
    )
    # remove the new BTC account
    with ExitStack() as stack:
        setup.enter_blockchain_patches(stack)
        response = requests.delete(api_url_for(
            rotkehlchen_api_server,
            'blockchainsaccountsresource',
            blockchain=SupportedBlockchain.BITCOIN.value,
        ), json={'accounts': [UNIT_BTC_ADDRESS1], 'async_query': async_query})
        if async_query:
            task_id = assert_ok_async_response(response)
            outcome = wait_for_async_task_with_result(rotkehlchen_api_server, task_id)
        else:
            outcome = assert_proper_response_with_result(response)
    assert_btc_balances_result(
        result=outcome,
        btc_accounts=btc_accounts_after_removal,
        btc_balances=['5000000'],
        also_eth=True,
    )

    # Also make sure it's removed from the DB
    with rotki.data.db.conn.read_ctx() as cursor:
        accounts = rotki.data.db.get_blockchain_accounts(cursor)
    assert len(accounts.eth) == 2
    assert all(acc in accounts.eth for acc in eth_accounts_after_removal)
    assert len(accounts.btc) == 1
    assert all(acc in accounts.btc for acc in btc_accounts_after_removal)

    # Now try to query all balances to make sure the result is also stored
    with ExitStack() as stack:
        setup.enter_blockchain_patches(stack)
        response = requests.get(api_url_for(
            rotkehlchen_api_server,
            "blockchainbalancesresource",
        ), json={'async_query': async_query})
        if async_query:
            task_id = assert_ok_async_response(response)
            outcome = wait_for_async_task_with_result(
                server=rotkehlchen_api_server,
                task_id=task_id,
                timeout=ASYNC_TASK_WAIT_TIMEOUT * 3,
            )
        else:
            outcome = assert_proper_response_with_result(response)

    assert_btc_balances_result(
        result=outcome,
        btc_accounts=btc_accounts_after_removal,
        btc_balances=['5000000'],
        also_eth=True,
    )


@pytest.mark.parametrize('number_of_eth_accounts', [2])
def test_remove_nonexisting_blockchain_account_along_with_existing(
        rotkehlchen_api_server,
        ethereum_accounts,
):
    """Test that if an existing and a non-existing account are given to remove, nothing is"""
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen

    # Add a tag
    tag1 = {
        'name': 'public',
        'description': 'My public accounts',
        'background_color': 'ffffff',
        'foreground_color': '000000',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag1,
    )
    assert_proper_response(response)
    # Edit the first ethereum account which we will attempt to delete
    # to have this tag so that we see the mapping is still there afterwards
    request_data = {'accounts': [{'address': ethereum_accounts[0], 'tags': ['public']}]}
    response = requests.patch(api_url_for(
        rotkehlchen_api_server,
        "blockchainsaccountsresource",
        blockchain='ETH',
    ), json=request_data)
    assert_proper_response(response)
    expected_data = request_data['accounts'] + [
        {'address': ethereum_accounts[1]},
    ]
    compare_account_data(response.json()['result'], expected_data)

    eth_balances = ['11110', '22222']
    setup = setup_balances(
        rotki,
        ethereum_accounts=ethereum_accounts,
        btc_accounts=None,
        eth_balances=eth_balances,
        token_balances=None,
    )
    unknown_account = make_evm_address()
    with ExitStack() as stack:
        setup.enter_ethereum_patches(stack)
        response = requests.delete(api_url_for(
            rotkehlchen_api_server,
            "blockchainsaccountsresource",
            blockchain='ETH',
        ), json={'accounts': [ethereum_accounts[0], unknown_account]})
    assert_error_response(
        response=response,
        contained_in_msg=f'Tried to remove unknown ETH accounts {unknown_account}',
        status_code=HTTPStatus.BAD_REQUEST,
    )
    # Also make sure that no account was removed from the DB
    with rotki.data.db.conn.read_ctx() as cursor:
        accounts = rotki.data.db.get_blockchain_accounts(cursor)
    assert len(accounts.eth) == 2
    assert all(acc in accounts.eth for acc in ethereum_accounts)
    # Also make sure no tag mappings were removed
    cursor = rotki.data.db.conn.cursor()
    query = cursor.execute('SELECT object_reference, tag_name FROM tag_mappings;').fetchall()
    assert len(query) == 1
    assert query[0][0] == f'{SupportedBlockchain.ETHEREUM.value}{ethereum_accounts[0]}'
    assert query[0][1] == 'public'


@pytest.mark.parametrize('number_of_eth_accounts', [0])
def test_remove_blockchain_account_with_tags_removes_mapping(rotkehlchen_api_server):
    """Test that removing an account with tags remove the mappings"""
    rotki = rotkehlchen_api_server.rest_api.rotkehlchen

    # Add two tags
    tag1 = {
        'name': 'public',
        'description': 'My public accounts',
        'background_color': 'ffffff',
        'foreground_color': '000000',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag1,
    )
    assert_proper_response(response)
    tag2 = {
        'name': 'desktop',
        'description': 'Accounts that are stored in the desktop PC',
        'background_color': '000000',
        'foreground_color': 'ffffff',
    }
    response = requests.put(
        api_url_for(
            rotkehlchen_api_server,
            'tagsresource',
        ), json=tag2,
    )
    assert_proper_response(response)

    # Now add 2 accounts both of them using tags
    new_btc_accounts = [UNIT_BTC_ADDRESS1, UNIT_BTC_ADDRESS2]
    accounts_data = [{
        "address": new_btc_accounts[0],
        "label": 'my btc miner',
        'tags': ['public', 'desktop'],
    }, {
        "address": new_btc_accounts[1],
        'label': 'other account',
        'tags': ['desktop'],
    }]
    response = requests.put(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain='BTC',
    ), json={'accounts': accounts_data})
    assert_proper_response(response)
    expected_accounts_data = [
        SingleBlockchainAccountData(
            address=new_btc_accounts[0],
            label='my btc miner',
            tags=['desktop', 'public'],
        ),
        SingleBlockchainAccountData(
            address=new_btc_accounts[1],
            label='other account',
            tags=['desktop'],
        ),
    ]
    with rotki.data.db.conn.read_ctx() as cursor:
        accounts_in_db = rotki.data.db.get_blockchain_account_data(
            cursor=cursor,
            blockchain=SupportedBlockchain.BITCOIN,
        )
        assert accounts_in_db == expected_accounts_data

    # now remove one account
    response = requests.delete(api_url_for(
        rotkehlchen_api_server,
        'blockchainsaccountsresource',
        blockchain=SupportedBlockchain.BITCOIN.value,
    ), json={'accounts': [UNIT_BTC_ADDRESS1]})
    assert_proper_response(response)

    assert rotki.chains_aggregator.accounts.btc == [UNIT_BTC_ADDRESS2]

    # Now check the DB directly and see that tag mappings of the deleted account are gone
    cursor = rotki.data.db.conn.cursor()
    query = cursor.execute('SELECT object_reference, tag_name FROM tag_mappings;').fetchall()
    assert len(query) == 1
    assert query[0][0] == f'{SupportedBlockchain.BITCOIN.value}{UNIT_BTC_ADDRESS2}'
    assert query[0][1] == 'desktop'
