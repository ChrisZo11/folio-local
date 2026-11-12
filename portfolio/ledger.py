"""Decimal arithmetic and lot accounting, independent of providers and transports."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import math
import re

ZERO = Decimal(0)
ONE = Decimal(1)
EPS = Decimal('0.000000000001')
TYPES = {'buy', 'sell', 'dividend', 'split', 'fee', 'deposit', 'withdrawal', 'opening'}


def dec(value, name='number'):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{name} must be a decimal number.') from None
    if not result.is_finite() or abs(result) > Decimal('1e18'):
        raise ValueError(f'{name} must be finite and at most 10^18.')
    return result


def number(value):
    return float(value) if value is not None else None


def canonical(value):
    return format(dec(value).normalize(), 'f')


def validate_transaction(data, instruments, portfolios, current_date):
    row = dict(data)
    try:
        day = date.fromisoformat(str(row.get('date', '')))
    except ValueError:
        raise ValueError('Date is required in YYYY-MM-DD format.') from None
    if day > current_date:
        raise ValueError('Future transactions cannot be booked. Enter the actual trade date.')
    row['date'] = day.isoformat()
    row['type'] = str(row.get('type', '')).strip().lower()
    if row['type'] not in TYPES:
        raise ValueError(f"Unknown operation. Use {', '.join(sorted(TYPES))}.")
    try:
        row['portfolio_id'] = int(row.get('portfolio_id', 0))
    except (ValueError, TypeError):
        raise ValueError('Select an existing portfolio.') from None
    if row['portfolio_id'] not in portfolios:
        raise ValueError('Select an existing portfolio.')
    symbol = str(row.get('symbol') or '').strip().upper()
    row['symbol'] = symbol or None
    security = row['type'] in {'buy', 'sell', 'dividend', 'split', 'opening'}
    if security and symbol not in instruments:
        raise ValueError(f'Unknown ticker {symbol or "(empty)"}. Add or look up the holding before importing.')
    row['currency'] = str(row.get('currency', '')).strip().upper()
    if row['currency'] not in {'IDR', 'USD'}:
        raise ValueError('Currency must be IDR or USD.')
    if symbol and symbol not in instruments:
        raise ValueError('Unknown ticker. Add the instrument first.')
    if symbol and row['currency'] != instruments[symbol]['currency']:
        raise ValueError(f"{symbol} trades in {instruments[symbol]['currency']}; fix the currency.")
    for field in ('quantity', 'price', 'fee', 'tax', 'fx_rate'):
        raw = row.get(field)
        value = dec(raw if raw not in ('', None) else ('1' if field == 'fx_rate' and (row['currency'] == 'IDR' or row['type']=='split') else '0'), field)
        if value < 0:
            raise ValueError(f'{field} cannot be negative.')
        row[field] = canonical(value)
    if dec(row['fx_rate']) <= 0:
        raise ValueError('USD transactions require the actual trade-date USD/IDR rate; it is never guessed.')
    if row['currency'] == 'IDR' and dec(row['fx_rate']) != 1:
        raise ValueError('IDR transactions use fx_rate=1 (all trade FX rates are IDR per currency unit).')
    if row['type'] in {'buy', 'sell', 'opening', 'split'} and dec(row['quantity']) <= 0:
        raise ValueError('Quantity must be greater than zero. Splits use the new/old share ratio.')
    if row['type'] in {'buy', 'sell', 'opening', 'dividend', 'deposit', 'withdrawal', 'fee'} and dec(row['price']) <= 0:
        raise ValueError('Price / gross cash amount must be greater than zero.')
    unit = str(row.get('quantity_unit', '')).lower().strip()
    if row['type'] in {'buy', 'sell', 'opening'}:
        if unit not in {'share', 'lot'}:
            raise ValueError('Specify quantity_unit as share or lot; ambiguous units are never guessed.')
        if unit == 'lot':
            if instruments[symbol]['exchange'] != 'IDX':
                raise ValueError('Lot conversion (100 shares) is only supported for IDX instruments.')
            row['quantity'] = canonical(dec(row['quantity']) * 100)
    if row['type'] == 'dividend' and dec(row['tax']) + dec(row['fee']) > dec(row['price']):
        raise ValueError('Dividend tax and fees cannot exceed the gross amount.')
    for field in ('affects_cash', 'fee_estimated', 'confirmed'):
        value = row.get(field, field == 'confirmed')
        if isinstance(value, str):
            if value.lower() not in {'1', '0', 'true', 'false', 'yes', 'no', ''}:
                raise ValueError(f'{field} must be true or false.')
            value = value.lower() in {'1', 'true', 'yes'}
        row[field] = int(bool(value))
    row['note'] = str(row.get('note') or '')[:2000]
    row['quantity_unit'] = 'share'
    if not row['confirmed']:
        raise ValueError('Book only confirmed transactions. Dividend proposals remain separate estimates.')
    return row


def build_ledger(transactions, method='fifo'):
    if method not in {'fifo', 'average'}:
        raise ValueError('Cost basis must be fifo or average.')
    positions = {}
    cash = defaultdict(lambda: ZERO)
    realized_by_tx = {}
    for tx in sorted(transactions, key=lambda t: (t['date'], t.get('id', 0))):
        symbol = tx.get('symbol')
        kind = tx['type']
        q, price, fee, tax, fx = (dec(tx[k]) for k in ('quantity', 'price', 'fee', 'tax', 'fx_rate'))
        key = (tx['portfolio_id'], symbol)
        p = positions.setdefault(key, {'quantity': ZERO, 'cost': ZERO, 'base_cost': ZERO,
            'realized': ZERO, 'realized_base': ZERO, 'dividends': ZERO, 'dividends_base': ZERO,
            'lots': [], 'history_complete': True, 'symbol': symbol, 'portfolio_id': tx['portfolio_id']}) if symbol else None
        ck = (tx['portfolio_id'], tx['currency'])
        if kind in {'buy', 'opening'}:
            cost = q * price + fee + tax
            p['lots'].append([q, cost, cost * fx])
            if method == 'average' and len(p['lots']) > 1:
                p['lots'] = [[sum(l[i] for l in p['lots']) for i in range(3)]]
            p['history_complete'] &= kind != 'opening'
            if tx.get('affects_cash'):
                cash[ck] -= cost
        elif kind == 'sell':
            held = sum(l[0] for l in p['lots'])
            if q > held + EPS:
                raise ValueError(f"{symbol}: sale on {tx['date']} exceeds shares held ({held}). Fix the missing buy or opening balance.")
            remaining, removed, removed_base = q, ZERO, ZERO
            while remaining > EPS and p['lots']:
                lot = p['lots'][0]
                take = min(remaining, lot[0])
                ratio = take / lot[0]
                removed += lot[1] * ratio
                removed_base += lot[2] * ratio
                p['lots'][0] = [lot[0] - take, lot[1] * (ONE - ratio), lot[2] * (ONE - ratio)]
                remaining -= take
                if p['lots'][0][0] <= EPS:
                    p['lots'].pop(0)
            proceeds = q * price - fee - tax
            realized = proceeds * fx - removed_base
            p['realized'] += proceeds - removed
            p['realized_base'] += realized
            realized_by_tx[tx.get('id', 0)] = realized
            if tx.get('affects_cash'):
                cash[ck] += proceeds
        elif kind == 'split':
            for lot in p['lots']:
                lot[0] *= q
        elif kind == 'dividend':
            net = price - tax - fee
            p['dividends'] += net
            p['dividends_base'] += net * fx
            if tx.get('affects_cash'):
                cash[ck] += net
        elif kind in {'deposit', 'withdrawal', 'fee'}:
            cash[ck] += (price if kind == 'deposit' else -price) - fee - tax
    for p in positions.values():
        p['quantity'], p['cost'], p['base_cost'] = [sum((l[i] for l in p['lots']), ZERO) for i in range(3)]
    return positions, dict(cash), realized_by_tx


def xirr(flows):
    grouped = defaultdict(float)
    for day, amount in flows:
        grouped[date.fromisoformat(str(day))] += float(amount)
    points = sorted((d, v) for d, v in grouped.items() if abs(v) > 1e-10)
    if len(points) < 2 or not any(v < 0 for _, v in points) or not any(v > 0 for _, v in points):
        return None
    first = points[0][0]
    years = [((d - first).days / 365.0, v) for d, v in points]
    if years[-1][0] == 0:
        return None
    def npv(log_rate):
        try:
            return sum(v * math.exp(-log_rate * t) for t, v in years)
        except OverflowError:
            return math.copysign(math.inf, years[-1][1])
    # Search in log(1+r): bisection remains stable for small fractional holdings.
    grid = [-20 + i * .1 for i in range(401)]
    roots = []
    previous, pv = grid[0], npv(grid[0])
    for current in grid[1:]:
        cv = npv(current)
        if cv == 0 or pv * cv < 0:
            lo, hi = previous, current
            lv = npv(lo)
            for _ in range(100):
                mid = (lo + hi) / 2
                mv = npv(mid)
                if lv * mv <= 0:
                    hi = mid
                else:
                    lo, lv = mid, mv
            root = math.expm1((lo + hi) / 2)
            if not roots or abs(root - roots[-1]) > 1e-6:
                roots.append(root)
        previous, pv = current, cv
    return roots[0] if len(roots) == 1 else None


def valid_symbol(symbol):
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9.^=\-]{0,23}', symbol):
        raise ValueError('Ticker must be 1–24 letters, numbers, dots, dashes, ^ or =.')
    return symbol
