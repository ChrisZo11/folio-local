from datetime import date
from decimal import Decimal as D
import pytest

from portfolio.importer import save_manual, delete_transaction
from portfolio.ledger import build_ledger, dec, xirr


def test_partial_sales_fifo_average_and_fx(db,trade):
    save_manual(db,{**trade,'symbol':'SCHD','quantity':'2','price':'10','currency':'USD','fx_rate':'15000'})
    save_manual(db,{**trade,'symbol':'SCHD','date':'2025-02-01','quantity':'2','price':'20','currency':'USD','fx_rate':'16000'})
    save_manual(db,{**trade,'symbol':'SCHD','date':'2025-03-01','type':'sell','quantity':'1','price':'30','fee':'1','currency':'USD','fx_rate':'17000'})
    fifo,_,_=build_ledger(db.transactions(),'fifo')
    average,_,_=build_ledger(db.transactions(),'average')
    p=fifo[(1,'SCHD')]
    assert p['quantity']==D(3)
    assert p['cost']==D(50)
    assert p['base_cost']==D(790000)
    assert p['realized']==D(19)
    assert p['realized_base']==D(343000)
    a=average[(1,'SCHD')]
    assert a['cost']==D(45)
    assert a['base_cost']==D(705000)
    assert a['realized_base']==D(258000)


def test_split_preserves_cost_and_accepts_following_sale(db,trade):
    save_manual(db,trade)
    save_manual(db,{**trade,'type':'split','date':'2025-02-01','quantity':'2','price':'0'})
    save_manual(db,{**trade,'type':'sell','date':'2025-03-01','quantity':'150','price':'600'})
    positions,_,_=build_ledger(db.transactions())
    p=positions[(1,'BBCA')]
    assert p['quantity']==D(50)
    assert p['cost']==D(25000)
    assert p['realized']==D(15000)


def test_oversell_and_deletion_are_atomic(db,trade):
    buy=save_manual(db,trade)
    with pytest.raises(ValueError,match='exceeds'):
        save_manual(db,{**trade,'date':'2025-03-01','type':'sell','quantity':'101'})
    assert len(db.transactions())==1
    save_manual(db,{**trade,'date':'2025-03-01','type':'sell','quantity':'50'})
    with pytest.raises(ValueError,match='exceeds'):
        delete_transaction(db,buy)
    assert len(db.transactions())==2


def test_edit_buy_cannot_break_later_sale(db,trade):
    buy=save_manual(db,trade)
    save_manual(db,{**trade,'date':'2025-02-01','type':'sell','quantity':'80'})
    with pytest.raises(ValueError,match='exceeds'):
        save_manual(db,{**trade,'quantity':'20'},buy)
    assert db.transactions()[0]['quantity']=='100'


def test_lots_fractional_shares_and_cash(db,trade):
    save_manual(db,{**trade,'quantity':'2','quantity_unit':'lot','affects_cash':True})
    save_manual(db,{**trade,'symbol':'VOO','quantity':'.0125','price':'500','currency':'USD','fx_rate':'16000'})
    positions,cash,_=build_ledger(db.transactions())
    assert positions[(1,'BBCA')]['quantity']==200
    assert positions[(1,'VOO')]['quantity']==D('.0125')
    assert cash[(1,'IDR')]==-200000


@pytest.mark.parametrize('change',[
    {'quantity':'-1'},{'quantity_unit':''},{'symbol':'NOTREAL'},
    {'currency':'EUR'},{'fx_rate':'0'},{'date':'bad'},{'price':'NaN'},
    {'date':'2099-01-01'},{'type':'execute_trade'}, {'fee':'Infinity'},
])
def test_validation_at_boundary(db,trade,change):
    with pytest.raises(ValueError):
        save_manual(db,{**trade,**change})
    assert db.transactions()==[]


def test_usd_fx_is_never_guessed(db,trade):
    with pytest.raises(ValueError,match='trade-date'):
        save_manual(db,{**trade,'symbol':'VOO','currency':'USD','fx_rate':''})


def test_xirr_hand_calculated_irregular_flows():
    # Cash flows constructed to have exactly a 10% annual return on actual/365 dates.
    first=date(2023,1,1)
    middle=date(2023,6,17)
    last=date(2024,1,1)
    terminal=1000*1.1**((last-first).days/365)+500*1.1**((last-middle).days/365)
    assert xirr([(first.isoformat(),-1000),(middle.isoformat(),-500),(last.isoformat(),terminal)])==pytest.approx(.1,abs=1e-9)
    assert xirr([('2024-01-01',-100),('2024-01-01',110)]) is None
    assert xirr([('2024-01-01',100),('2025-01-01',110)]) is None


def test_net_dividend_cash_and_tax(db,trade):
    save_manual(db,trade)
    save_manual(db,{**trade,'type':'dividend','date':'2025-02-01','quantity':'100','price':'1000','tax':'150','fee':'10','affects_cash':True})
    positions,cash,_=build_ledger(db.transactions())
    assert positions[(1,'BBCA')]['dividends']==840
    assert cash[(1,'IDR')]==840
    with pytest.raises(ValueError,match='exceed'):
        save_manual(db,{**trade,'type':'dividend','price':'100','tax':'101'})
