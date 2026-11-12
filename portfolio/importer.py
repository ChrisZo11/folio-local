import csv
import hashlib
import io
import json
from collections import Counter
from uuid import uuid4

from .db import now, today
from .ledger import build_ledger, canonical, dec, validate_transaction

FIELDS = ('portfolio_id','date','type','symbol','quantity','price','fee','tax','currency','fx_rate','note','source','source_hash','fee_estimated','affects_cash','confirmed','import_id')
SCHEMA = 'date,portfolio,type,symbol,quantity,quantity_unit,price,fee,tax,currency,fx_rate,note'
# Broker layouts vary. Only these documented column aliases are accepted; PDF/image rows use generic CSV after review.
ALIASES = {
 'generic': {'ticker':'symbol','average_cost':'price'},
 'stockbit': {'ticker': 'symbol', 'stock': 'symbol', 'transaction_date': 'date', 'action': 'type', 'shares': 'quantity', 'unit': 'quantity_unit'},
 'gotrade': {'ticker': 'symbol', 'transaction_date': 'date', 'action': 'type', 'shares': 'quantity', 'unit': 'quantity_unit', 'commission': 'fee'},
 'snowball': {'ticker': 'symbol', 'operation': 'type', 'account': 'portfolio', 'commission': 'fee', 'unit': 'quantity_unit'},
}


def catalogs(db):
    return ({r['symbol']: r for r in db.rows('SELECT * FROM instruments')},
            {r['id']: r for r in db.rows('SELECT * FROM portfolios')})


def insert(conn, row):
    values = [row.get(f) for f in FIELDS]
    return conn.execute(f'INSERT INTO transactions ({",".join(FIELDS)}) VALUES ({",".join("?" for _ in FIELDS)})', values).lastrowid


def preview(db, text, source='generic', quantity_unit='', default_fee_rate='0'):
    if source not in ALIASES:
        raise ValueError('Unknown import source. Choose generic, stockbit, gotrade, or snowball.')
    if len(text.encode('utf-8')) > 2_000_000:
        raise ValueError('CSV exceeds the 2 MB limit. Split it into smaller files.')
    rate = dec(default_fee_rate, 'default fee rate')
    if rate < 0 or rate > 1:
        raise ValueError('Default fee rate must be between 0 and 1 (0.0015 means 0.15%).')
    instruments, portfolios = catalogs(db)
    by_name = {p['name'].lower(): p['id'] for p in portfolios.values()}
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')), strict=True)
    if not reader.fieldnames:
        raise ValueError('CSV is empty. Use the downloadable template.')
    headers = [ALIASES[source].get(k.strip().lower().replace(' ', '_'), k.strip().lower().replace(' ', '_')) for k in reader.fieldnames]
    if len(headers)!=len(set(headers)):
        raise ValueError('Duplicate CSV columns map to the same field. Keep one column per canonical field.')
    existing = {r['source_hash']:r for r in db.transactions()}
    ledger = db.transactions()
    result, counts = [], Counter()
    try:
        for line, raw in enumerate(reader, 2):
            item = {'line': line, 'status': 'error', 'row': None, 'error': None}
            try:
                if None in raw:
                    raise ValueError('Too many columns; quote notes containing commas.')
                data = {ALIASES[source].get(k.strip().lower().replace(' ', '_'), k.strip().lower().replace(' ', '_')): (v or '').strip() for k,v in raw.items()}
                data['portfolio_id'] = by_name.get(data.get('portfolio', '').lower(), 0)
                data['quantity_unit'] = data.get('quantity_unit') or quantity_unit
                if not data.get('type') and any(k.strip().lower().replace(' ','_')=='average_cost' for k in raw):
                    data['type']='opening'
                data['type'] = {'purchase': 'buy', 'sale': 'sell', 'income': 'dividend'}.get(data.get('type','').lower(), data.get('type',''))
                if not data.get('fee') and rate and data.get('type') in {'buy','sell'}:
                    shares = dec(data.get('quantity', 0)) * (100 if data['quantity_unit'] == 'lot' else 1)
                    data['fee'] = canonical(shares * dec(data.get('price', 0)) * rate)
                    data['fee_estimated'] = True
                row = validate_transaction(data, instruments, portfolios, today())
                base = [row[k] for k in ('date','portfolio_id','symbol','type','quantity','price')]
                key = json.dumps(base, separators=(',', ':'))
                counts[key] += 1
                row.update(source=source, import_id=None,
                    source_hash=hashlib.sha256(f'{key}:{counts[key]}'.encode()).hexdigest())
                item['row'] = row
                if row['source_hash'] in existing:
                    old = existing[row['source_hash']]
                    if any(row[k]!=old[k] for k in ('fee','tax','fx_rate','currency')):
                        raise ValueError('A matching trade has different fees, tax, currency, or FX. Review both records; the saved trade is unchanged.')
                    item['status'] = 'skipped'
                else:
                    item['status'] = 'ready'
            except (ValueError, TypeError) as exc:
                item['error'] = str(exc)
            result.append(item)
    except csv.Error as exc:
        raise ValueError(f'Malformed CSV near line {reader.line_num}: {exc}') from None
    method=db.settings()['cost_basis']
    # Broker exports often list newest first; validate and persist in the same chronological order.
    for item in sorted((i for i in result if i['status']=='ready'),key=lambda i:(i['row']['date'],i['line'])):
        candidate={**item['row'],'id':10**12+item['line']}
        try:
            build_ledger(ledger+[candidate],method)
            ledger.append(candidate)
        except ValueError as exc:
            item['status']='error'
            item['error']=str(exc)
    return {'rows': result, 'added': sum(x['status']=='ready' for x in result),
            'skipped': sum(x['status']=='skipped' for x in result), 'failed': sum(x['status']=='error' for x in result)}


