import pytest
from portfolio.importer import preview, commit, save_manual, undo_import

HEADER='date,portfolio,type,symbol,quantity,quantity_unit,price,fee,tax,currency,fx_rate,note\n'
BUY='2025-01-01,Stockbit,buy,BBCA,100,share,1000,0,0,IDR,1,test\n'


def test_preview_has_no_writes_and_import_is_idempotent(db):
    text=HEADER+BUY+BUY
    assert preview(db,text)['added']==2
    assert db.transactions()==[]
    assert commit(db,text)['added']==2
    repeated=commit(db,text)
    assert repeated['added']==0
    assert repeated['skipped']==2
    assert len(db.transactions())==2


def test_distinct_same_day_trades_are_kept(db):
    assert commit(db,HEADER+BUY+BUY.replace('1000,','1100,'))['added']==2


def test_import_reports_invalid_rows(db):
    result=commit(db,HEADER+BUY+BUY.replace('BBCA','UNKNOWN')+BUY.replace('100,share','-1,share'))
    assert result['added']==1
    assert result['failed']==2
    assert result['rows'][1]['line']==3
    assert 'Unknown ticker' in result['rows'][1]['error']
    assert len(db.transactions())==1


def test_import_undo_protects_later_sales(db,trade):
    result=commit(db,HEADER+BUY)
    save_manual(db,{**trade,'type':'sell','date':'2025-02-01','quantity':'50'})
    with pytest.raises(ValueError,match='exceeds'):
        undo_import(db,result['import_id'])
    assert len(db.transactions())==2


def test_undo_preserves_manually_edited_rows_and_allows_reimport(db,trade):
    result=commit(db,HEADER+BUY)
    tx=db.transactions()[0]
    save_manual(db,{**trade,'quantity':'150'},tx['id'])
    undo_import(db,result['import_id'])
    assert db.transactions()[0]['quantity']=='150'
    assert commit(db,HEADER+BUY)['skipped']==1


def test_undo_then_reimport(db):
    result=commit(db,HEADER+BUY)
    undo_import(db,result['import_id'])
    assert not db.transactions()
    assert commit(db,HEADER+BUY)['added']==1


def test_fee_estimate_and_explicit_lot_adapter(db):
    text='date,portfolio,action,ticker,quantity,unit,price,currency,fx_rate\n2025-01-01,Stockbit,buy,BBCA,1,lot,1000,IDR,1\n'
    result=commit(db,text,source='stockbit',default_fee_rate='.0015')
    assert result['added']==1
    row=db.transactions()[0]
    assert row['quantity']=='100'
    assert row['fee']=='150'
    assert row['fee_estimated']==1


def test_missing_unit_is_flagged(db):
    result=preview(db,HEADER+BUY.replace('100,share','100,'))
    assert result['failed']==1
    assert 'quantity_unit' in result['rows'][0]['error']


def test_newest_first_csv_is_validated_chronologically(db):
    sale=BUY.replace('2025-01-01','2025-02-01').replace(',buy,',',sell,').replace(',100,share',',50,share')
    result=commit(db,HEADER+sale+BUY)
    assert result['added']==2
    assert result['failed']==0
    assert [t['type'] for t in db.transactions()]==['buy','sell']


def test_duplicate_with_conflicting_fx_or_fees_requires_review(db):
    commit(db,HEADER+BUY)
    result=preview(db,HEADER+BUY.replace('1000,0,0','1000,5,0'))
    assert result['failed']==1
    assert 'matching trade' in result['rows'][0]['error']
    assert db.transactions()[0]['fee']=='0'


def test_holdings_only_csv(db):
    text='date,portfolio,ticker,quantity,quantity_unit,average_cost,currency,fx_rate\n2025-01-01,Stockbit,BBCA,100,share,1000,IDR,1\n'
    result=commit(db,text)
    assert result['added']==1
    assert db.transactions()[0]['type']=='opening'
