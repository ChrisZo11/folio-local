"""Read-only portfolio tools. Refreshing only updates the public market-data cache."""
import functools
import logging

from mcp.server.fastmcp import FastMCP

from .analytics import Analytics
from .db import Database, now
from .market import MarketService, quote_status


def create_server(db=None, market=None):
    db=db or Database()
    analytics=Analytics(db)
    market=market or MarketService(db)
    server=FastMCP('Folio Local',instructions='Local portfolio reporting. All math is calculated in code. State currency, cost basis, timestamps, and missing-data warnings. Never present estimates as confirmed income. These tools cannot execute trades or edit transactions.')

    def safe(fn):
        @functools.wraps(fn)
        def wrapper(*args,**kwargs):
            try:
                return fn(*args,**kwargs)
            except ValueError as exc:
                return {'error':str(exc),'suggested_fix':'Check parameters. Use list_portfolios and data_status to inspect available data.'}
            except Exception:
                logging.exception('Portfolio tool failed')
                return {'error':'Unable to complete the query.','suggested_fix':'Check the local database and retry. Cached data is retained.'}
        return wrapper

    @server.tool()
    @safe
    def list_portfolios() -> dict:
        """List local portfolio IDs, names, and base currencies. Omit portfolio in other calls to aggregate all portfolios."""
        return {'portfolios':db.rows('SELECT * FROM portfolios ORDER BY id'),'as_of':now()}

    @server.tool()
    @safe
    def get_holdings(portfolio:int|None=None,base_currency:str|None=None,show_sold:bool=False) -> dict:
        """Return share quantities, costs, current values, realized/unrealized P&L, weights and quote timestamps. portfolio is an ID; currency is IDR or USD. Holdings-only data cannot supply full performance."""
        market.refresh_if_due()
        return analytics.holdings(portfolio,base_currency,show_sold)

    @server.tool()
    @safe
    def get_transactions(portfolio:int|None=None,symbol:str|None=None,type:str|None=None,start:str|None=None,end:str|None=None,limit:int=100) -> dict:
        """Read transactions, newest first, optionally filtered by ticker, operation, portfolio ID, and inclusive YYYY-MM-DD dates. limit is 1–10000. Cannot create or change records."""
        return analytics.transactions(portfolio,symbol=symbol,kind=type,start=start,end=end,limit=limit)

    @server.tool()
    @safe
    def get_quote(symbols:list[str]) -> dict:
        """Read current cached quotes for registered tickers, refreshing expired cache when possible. Includes source timestamp, delay and stale status. IDX prices are delayed ~10 min, never real-time."""
        if len(symbols)>100:
            raise ValueError('Query at most 100 symbols per call.')
        instruments={i['symbol']:i for i in db.rows('SELECT * FROM instruments')}
        if any(s not in instruments for s in symbols):
            raise ValueError('Ticker not registered. Add it using the local web app first.')
        market.refresh_if_due(symbols)
        quotes={q['symbol']:q for q in db.rows('SELECT * FROM quotes')}
        return {'quotes':[{'symbol':s,'currency':instruments[s]['currency'],
            'price':float(quotes[s]['price']) if s in quotes else None,
            **quote_status(quotes.get(s),instruments[s],db.settings()['quote_ttl'])} for s in symbols],
            'as_of':now(),'units':'price per share','assumptions':['Quotes are provider-delayed; missing prices are null.']}

    @server.tool()
    @safe
    def get_portfolio_value(portfolio:int|None=None,base_currency:str|None=None,detail:bool=False) -> dict:
        """Value securities plus tracked cash in IDR (default) or USD, with profit, day change, FX and warnings. Set detail=true for per-holding values. Missing prices make totals incomplete."""
        market.refresh_if_due()
        result=analytics.summary(portfolio,base_currency)
        if not detail:
            result.pop('holdings')
        return result

    @server.tool()
    @safe
    def get_performance(portfolio:int|None=None,period:str='ALL',base_currency:str|None=None,detail:bool=False) -> dict:
        """Annualized XIRR and daily TWR for 1M, 3M, YTD, 1Y, 3Y, ALL. Corrects external cash flows at end of day. Missing historical prices/FX or holdings-only data return explicit unavailable reasons. detail adds daily series. Contributors attribute security gains over the selected period, excluding unassigned cash expenses."""
        result=analytics.performance(portfolio,base_currency,period)
        if not detail:
            result.pop('series')
        return result

    @server.tool()
    @safe
    def get_allocation(portfolio:int|None=None,group_by:str='asset_class',base_currency:str|None=None) -> dict:
        """Break down securities by asset_class, sector, currency, country or symbol. Weights exclude tracked cash and unpriced holdings; incomplete inputs are flagged."""
        return analytics.allocation(portfolio,base_currency,group_by)

    @server.tool()
    @safe
    def get_concentration(portfolio:int|None=None,top_n:int=5,base_currency:str|None=None) -> dict:
        """Return top_n position weights (1–100) and HHI on a 0–1 scale. Securities only. Does not recommend rebalancing."""
        return analytics.concentration(portfolio,base_currency,top_n)

    @server.tool()
    @safe
    def get_dividends(portfolio:int|None=None,period:str='ALL',group_by:str='symbol',base_currency:str|None=None) -> dict:
        """Confirmed gross/net broker income, trailing income, unconfirmed proposals and forward estimates. group_by is symbol or month. Periods: 1M,3M,YTD,1Y,3Y,ALL. Proposals/forecasts are never actual income; discrepancies retain both share counts."""
        return analytics.dividends(portfolio,base_currency,period,group_by)

    @server.tool()
    @safe
    def refresh_prices(symbols:list[str]|None=None) -> dict:
        """Refresh public quotes, historical prices, dividends and USD/IDR in the local cache. Defaults to portfolio tickers. Does not change trades or confirmed income. Network failures retain cached values with warnings."""
        return market.refresh(symbols)

    @server.tool()
    @safe
    def data_status() -> dict:
        """Return database location, demo status, last import/refresh, record counts and missing-data warnings. Read-only; no portfolio changes."""
        return analytics.status()

    return server
