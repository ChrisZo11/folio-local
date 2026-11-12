import logging
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .analytics import Analytics
from .db import Database, ROOT, now, today
from .importer import preview, commit, save_manual, delete_transaction, undo_import, SCHEMA
from .ledger import canonical, dec, valid_symbol
from .market import MarketService

logger=logging.getLogger(__name__)


class TransactionInput(BaseModel):
    portfolio_id: int
    date: str
    type: str
    symbol: str | None = None
    quantity: str | float = '0'
    quantity_unit: str = 'share'
    price: str | float = '0'
    fee: str | float = '0'
    tax: str | float = '0'
    currency: str = 'IDR'
    fx_rate: str | float = ''
    note: str = Field(default='',max_length=2000)
    affects_cash: bool = False
    fee_estimated: bool = False
    confirmed: bool = True


class ImportInput(BaseModel):
    text: str = Field(max_length=2_000_000)
    file_name: str = Field(default='import.csv',max_length=255)
    source: Literal['generic','stockbit','gotrade','snowball'] = 'generic'
    quantity_unit: str = ''
    default_fee_rate: str = '0'
    confirmed: bool = False


class SettingsInput(BaseModel):
    base_currency: Literal['IDR','USD']
    cost_basis: Literal['fifo','average']
    quote_ttl: int = Field(default=300,ge=60,le=86400)


class InstrumentInput(BaseModel):
    symbol: str
    name: str = Field(min_length=1,max_length=200)
    currency: Literal['IDR','USD']
    asset_class: str = Field(default='Custom',min_length=1,max_length=60)
    sector: str = Field(default='Other',max_length=100)
    country: str = Field(default='Indonesia',max_length=100)
    exchange: str = Field(default='Manual',max_length=30)
    provider_symbol: str = ''
    custom: bool = True


class ValuationInput(BaseModel):
    price: str
    date: str


class PortfolioInput(BaseModel):
    name: str = Field(min_length=1,max_length=100)
    base_currency: Literal['IDR','USD'] = 'IDR'


class RefreshInput(BaseModel):
    symbols: list[str] | None = Field(default=None,max_length=100)
    history: bool = True


