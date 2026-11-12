import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now().astimezone().isoformat()


def today():
    try:
        return datetime.now(ZoneInfo('Asia/Jakarta')).date()
    except ZoneInfoNotFoundError:
        from datetime import timedelta, timezone
        return datetime.now(timezone(timedelta(hours=7))).date()


SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolios (
 id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, base_currency TEXT NOT NULL DEFAULT 'IDR');
CREATE TABLE IF NOT EXISTS instruments (
 symbol TEXT PRIMARY KEY, provider_symbol TEXT NOT NULL, name TEXT NOT NULL,
 asset_class TEXT NOT NULL, sector TEXT NOT NULL, country TEXT NOT NULL,
 currency TEXT NOT NULL, exchange TEXT NOT NULL, custom INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS imports (
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, file_name TEXT NOT NULL,
 imported_at TEXT NOT NULL, added INTEGER NOT NULL, skipped INTEGER NOT NULL,
 failed INTEGER NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS transactions (
 id INTEGER PRIMARY KEY, portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
 date TEXT NOT NULL, type TEXT NOT NULL, symbol TEXT REFERENCES instruments(symbol),
 quantity TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL, tax TEXT NOT NULL,
 currency TEXT NOT NULL, fx_rate TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL, source_hash TEXT UNIQUE NOT NULL,
 fee_estimated INTEGER NOT NULL DEFAULT 0, affects_cash INTEGER NOT NULL DEFAULT 0,
 confirmed INTEGER NOT NULL DEFAULT 1, import_id INTEGER REFERENCES imports(id));
CREATE INDEX IF NOT EXISTS transaction_order ON transactions(portfolio_id, date, id);
CREATE TABLE IF NOT EXISTS quotes (
 symbol TEXT PRIMARY KEY REFERENCES instruments(symbol), price TEXT NOT NULL,
 previous_close TEXT, currency TEXT NOT NULL, as_of TEXT NOT NULL,
 fetched_at TEXT NOT NULL, delayed_flag INTEGER NOT NULL DEFAULT 1, origin TEXT NOT NULL DEFAULT 'yahoo');
CREATE TABLE IF NOT EXISTS prices (
 symbol TEXT NOT NULL, date TEXT NOT NULL, close TEXT NOT NULL, adj_close TEXT,
 currency TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(symbol,date));
CREATE TABLE IF NOT EXISTS dividends (
 symbol TEXT NOT NULL, ex_date TEXT NOT NULL, pay_date TEXT,
 amount_per_share TEXT NOT NULL, currency TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'provider',
 adjusted_amount_per_share TEXT,
 PRIMARY KEY(symbol,ex_date));
CREATE TABLE IF NOT EXISTS fx_rates (
 pair TEXT NOT NULL, date TEXT NOT NULL, rate TEXT NOT NULL,
 fetched_at TEXT NOT NULL, PRIMARY KEY(pair,date));
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

INSTRUMENTS = [
 ('BBCA','BBCA.JK','Bank Central Asia','Stocks','Financials','Indonesia','IDR','IDX'),
 ('BBRI','BBRI.JK','Bank Rakyat Indonesia','Stocks','Financials','Indonesia','IDR','IDX'),
 ('BMRI','BMRI.JK','Bank Mandiri','Stocks','Financials','Indonesia','IDR','IDX'),
 ('SIDO','SIDO.JK','Sido Muncul','Stocks','Healthcare','Indonesia','IDR','IDX'),
 ('SMSM','SMSM.JK','Selamat Sempurna','Stocks','Industrials','Indonesia','IDR','IDX'),
 ('SCHD','SCHD','Schwab US Dividend Equity ETF','ETFs','Diversified','United States','USD','NYSE'),
 ('VOO','VOO','Vanguard S&P 500 ETF','ETFs','Diversified','United States','USD','NYSE'),
 ('VTI','VTI','Vanguard Total Stock Market ETF','ETFs','Diversified','United States','USD','NYSE'),
 ('QQQ','QQQ','Invesco QQQ Trust','ETFs','Technology','United States','USD','NASDAQ'),
]


class Database:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('FOLIO_DB', ROOT / 'data' / 'portfolio.db')).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            if 'adjusted_amount_per_share' not in {r['name'] for r in conn.execute('PRAGMA table_info(dividends)')}:
                conn.execute('ALTER TABLE dividends ADD COLUMN adjusted_amount_per_share TEXT')
            conn.executemany('INSERT OR IGNORE INTO instruments VALUES (?,?,?,?,?,?,?,?,0)', INSTRUMENTS)
            conn.execute("INSERT OR IGNORE INTO portfolios(name) VALUES ('Stockbit')")
            conn.execute("INSERT OR IGNORE INTO portfolios(name) VALUES ('GoTrade')")
            for key, value in {'base_currency': 'IDR', 'cost_basis': 'fifo', 'quote_ttl': 300, 'demo': False}.items():
                conn.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA journal_mode=WAL')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def rows(self, sql, params=()):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def settings(self):
        return {r['key']: json.loads(r['value']) for r in self.rows('SELECT * FROM settings')}

    def set_settings(self, values):
        with self.connect() as conn:
            conn.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)', [(k, json.dumps(v)) for k, v in values.items()])

    def transactions(self, portfolio=None):
        query = 'SELECT t.*, p.name portfolio FROM transactions t JOIN portfolios p ON p.id=t.portfolio_id'
        args = ()
        if portfolio is not None:
            query += ' WHERE t.portfolio_id=?'
            args = (portfolio,)
        return self.rows(query + ' ORDER BY t.date,t.id', args)

    def backup(self, target):
        target = Path(target).resolve()
        if target == self.path:
            raise ValueError('Choose a backup path different from the active database.')
        if target.exists():
            raise ValueError('Backup already exists. Choose a new filename to keep previous backups.')
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        return str(target)
