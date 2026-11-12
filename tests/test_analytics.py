from datetime import timedelta
import pytest
from portfolio.analytics import Analytics
from portfolio.db import now, today
from portfolio.importer import save_manual


def quote(db,symbol,price,currency='IDR'):
    with db.connect() as conn:
        conn.execute('INSERT OR REPLACE INTO quotes VALUES (?,?,?,?,?,?,?,?)',(symbol,str(price),str(price),currency,now(),now(),1,'demo'))


def test_multi_currency_base_cost_and_currency_effect(db,trade):
    save_manual(db,{**trade,'symbol':'SCHD','quantity':'1','price':'100','currency':'USD','fx_rate':'15000'})
    quote(db,'SCHD',110,'USD')
    with db.connect() as conn:
        conn.execute('INSERT INTO fx_rates VALUES (?,?,?,?)',('USDIDR',today().isoformat(),'16000',now()))
    data=Analytics(db).summary()
    assert data['total_value']==1760000
    assert data['invested']==1500000
    assert data['total_profit']==260000
    assert data['holdings'][0]['currency_effect']==100000
    usd=Analytics(db).summary(base_currency='USD')
    assert usd['total_value']==110
    assert usd['invested']==93.75


def test_missing_quote_is_not_silently_zero(db,trade):
    save_manual(db,trade)
    data=Analytics(db).summary()
    assert not data['complete']
    assert data['holdings'][0]['value'] is None
    assert data['total_value'] is None
    assert data['total_profit'] is None
    assert data['irr'] is None
    assert any('no cached price' in w for w in data['warnings'])


def test_opening_balance_limits_performance(db,trade):
    save_manual(db,{**trade,'type':'opening'})
    quote(db,'BBCA',1100)
    assert Analytics(db).summary()['irr'] is None
    result=Analytics(db).performance()
    assert result['xirr'] is None
    assert 'Holdings-only' in result['warnings'][0]


def test_twr_and_xirr_handle_external_contribution(db,trade):
    start=today()-timedelta(days=3)
    save_manual(db,{**trade,'date':start.isoformat(),'quantity':'1','price':'100'})
    save_manual(db,{**trade,'date':(start+timedelta(days=1)).isoformat(),'quantity':'1','price':'110'})
    with db.connect() as conn:
        for offset,close in enumerate((100,110,121,121)):
            conn.execute('INSERT INTO prices VALUES (?,?,?,?,?,?)',('BBCA',(start+timedelta(days=offset)).isoformat(),str(close),None,'IDR',now()))
    result=Analytics(db).performance()
    assert result['warnings']==[]
    assert result['twr']==pytest.approx(.21)
    assert len(result['series'])==4
    assert result['series'][-1]['value']==242
    assert result['contributors']==[{'symbol':'BBCA','profit':32.0}]


def test_holdings_dividend_and_allocation(db,trade):
    save_manual(db,trade)
    save_manual(db,{**trade,'date':'2025-02-01','type':'dividend','price':'1000','tax':'100'})
    quote(db,'BBCA',1100)
    analytics=Analytics(db)
    data=analytics.summary()
    assert data['total_profit']==10900
    assert data['dividends_received']==900
    assert analytics.allocation()['groups'][0]['weight']==1
    assert analytics.concentration()['hhi']==1
    assert analytics.dividends()['total_net']==900


def test_backup_is_consistent_and_does_not_overwrite(db,trade,tmp_path):
    save_manual(db,trade)
    path=tmp_path/'copy.db'
    db.backup(path)
    from portfolio.db import Database
    assert len(Database(path).transactions())==1
    with pytest.raises(ValueError,match='already exists'):
        db.backup(path)


def test_dividend_share_discrepancy_preserves_both(db,trade):
    save_manual(db,trade)
    save_manual(db,{**trade,'type':'dividend','date':'2025-03-10','quantity':'90','price':'500'})
    with db.connect() as conn:
        conn.execute('INSERT INTO dividends(symbol,ex_date,pay_date,amount_per_share,currency,source) VALUES (?,?,?,?,?,?)',('BBCA','2025-03-01',None,'5','IDR','provider'))
    data=Analytics(db).dividends()
    assert data['confirmed'][0]['reported_shares']==90
    p=data['proposals'][0]
    assert p['quantity']==100
    assert p['reported_shares']==90
    assert p['reconciliation_warning']


def test_trailing_dividends_are_not_truncated_by_report_period(db,trade):
    save_manual(db,trade)
    save_manual(db,{**trade,'date':(today()-timedelta(days=90)).isoformat(),'type':'dividend','price':'500','tax':'50'})
    data=Analytics(db).dividends(period='1M')
    assert data['total_net']==0
    assert data['trailing_12_months']==450


def test_usd_xirr_uses_native_historical_cashflows(db,trade):
    start=today()-timedelta(days=365)
    save_manual(db,{**trade,'date':start.isoformat(),'symbol':'SCHD','quantity':'1','price':'100','currency':'USD','fx_rate':'15000'})
    quote(db,'SCHD',110,'USD')
    with db.connect() as conn:
        conn.execute('INSERT INTO fx_rates VALUES (?,?,?,?)',('USDIDR',today().isoformat(),'16000',now()))
    analytics=Analytics(db)
    assert analytics.summary(base_currency='USD')['irr']==pytest.approx(.1)
    assert analytics.summary(base_currency='IDR')['irr']==pytest.approx(1760000/1500000-1)


def test_expenses_reduce_profit_without_reducing_security_cost(db,trade):
    save_manual(db,trade)
    save_manual(db,{**trade,'type':'fee','symbol':None,'date':'2025-02-01','quantity':'0','price':'500','fee':'10'})
    quote(db,'BBCA',1100)
    data=Analytics(db).summary()
    assert data['invested']==100000
    assert data['total_profit']==9490
    expense=Analytics(db).transactions()['transactions'][0]
    assert expense['total']==510


def test_zero_cash_does_not_require_fx(db,trade):
    save_manual(db,{**trade,'symbol':None,'type':'deposit','currency':'USD','price':'100','fx_rate':'16000'})
    save_manual(db,{**trade,'symbol':None,'type':'withdrawal','currency':'USD','price':'100','fx_rate':'16000'})
    data=Analytics(db).summary()
    assert data['complete']
    assert data['total_value']==0


def test_performance_in_usd_does_not_need_fx_for_empty_opening_day(db,trade):
    start=today()-timedelta(days=1)
    save_manual(db,{**trade,'date':start.isoformat(),'symbol':'SCHD','quantity':'1','price':'100','currency':'USD','fx_rate':'15000'})
    with db.connect() as conn:
        for d,p in [(start,100),(today(),110)]:
            conn.execute('INSERT INTO fx_rates VALUES (?,?,?,?)',('USDIDR',d.isoformat(),'15000',now()))
            conn.execute('INSERT INTO prices VALUES (?,?,?,?,?,?)',('SCHD',d.isoformat(),str(p),None,'USD',now()))
    result=Analytics(db).performance(base_currency='USD')
    assert len(result['series'])==2
    assert result['twr']==pytest.approx(.1)