def create_app(db=None, market=None):
    db=db or Database()
    engine=Analytics(db)
    market=market or MarketService(db)
    app=FastAPI(title='Folio Local',version='0.1.0',description='Private portfolio API. All portfolio data remains in local SQLite.')
    app.state.db=db
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
    app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5173','http://localhost:5173'],
        allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type'])

    @app.middleware('http')
    async def local_requests(request: Request, call_next):
        origin=request.headers.get('origin')
        allowed={f'http://{host}:{port}' for host in ('127.0.0.1','localhost') for port in ('8000','5173')}
        if request.method not in {'GET','HEAD','OPTIONS'}:
            if origin and origin not in allowed:
                return JSONResponse({'error':'Requests from external websites are not allowed.'},status_code=403)
            if request.headers.get('content-type','').split(';')[0] != 'application/json':
                return JSONResponse({'error':'Use application/json for local API changes.'},status_code=415)
        try:
            length=int(request.headers.get('content-length','0') or 0)
        except ValueError:
            return JSONResponse({'error':'Invalid Content-Length.'},status_code=400)
        if length>2_100_000:
            return JSONResponse({'error':'Request exceeds the 2 MB limit.'},status_code=413)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        if request.url.path.startswith('/api'):
            response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        return JSONResponse({'error':str(exc),'suggested_fix':'Check the highlighted fields or preview rows and try again.'},status_code=400)

    @app.exception_handler(Exception)
    async def unexpected(request,exc):
        logger.exception('Local request failed')
        return JSONResponse({'error':'The operation failed; your database transaction was rolled back.',
            'suggested_fix':'Retry, or check the local server log for details.'},status_code=500)

    @app.get('/api/health')
    def health():
        return {'status':'ok','local_only':True,'demo':db.settings().get('demo',False)}

    @app.get('/api/portfolios')
    def portfolios():
        return db.rows('SELECT * FROM portfolios ORDER BY id')

    @app.post('/api/portfolios',status_code=201)
    def add_portfolio(data:PortfolioInput):
        name=data.name.strip()
        if not name:
            raise ValueError('Portfolio name cannot be blank.')
        if db.rows('SELECT id FROM portfolios WHERE lower(name)=lower(?)',(name,)):
            raise ValueError('A portfolio already has that name.')
        with db.connect() as conn:
            pid=conn.execute('INSERT INTO portfolios(name,base_currency) VALUES (?,?)',(name,data.base_currency)).lastrowid
        return {'id':pid,'name':name,'base_currency':data.base_currency}

    @app.get('/api/summary')
    def summary(portfolio:int|None=None,base_currency:str|None=None):
        market.refresh_if_due()
        return engine.summary(portfolio,base_currency)

    @app.get('/api/holdings')
    def holdings(portfolio:int|None=None,base_currency:str|None=None,show_sold:bool=False):
        market.refresh_if_due()
        return engine.holdings(portfolio,base_currency,show_sold)

    @app.get('/api/allocation')
    def allocation(portfolio:int|None=None,base_currency:str|None=None,group_by:str='asset_class'):
        return engine.allocation(portfolio,base_currency,group_by)

    @app.get('/api/concentration')
    def concentration(portfolio:int|None=None,base_currency:str|None=None,top_n:int=5):
        return engine.concentration(portfolio,base_currency,top_n)

    @app.get('/api/performance')
    def performance(portfolio:int|None=None,base_currency:str|None=None,period:str='ALL'):
        return engine.performance(portfolio,base_currency,period)

    @app.get('/api/dividends')
    def dividends(portfolio:int|None=None,base_currency:str|None=None,period:str='ALL',group_by:str='symbol'):
        return engine.dividends(portfolio,base_currency,period,group_by)

    @app.get('/api/transactions')
    def transactions(portfolio:int|None=None,base_currency:str|None=None,symbol:str|None=None,type:str|None=None,
                     start:str|None=None,end:str|None=None,limit:int=1000):
        return engine.transactions(portfolio,base_currency,symbol,type,start,end,limit)

    @app.post('/api/transactions',status_code=201)
    def add_transaction(data:TransactionInput):
        return {'id':save_manual(db,data.model_dump())}

    @app.put('/api/transactions/{tx_id}')
    def edit_transaction(tx_id:int,data:TransactionInput):
        return {'id':save_manual(db,data.model_dump(),tx_id)}

    @app.delete('/api/transactions/{tx_id}')
    def remove_transaction(tx_id:int):
        delete_transaction(db,tx_id)
        return {'deleted':tx_id}

    @app.get('/api/instruments')
    def instruments():
        return db.rows('SELECT * FROM instruments ORDER BY symbol')

    @app.get('/api/instruments/lookup')
    def lookup(symbol:str):
        try:
            return market.lookup(symbol)
        except ValueError:
            raise
        except Exception:
            raise ValueError('Ticker lookup is unavailable. Check the symbol or add a custom holding to work offline.') from None

    @app.post('/api/instruments',status_code=201)
    def add_instrument(data:InstrumentInput):
        row=data.model_dump()
        row['symbol']=valid_symbol(row['symbol'].strip().upper())
        if db.rows('SELECT symbol FROM instruments WHERE symbol=?',(row['symbol'],)):
            raise ValueError('This ticker already exists. Edit its mapping in Settings.')
        row['provider_symbol']=valid_symbol((row['provider_symbol'] or row['symbol']).strip().upper())
        with db.connect() as conn:
            conn.execute('INSERT INTO instruments VALUES (?,?,?,?,?,?,?,?,?)',
                tuple(row[k] for k in ('symbol','provider_symbol','name','asset_class','sector','country','currency','exchange','custom')))
        return row

    @app.put('/api/instruments/{symbol}/mapping')
    def update_mapping(symbol:str,data:dict[str,str]):
        mapped=valid_symbol(data.get('provider_symbol','').upper().strip())
        with db.connect() as conn:
            if conn.execute('UPDATE instruments SET provider_symbol=? WHERE symbol=? AND custom=0',(mapped,symbol)).rowcount==0:
                raise ValueError('Market-backed instrument not found.')
            conn.execute('DELETE FROM quotes WHERE symbol=?',(symbol,))
            conn.execute('DELETE FROM prices WHERE symbol=?',(symbol,))
            conn.execute('DELETE FROM dividends WHERE symbol=?',(symbol,))
        return {'symbol':symbol,'provider_symbol':mapped,'message':'Old provider cache cleared. Refresh prices to fetch the new mapping.'}

    @app.post('/api/instruments/{symbol}/valuation')
    def valuation(symbol:str,data:ValuationInput):
        from datetime import date
        rows=db.rows('SELECT * FROM instruments WHERE symbol=? AND custom=1',(symbol,))
        if not rows:
            raise ValueError('Manual valuations are only available for custom holdings.')
        price=dec(data.price,'valuation')
        day=date.fromisoformat(data.date)
        if price<=0 or day>today():
            raise ValueError('Enter a positive value and a date no later than today.')
        with db.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)',(symbol,day.isoformat(),canonical(price),None,rows[0]['currency'],now()))
            latest=conn.execute('SELECT date,close FROM prices WHERE symbol=? ORDER BY date DESC LIMIT 1',(symbol,)).fetchone()
            conn.execute('INSERT OR REPLACE INTO quotes VALUES (?,?,?,?,?,?,?,?)',
                (symbol,latest['close'],None,rows[0]['currency'],latest['date']+'T00:00:00+07:00',now(),0,'manual'))
        return {'symbol':symbol,'price':float(price),'as_of':day.isoformat()}

    @app.post('/api/prices/refresh')
    def refresh(data:RefreshInput):
        return market.refresh(data.symbols,data.history)

    @app.get('/api/settings')
    def settings():
        return db.settings()

    @app.put('/api/settings')
    def set_settings(data:SettingsInput):
        db.set_settings(data.model_dump())
        return {**db.settings(),'message':'Cost basis recalculated from unchanged transactions.'}

    @app.get('/api/status')
    def status():
        return engine.status()

    @app.post('/api/imports/preview')
    def import_preview(data:ImportInput):
        return preview(db,data.text,data.source,data.quantity_unit,data.default_fee_rate)

    @app.post('/api/imports/commit')
    def import_commit(data:ImportInput):
        if not data.confirmed:
            raise ValueError('Review the import preview and confirm before writing any rows.')
        return commit(db,data.text,data.file_name,data.source,data.quantity_unit,data.default_fee_rate)

    @app.get('/api/imports')
    def imports():
        return db.rows('SELECT * FROM imports ORDER BY id DESC')

    @app.post('/api/imports/{import_id}/undo')
    def import_undo(import_id:int):
        undo_import(db,import_id)
        return {'undone':import_id}

    @app.get('/api/imports/template')
    def template():
        return Response(SCHEMA+'\n2026-01-15,Stockbit,buy,BBCA,1,lot,9000,1350,0,IDR,1,Example only — replace before importing\n',
            media_type='text/csv',headers={'Content-Disposition':'attachment; filename="folio-import-template.csv"'})

    @app.post('/api/backup')
    def backup():
        path=db.path.parent/'backups'/f'{db.path.stem}-{now().replace(":","-")}.db'
        return {'path':db.backup(path),'message':'Consistent SQLite backup created locally.'}

    dist=ROOT/'web'/'dist'
    if dist.exists():
        app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
        @app.get('/{path:path}')
        def frontend(path:str):
            if path.startswith('api/'):
                raise HTTPException(404,'API route not found.')
            if path=='favicon.svg':
                return FileResponse(dist/'favicon.svg')
            return FileResponse(dist/'index.html')
    else:
        @app.get('/')
        def frontend_not_built():
            return {'message':'Build the React app: cd web; npm install; npm run build. Or use npm run dev.','api_docs':'/docs'}
    return app
