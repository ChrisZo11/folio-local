"""Provider boundary and persistent cache. Only symbols are sent to Yahoo."""
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Protocol

from .db import ROOT, now, today
from .ledger import canonical, dec, valid_symbol

logger = logging.getLogger(__name__)


class PriceProvider(Protocol):
    def get_quote(self, symbol: str) -> dict: ...
    def get_history(self, symbol: str, start: str) -> list[dict]: ...
    def get_dividends(self, symbol: str, start: str) -> list[dict]: ...
    def get_metadata(self, symbol: str) -> dict: ...
    def get_fx_rate(self, pair: str, start: str) -> list[dict]: ...


class YahooProvider:
    def __init__(self, cache_dir=None):
        from pathlib import Path
        self.cache_dir=Path(cache_dir or ROOT/'data'/'yahoo-cache')
        self.configured=False

    def _ticker(self, symbol):
        import yfinance as yf
        from curl_cffi.requests import Session
        if not self.configured:
            self.cache_dir.mkdir(parents=True,exist_ok=True)
            yf.set_tz_cache_location(str(self.cache_dir))
            self.configured=True
        return yf.Ticker(valid_symbol(symbol), session=Session(impersonate='chrome', timeout=7))

    def get_metadata(self, symbol):
        info = self._ticker(symbol).get_info()
        currency = info.get('currency')
        if currency not in {'IDR', 'USD'} or not info.get('regularMarketPrice'):
            raise ValueError('Ticker was not found, has no quote, or trades in an unsupported currency.')
        return {'symbol': symbol.removesuffix('.JK'), 'provider_symbol': symbol,
            'name': info.get('longName') or info.get('shortName') or symbol,
            'asset_class': 'ETFs' if info.get('quoteType') == 'ETF' else 'Stocks',
            'sector': info.get('sector', 'Diversified' if info.get('quoteType') == 'ETF' else 'Unknown'),
            'country': info.get('country') or ('Indonesia' if symbol.endswith('.JK') else 'United States'),
            'currency': currency, 'exchange': 'IDX' if symbol.endswith('.JK') else info.get('exchange', 'US')}

    def get_quote(self, symbol):
        info = self._ticker(symbol).get_info()
        price = info.get('regularMarketPrice')
        timestamp = info.get('regularMarketTime')
        if not price or not timestamp or not math.isfinite(float(price)) or float(price) <= 0:
            raise ValueError('Provider returned no valid current quote.')
        return {'price': canonical(price), 'previous_close': canonical(info['regularMarketPreviousClose']) if info.get('regularMarketPreviousClose') else None,
            'currency': info.get('currency'), 'as_of': datetime.fromtimestamp(timestamp, timezone.utc).isoformat()}

    def get_history(self, symbol, start):
        frame = self._ticker(symbol).history(start=start, end=(today()+timedelta(days=1)).isoformat(),
            auto_adjust=False, actions=True, raise_errors=True, timeout=7)
        rows = []
        subsequent_splits = 1.0
        for stamp, row in reversed(list(frame.iterrows())):
            close = float(row['Close']) * subsequent_splits
            if math.isfinite(close) and close > 0:
                rows.append({'date': stamp.date().isoformat(), 'close': canonical(close),
                    'adj_close': canonical(row['Adj Close']) if 'Adj Close' in row and math.isfinite(row['Adj Close']) else None})
            # Yahoo Close is split-adjusted; restore actual historical prices for a ledger with explicit splits.
            split = float(row.get('Stock Splits', 0))
            if split > 0:
                subsequent_splits *= split
        return list(reversed(rows))

    def get_dividends(self, symbol, start):
        frame = self._ticker(symbol).history(start=start,auto_adjust=False,actions=True,raise_errors=True,timeout=7)
        payouts=[]
        subsequent_splits=1.0
        for stamp,row in reversed(list(frame.iterrows())):
            amount=float(row.get('Dividends',0))
            if amount>0 and math.isfinite(amount):
                payouts.append({'ex_date':stamp.date().isoformat(),'amount_per_share':canonical(amount*subsequent_splits),
                    'adjusted_amount_per_share':canonical(amount)})
            split=float(row.get('Stock Splits',0))
            if split>0:
                subsequent_splits*=split
        return list(reversed(payouts))

    def get_fx_rate(self, pair, start):
        return self.get_history('IDR=X' if pair == 'USDIDR' else pair, start)


def quote_status(quote, instrument, ttl=300, reference=None):
    if not quote:
        return {'stale': True, 'status': 'missing', 'delay': 'No cached quote', 'as_of': None}
    reference = reference or datetime.now(timezone.utc)
    delay = 'Manual valuation' if instrument.get('custom') else ('Delayed ~10 min' if instrument['exchange'] == 'IDX' else 'Provider delayed')
    if quote.get('origin') == 'demo':
        return {'stale': False, 'status': 'demo', 'delay': 'Sample quote', 'as_of': quote['as_of']}
    if instrument.get('custom'):
        return {'stale': False, 'status': 'manual', 'delay': delay, 'as_of': quote['as_of']}
    fetched = datetime.fromisoformat(quote['fetched_at'])
    stamp = datetime.fromisoformat(quote['as_of'])
    # shortcut: weekday sessions exclude exchange holidays, add an exchange calendar if holiday warnings become noisy.
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('Asia/Jakarta' if instrument['exchange'] == 'IDX' else 'America/New_York')
    except (KeyError, ImportError):
        zone = timezone(timedelta(hours=7 if instrument['exchange'] == 'IDX' else -5))
    local = reference.astimezone(zone)
    opening, closing = ((9, 0), (16, 0)) if instrument['exchange'] == 'IDX' else ((9, 30), (16, 0))
    open_time = local.replace(hour=opening[0], minute=opening[1], second=0, microsecond=0)
    close_time = local.replace(hour=closing[0], minute=closing[1], second=0, microsecond=0)
    market_open = local.weekday() < 5 and open_time <= local <= close_time
    if market_open:
        stale = (reference - stamp).total_seconds() > ttl + (900 if instrument['exchange'] == 'IDX' else 1200)
    else:
        expected = close_time
        if local < close_time or local.weekday() > 4:
            expected -= timedelta(days=1)
        while expected.weekday() > 4:
            expected -= timedelta(days=1)
        stale = stamp < expected - timedelta(minutes=30)
    return {'stale': stale, 'status': 'stale' if stale else ('delayed' if market_open else 'market closed'),
            'delay': delay, 'as_of': quote['as_of'], 'cache_age_seconds': max(0, int((reference-fetched).total_seconds()))}