def commit(db, text, file_name='import.csv', source='generic', quantity_unit='', default_fee_rate='0'):
    # Revalidate inside the write transaction so a stale preview cannot create an invalid ledger.
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        result = preview(db, text, source, quantity_unit, default_fee_rate)
        if not result['added'] and result['failed']:
            raise ValueError('No valid rows to import. Correct the errors shown in the preview.')
        import_id = conn.execute('INSERT INTO imports(source,file_name,imported_at,added,skipped,failed) VALUES (?,?,?,?,?,?)',
            (source, file_name[:255], now(), result['added'], result['skipped'], result['failed'])).lastrowid
        for item in sorted(result['rows'],key=lambda i:(i['row']['date'] if i['row'] else '',i['line'])):
            if item['status'] == 'ready':
                insert(conn, {**item['row'], 'import_id': import_id})
        return {**result, 'import_id': import_id}


def save_manual(db, data, tx_id=None):
    instruments, portfolios = catalogs(db)
    row = validate_transaction(data, instruments, portfolios, today())
    row.update(source='manual', source_hash=f'manual:{uuid4()}', import_id=None)
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = db.transactions()
        if tx_id is not None:
            old = next((t for t in existing if t['id'] == tx_id), None)
            if not old:
                raise ValueError('Transaction no longer exists. Refresh the page.')
            row['source_hash'] = old['source_hash']
            conn.execute('DELETE FROM transactions WHERE id=?', (tx_id,))
        build_ledger([t for t in existing if t['id'] != tx_id] + [{**row, 'id': tx_id or 10**12}], db.settings()['cost_basis'])
        if tx_id is None:
            return insert(conn, row)
        return conn.execute(f'INSERT INTO transactions (id,{",".join(FIELDS)}) VALUES ({",".join("?" for _ in range(len(FIELDS)+1))})',
            [tx_id] + [row.get(f) for f in FIELDS]).lastrowid


def delete_transaction(db, tx_id):
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM transactions WHERE id=?', (tx_id,)).fetchone():
            raise ValueError('Transaction not found.')
        build_ledger([t for t in db.transactions() if t['id'] != tx_id], db.settings()['cost_basis'])
        conn.execute('DELETE FROM transactions WHERE id=?', (tx_id,))


def undo_import(db, import_id):
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        batch = conn.execute('SELECT * FROM imports WHERE id=? AND undone=0', (import_id,)).fetchone()
        if not batch:
            raise ValueError('Import not found or already undone.')
        build_ledger([t for t in db.transactions() if t['import_id'] != import_id], db.settings()['cost_basis'])
        conn.execute('DELETE FROM transactions WHERE import_id=?', (import_id,))
        conn.execute('UPDATE imports SET undone=1 WHERE id=?', (import_id,))
