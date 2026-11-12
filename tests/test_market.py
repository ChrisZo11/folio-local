from datetime import datetime, timezone

from portfolio.market import YahooProvider, quote_status


class History:
    def iterrows(self):
        return iter([
            (datetime(2025,1,1),{'Close':50.,'Adj Close':49.,'Stock Splits':0.,'Dividends':.5}),
            (datetime(2025,2,1),{'Close':55.,'Adj Close':55.,'Stock Splits':2.,'Dividends':.3}),
        ])


class Ticker:
    def history(self,**kwargs):
        return History()


def test_provider_restores_prices_and_dividends_before_split(tmp_path,monkeypatch):
    provider=YahooProvider(tmp_path)
    monkeypatch.setattr(provider,'_ticker',lambda symbol:Ticker())
    prices=provider.get_history('TEST','2025-01-01')
    assert prices[0]['close']=='100'
    assert prices[1]['close']=='55'
    payouts=provider.get_dividends('TEST','2025-01-01')
    assert payouts[0]['amount_per_share']=='1'
    assert payouts[0]['adjusted_amount_per_share']=='0.5'
    assert payouts[1]['amount_per_share']=='0.3'


def test_market_closed_weekend_quote_is_not_mislabeled_stale():
    quote={'price':'100','as_of':'2026-11-06T21:00:00+00:00','fetched_at':'2026-11-08T12:00:00+00:00','origin':'yahoo'}
    instrument={'exchange':'NYSE','custom':0}
    reference=datetime(2026,11,8,12,tzinfo=timezone.utc)
    status=quote_status(quote,instrument,reference=reference)
    assert status['status']=='market closed'
    assert not status['stale']


def test_idx_quotes_explicitly_indicate_delay():
    quote={'price':'100','as_of':'2026-11-12T03:00:00+00:00','fetched_at':'2026-11-12T03:10:00+00:00','origin':'yahoo'}
    instrument={'exchange':'IDX','custom':0}
    reference=datetime(2026,11,12,3,10,tzinfo=timezone.utc)
    status=quote_status(quote,instrument,reference=reference)
    assert status['delay']=='Delayed ~10 min'
    assert not status['stale']