class MarketService:
    def __init__(self, db, provider=None):
        self.db, self.provider = db, provider or YahooProvider(db.path.parent/'yahoo-cache')

    def _retry(self, function, *args):
        for attempt in range(2):
            try:
                return function(*args)
            except Exception:
                if attempt == 1:
                    raise
                time.sleep(.25)

    def lookup(self, symbol):
        symbol = valid_symbol(symbol.strip().upper())
        return self._retry(self.provider.get_metadata, symbol)

    def refresh(self, symbols=None, history=True):
        if self.db.settings().get('demo'):
            return {'updated': [], 'errors': [], 'message': 'Demo quotes are synthetic and are never refreshed from Yahoo.'}
        instruments = self.db.rows('SELECT * FROM instruments WHERE custom=0')
        if symbols is None:
            symbols = {t['symbol'] for t in self.db.transactions() if t['symbol']}
        else:
            symbols = set(symbols)
        known = {i['symbol'] for i in instruments}
        unknown = symbols-known
        if unknown:
            raise ValueError(f'Unknown or custom tickers: {", ".join(sorted(unknown))}. Add the instrument or enter a manual valuation.')
        selected = [i for i in instruments if i['symbol'] in symbols]
        updated, errors = [], []
        first = self.db.rows('SELECT MIN(date) start FROM transactions')[0]['start'] or (today()-timedelta(days=370)).isoformat()
        start = min(first, (today()-timedelta(days=6*366)).isoformat())
        def fetch(instrument):
            symbol = instrument['symbol']
            try:
                q = self._retry(self.provider.get_quote, instrument['provider_symbol'])
                if q.get('currency') != instrument['currency']:
                    raise ValueError('Provider currency disagrees with instrument metadata.')
                with self.db.connect() as conn:
                    conn.execute('INSERT OR REPLACE INTO quotes VALUES (?,?,?,?,?,?,?,?)',
                        (symbol,q['price'],q.get('previous_close'),q['currency'],q['as_of'],now(),1,'yahoo'))
            except Exception:
                return symbol, 'Quote refresh failed; retained cached data. Retry later or check the ticker mapping.'
            if history:
                try:
                    rows = self._retry(self.provider.get_history, instrument['provider_symbol'], start)
                    payouts = self._retry(self.provider.get_dividends, instrument['provider_symbol'], start)
                    with self.db.connect() as conn:
                        conn.executemany('INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)',
                            [(symbol,r['date'],r['close'],r.get('adj_close'),instrument['currency'],now()) for r in rows])
                        conn.executemany('INSERT OR REPLACE INTO dividends VALUES (?,?,?,?,?,?,?)',
                            [(symbol,r['ex_date'],None,r['amount_per_share'],instrument['currency'],'provider',r.get('adjusted_amount_per_share')) for r in payouts])
                except Exception:
                    return symbol, 'Quote updated; historical prices or dividends unavailable. Retained cached history.'
            return symbol, None
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = [pool.submit(fetch, i) for i in selected]
            fx_job = pool.submit(self._retry, self.provider.get_fx_rate, 'USDIDR', start)
            for job in as_completed(jobs):
                symbol, error = job.result()
                if error:
                    errors.append({'symbol': symbol, 'message': error})
                else:
                    updated.append(symbol)
            try:
                rates = fx_job.result()
                with self.db.connect() as conn:
                    conn.executemany('INSERT OR REPLACE INTO fx_rates VALUES (?,?,?,?)',
                        [('USDIDR',r['date'],r['close'],now()) for r in rates])
                if not rates:
                    errors.append({'symbol': 'USDIDR', 'message': 'No FX rate returned. Cached rates retained.'})
            except Exception:
                errors.append({'symbol':'USDIDR','message':'FX refresh failed; retained cached rate. Retry later.'})
        self.db.set_settings({'last_refresh':now(), 'refresh_errors':errors})
        return {'updated': updated, 'errors': errors, 'as_of': now()}

    def refresh_if_due(self, symbols=None):
        if self.db.settings().get('demo'):
            return
        ttl = self.db.settings()['quote_ttl']
        held = set(symbols) if symbols is not None else {t['symbol'] for t in self.db.transactions() if t['symbol']}
        instruments = {i['symbol']:i for i in self.db.rows('SELECT * FROM instruments')}
        quotes = {q['symbol']:q for q in self.db.rows('SELECT * FROM quotes')}
        stamp = datetime.now(timezone.utc)
        due = [s for s in held if s in instruments and not instruments[s]['custom'] and
            (s not in quotes or (stamp-datetime.fromisoformat(quotes[s]['fetched_at'])).total_seconds()>ttl)]
        last_attempt = self.db.settings().get('last_refresh')
        if due and (not last_attempt or (stamp-datetime.fromisoformat(last_attempt)).total_seconds() > min(ttl,60)):
            self.refresh(due, history=False)
