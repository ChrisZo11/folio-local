import asyncio
import pytest
from fastapi.testclient import TestClient
from portfolio.api import create_app
from portfolio.market import MarketService
from portfolio.mcp_server import create_server
from portfolio.db import now, today
from portfolio.importer import save_manual


class OfflineProvider:
    def get_quote(self,*args):
        raise ConnectionError('offline')
    get_history=get_dividends=get_fx_rate=get_metadata=get_quote


class NoNetworkMarket(MarketService):
    def refresh_if_due(self,*args):
        pass


def test_api_create_edit_delete_and_origin_protection(db,trade):
    client=TestClient(create_app(db,NoNetworkMarket(db)))
    assert client.get('/api/health').json()['local_only']
    assert client.post('/api/transactions',json=trade,headers={'Origin':'https://malicious.example'}).status_code==403
    assert not db.transactions()
    created=client.post('/api/transactions',json=trade)
    assert created.status_code==201
    tid=created.json()['id']
    assert client.put(f'/api/transactions/{tid}',json={**trade,'quantity':'200'}).status_code==200
    assert client.get('/api/holdings').json()['holdings'][0]['quantity']==200
    assert client.delete(f'/api/transactions/{tid}',headers={'Content-Type':'application/json'}).status_code==200
    assert not db.transactions()
    assert client.get('/api/summary',headers={'Host':'evil.example'}).status_code==400


def test_import_requires_confirmation(db):
    client=TestClient(create_app(db,NoNetworkMarket(db)))
    response=client.post('/api/imports/commit',json={'text':'date,portfolio,type\n'})
    assert response.status_code==400
    assert 'confirm' in response.json()['error']


def test_api_does_not_default_missing_usd_fx_to_one(db,trade):
    client=TestClient(create_app(db,NoNetworkMarket(db)))
    payload={k:v for k,v in trade.items() if k!='fx_rate'}
    response=client.post('/api/transactions',json={**payload,'symbol':'SCHD','currency':'USD'})
    assert response.status_code==400
    assert 'trade-date' in response.json()['error']


def test_provider_failure_retains_quote(db,trade):
    save_manual(db,trade)
    with db.connect() as conn:
        conn.execute('INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?)',('BBCA','1000','990','IDR',now(),now(),1,'yahoo'))
    result=MarketService(db,OfflineProvider()).refresh()
    assert len(result['errors'])==2
    assert db.rows('SELECT price FROM quotes')[0]['price']=='1000'


def test_mcp_tools_are_registered_and_match_api(db,trade):
    save_manual(db,trade)
    market=NoNetworkMarket(db)
    server=create_server(db,market)
    tools=asyncio.run(server.list_tools())
    assert {t.name for t in tools}=={'list_portfolios','get_holdings','get_transactions','get_quote','get_portfolio_value','get_performance','get_allocation','get_concentration','get_dividends','refresh_prices','data_status'}
    # Exercise the actual MCP tool dispatcher, not a duplicate engine implementation.
    result=asyncio.run(server.call_tool('get_holdings',{}))
    assert result is not None
    import json
    blocks=result[0] if isinstance(result,tuple) else result
    body=json.loads(blocks[0].text)
    http=TestClient(create_app(db,market)).get('/api/holdings').json()
    assert body['holdings']==http['holdings']
    assert body['warnings']==http['warnings']


def test_custom_asset_manual_valuation_and_portfolio(db):
    client=TestClient(create_app(db,NoNetworkMarket(db)))
    pid=client.post('/api/portfolios',json={'name':'Bonds'}).json()['id']
    asset={'symbol':'MY-BOND','name':'My bond','currency':'IDR','custom':True,'asset_class':'Bonds'}
    assert client.post('/api/instruments',json=asset).status_code==201
    assert client.post('/api/instruments/MY-BOND/valuation',json={'price':'120000','date':today().isoformat()}).status_code==200
    assert client.post('/api/transactions',json={'portfolio_id':pid,'date':today().isoformat(),'type':'opening','symbol':'MY-BOND','quantity':'1','price':'100000','currency':'IDR'}).status_code==201
    data=client.get(f'/api/summary?portfolio={pid}').json()
    assert data['total_value']==120000
    assert data['total_profit']==20000
    assert data['irr'] is None
