"""One reporting engine for HTTP, CLI, and MCP. All amounts are calculated here."""
from collections import defaultdict
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal

from .db import now, today
from .ledger import ZERO, ONE, EPS, dec, number, build_ledger, xirr
from .market import quote_status

PERIODS = {'1M':31, '3M':92, '1Y':365, '3Y':1095, 'ALL':None, 'YTD':None}


def period_start(period, first=None):
    if period not in PERIODS:
        raise ValueError('Period must be 1M, 3M, YTD, 1Y, 3Y or ALL.')
    if period == 'ALL':
        return date.fromisoformat(first) if first else today()
    if period == 'YTD':
        return date(today().year, 1, 1)
    return today()-timedelta(days=PERIODS[period])


class Analytics:
    def __init__(self, db):
        self.db = db

    def context(self, portfolio=None, base_currency=None):
        settings = self.db.settings()
        currency = base_currency or settings['base_currency']
        if currency not in {'IDR','USD'}:
            raise ValueError('Display currency must be IDR or USD.')
        if portfolio is not None and not self.db.rows('SELECT id FROM portfolios WHERE id=?', (portfolio,)):
            raise ValueError('Portfolio not found. Call list_portfolios for valid IDs.')
        fx_rows = self.db.rows("SELECT * FROM fx_rates WHERE pair='USDIDR' AND date<=? ORDER BY date DESC LIMIT 1", (today().isoformat(),))
        fx_row = fx_rows[0] if fx_rows else None
        fx = dec(fx_row['rate']) if fx_row else None
        return {'base_currency':currency, 'cost_basis':settings['cost_basis'], 'as_of':now(),
                'fx':fx, 'fx_row':fx_row, 'settings':settings, 'portfolio':portfolio,
                'fx_history':self.db.rows("SELECT date,rate FROM fx_rates WHERE pair='USDIDR' ORDER BY date"),
                'assumptions':['FIFO lot accounting' if settings['cost_basis']=='fifo' else 'Moving weighted-average cost',
                    'Trade FX is IDR per trading-currency unit; realized IDR amounts are converted at current FX for USD display.',
                    'Recorded dividends are confirmed broker amounts; forward dividends are estimates.',
                    'Buy/sell cash movements are optional; untracked cash purchases are external contributions.',
                    'Weekday market calendar; exchange holidays are not modeled.']}

    @staticmethod
    def convert(amount, currency, ctx):
        if amount == 0:
            return ZERO
        if currency == ctx['base_currency']:
            return amount
        if not ctx['fx']:
            return None
        return amount * ctx['fx'] if currency=='USD' else amount / ctx['fx']

    def envelope(self, ctx):
        fx = ctx['fx_row']
        return {'base_currency':ctx['base_currency'], 'units':'shares and currency amounts', 'cost_basis':ctx['cost_basis'],
            'as_of':ctx['as_of'], 'assumptions':ctx['assumptions'], 'fx':None if not fx else
            {'pair':'USDIDR','rate':number(ctx['fx']), 'as_of':fx['date'], 'fetched_at':fx['fetched_at'],
             'stale': (today()-date.fromisoformat(fx['date'])).days > (3 if today().weekday() in {0,5,6} else 1)}}

    def holdings(self, portfolio=None, base_currency=None, show_sold=False):
        ctx = self.context(portfolio,base_currency)
        txs = self.db.transactions(portfolio)
        positions,cash,_ = build_ledger(txs,ctx['cost_basis'])
        instruments = {r['symbol']:r for r in self.db.rows('SELECT * FROM instruments')}
        quotes = {r['symbol']:r for r in self.db.rows('SELECT * FROM quotes')}
        merged = {}
        for (_,symbol),p in positions.items():
            if symbol not in merged:
                merged[symbol] = {k:v for k,v in p.items() if k!='lots'}
            else:
                for key in ('quantity','cost','base_cost','realized','realized_base','dividends','dividends_base'):
                    merged[symbol][key] += p[key]
                merged[symbol]['history_complete'] &= p['history_complete']
        rows, warnings = [], []
        for symbol,p in merged.items():
            if p['quantity']<=EPS and not show_sold:
                continue
            instrument, quote = instruments[symbol],quotes.get(symbol)
            price = dec(quote['price']) if quote else None
            status = quote_status(quote,instrument,ctx['settings']['quote_ttl'])
            local_value = p['quantity']*price if price is not None else (ZERO if p['quantity']<=EPS else None)
            value = self.convert(local_value,instrument['currency'],ctx) if local_value is not None else None
            cost = self.convert(p['base_cost'],'IDR',ctx)
            realized = self.convert(p['realized_base'],'IDR',ctx)
            dividends = self.convert(p['dividends_base'],'IDR',ctx)
            unrealized = value-cost if value is not None and cost is not None else None
            profit = unrealized+realized+dividends if unrealized is not None and realized is not None and dividends is not None else None
            native_return = (local_value-p['cost'])/p['cost'] if local_value is not None and p['cost'] else None
            asset_gain_idr = (local_value-p['cost'])*ctx['fx'] if local_value is not None and instrument['currency']=='USD' and ctx['fx'] else None
            base_gain_idr = local_value*ctx['fx']-p['base_cost'] if local_value is not None and instrument['currency']=='USD' and ctx['fx'] else None
            prev = dec(quote['previous_close']) if quote and quote.get('previous_close') else None
            day_change = self.convert((price-prev)*p['quantity'],instrument['currency'],ctx) if price is not None and prev is not None else None
            payouts = self.db.rows('SELECT * FROM dividends WHERE symbol=? ORDER BY ex_date DESC', (symbol,))
            trailing = sum((dec(d.get('adjusted_amount_per_share') or d['amount_per_share']) for d in payouts if date.fromisoformat(d['ex_date'])>today()-timedelta(days=365)),ZERO)
            annual = trailing*p['quantity']
            annual_base = self.convert(annual,instrument['currency'],ctx)
            old_year = today().year-5
            old = sum((dec(d.get('adjusted_amount_per_share') or d['amount_per_share']) for d in payouts if date.fromisoformat(d['ex_date']).year==old_year),ZERO)
            last_year = sum((dec(d.get('adjusted_amount_per_share') or d['amount_per_share']) for d in payouts if date.fromisoformat(d['ex_date']).year==today().year-1),ZERO)
            growth = (float(last_year/old)**.2-1) if old and last_year else None
            flows = self._security_flows(txs,symbol,ctx)
            irr = None if not p['history_complete'] or value is None or flows is None else xirr(flows+[(today().isoformat(),value)])
            rows.append({**instrument, 'quantity':number(p['quantity']), 'cost_basis':number(cost),
                'local_cost':number(p['cost']), 'average_cost':number(p['cost']/p['quantity']) if p['quantity']>EPS else None,
                'price':number(price), 'value':number(value), 'local_value':number(local_value),
                'unrealized_pnl':number(unrealized), 'realized_pnl':number(realized), 'dividends_received':number(dividends),
                'total_profit':number(profit), 'profit_percent':number(profit/cost) if profit is not None and cost else None,
                'asset_return':number(native_return), 'currency_effect':number(self.convert(base_gain_idr-asset_gain_idr,'IDR',ctx)) if base_gain_idr is not None else None,
                'day_change':number(day_change), 'day_percent':number((price-prev)/prev) if price is not None and prev else None,
                'yield_on_cost':number(annual/p['cost']) if annual and p['cost'] else None,
                'current_yield':number(trailing/price) if trailing and price else None,
                'annual_income_estimate':number(annual_base) if payouts else None,
                'dividend_growth_5y':growth, 'irr':irr, 'history_complete':p['history_complete'], 'quote':status})
            if not p['history_complete']:
                warnings.append(f'{symbol}: holdings-only balance; performance is unavailable until full transactions are entered.')
            if status['status']=='missing':
                warnings.append(f'{symbol}: no cached price; refresh prices or enter a manual valuation.')
            elif status['stale']:
                warnings.append(f'{symbol}: cached quote is stale. Last known value is shown.')
        total = sum((r['value'] for r in rows if r['value'] is not None),0)
        for row in rows:
            row['weight'] = row['value']/total if row['value'] is not None and total else 0
        cash_rows=[]
        for (pid,currency),amount in cash.items():
            cash_rows.append({'portfolio_id':pid,'currency':currency,'amount':number(amount),'value':number(self.convert(amount,currency,ctx))})
            if amount < 0:
                warnings.append(f'Portfolio {pid}: negative {currency} tracked cash. Check for missing deposits or inconsistent cash-update flags.')
        complete = all(r['value'] is not None and r['cost_basis'] is not None for r in rows) and all(c['value'] is not None for c in cash_rows)
        if not complete and not ctx['fx'] and any(r['currency']!=ctx['base_currency'] for r in rows+cash_rows):
            warnings.append('USD/IDR rate is missing; totals exclude unconvertible amounts and are incomplete.')
        if self.envelope(ctx)['fx'] and self.envelope(ctx)['fx']['stale']:
            warnings.append('USD/IDR is stale. Totals use the last cached FX rate.')
        timestamps=[r['quote']['as_of'] for r in rows if r['quote']['as_of'] and r['quantity']>0]
        return {**self.envelope(ctx),'price_as_of':min(timestamps) if timestamps else None,
                'price_timestamp_scope':'Oldest cached quote among open holdings; individual quote timestamps are included per holding.',
                'holdings':sorted(rows,key=lambda r:r['value'] or 0,reverse=True),
                'cash':cash_rows,'complete':complete,'warnings':warnings}

    def _security_flows(self, txs, symbol, ctx):
        flows=[]
        for tx in txs:
            if symbol is not None and tx['symbol']!=symbol:
                continue
            q,p,f,t,fx=(dec(tx[k]) for k in ('quantity','price','fee','tax','fx_rate'))
            amount = None
            if tx['type'] in {'buy','opening'}:
                amount = -(q*p+f+t)
            elif tx['type']=='sell':
                amount = q*p-f-t
            elif tx['type']=='dividend':
                amount=p-f-t
            if amount is not None:
                if ctx['base_currency']=='IDR':
                    value=amount*fx
                elif tx['currency']=='USD':
                    value=amount
                else:
                    history=[r for r in ctx['fx_history'] if r['date']<=tx['date']]
                    if not history or (date.fromisoformat(tx['date'])-date.fromisoformat(history[-1]['date'])).days>7:
                        return None
                    value=amount/dec(history[-1]['rate'])
                flows.append((tx['date'],value))
        return flows

    def summary(self, portfolio=None, base_currency=None):
        result = self.holdings(portfolio,base_currency)
        rows=result['holdings']
        total=sum((r['value'] or 0 for r in rows),0)
        cost=sum((r['cost_basis'] or 0 for r in rows),0)
        all_rows=self.holdings(portfolio,base_currency,True)['holdings']
        realized=sum((r['realized_pnl'] or 0 for r in all_rows),0)
        divs=sum((r['dividends_received'] or 0 for r in all_rows),0)
        cash=sum((c['value'] or 0 for c in result['cash']),0)
        income=sum((r['annual_income_estimate'] or 0 for r in rows),0)
        gains=total-cost+realized+divs
        ctx=self.context(portfolio,base_currency)
        txs=self.db.transactions(portfolio)
        flows=self._security_flows(txs,None,ctx)
        complete_history=all(r['history_complete'] for r in all_rows)
        irr=xirr(flows+[(today().isoformat(),total)]) if complete_history and result['complete'] and flows is not None else None
        current_flows=[self._security_flows(txs,r['symbol'],ctx) for r in rows]
        current_irr=xirr([f for group in current_flows for f in group]+[(today().isoformat(),total)]) if complete_history and result['complete'] and all(g is not None for g in current_flows) else None
        if flows is None:
            result['warnings'].append('Historical USD/IDR is missing for some cash flows; USD-denominated XIRR is unavailable. Refresh full history.')
        day=sum((r['day_change'] or 0 for r in rows),0)
        complete=result['complete']
        securities_complete=all(r['value'] is not None for r in rows)
        cost_complete=all(r['cost_basis'] is not None for r in rows)
        day_complete=all(r['day_change'] is not None for r in rows)
        expenses=sum(((dec(t['price'])+dec(t['fee'])+dec(t['tax']))*dec(t['fx_rate']) if t['type']=='fee'
            else (dec(t['fee'])+dec(t['tax']))*dec(t['fx_rate'])
            for t in txs if t['type'] in {'fee','deposit','withdrawal'}),ZERO)
        expense_value=self.convert(expenses,'IDR',ctx)
        gains-=number(expense_value) or 0
        return {**result,'total_value':total+cash if complete else None,'securities_value':total if securities_complete else None,
            'cash_value':cash if all(c['value'] is not None for c in result['cash']) else None,'invested':cost if cost_complete else None,
            'known_value':total+cash,'total_profit':gains if complete else None,'profit_percent':gains/cost if cost and complete else None,
            'realized_pnl':realized if all(r['realized_pnl'] is not None for r in all_rows) else None,
            'unrealized_pnl':total-cost if complete else None,'dividends_received':divs if all(r['dividends_received'] is not None for r in all_rows) else None,
            'day_change':day if day_complete and complete else None,
            'day_percent':day/(total-day) if total-day and day_complete and complete else None,'irr':irr,'current_holdings_irr':current_irr,
            'irr_scope':'Annualized security cash-flow XIRR; excludes idle cash. Undefined or non-unique roots are unavailable.',
            'annual_income_estimate':income,'income_yield':income/total if total and complete else None,
            'income_estimate_complete':all(r['annual_income_estimate'] is not None for r in rows)}

    def allocation(self, portfolio=None, base_currency=None, group_by='asset_class'):
        if group_by not in {'asset_class','sector','currency','country','symbol'}:
            raise ValueError('Group by asset_class, sector, currency, country, or symbol.')
        data=self.holdings(portfolio,base_currency)
        groups={}
        for row in data['holdings']:
            key=row[group_by]
            group=groups.setdefault(key,{'name':key,'value':0,'invested':0,'profit':0,'count':0})
            group['value']+=row['value'] or 0
            group['invested']+=row['cost_basis'] or 0
            group['profit']+=row['total_profit'] or 0
            group['count']+=1
        total=sum(g['value'] for g in groups.values())
        for group in groups.values():
            group['weight']=group['value']/total if total else 0
        return {k:v for k,v in data.items() if k not in {'holdings','cash'}} | {'group_by':group_by,'groups':sorted(groups.values(),key=lambda g:g['value'],reverse=True), 'scope':'Securities only; tracked cash is reported separately.'}

    def concentration(self, portfolio=None, base_currency=None, top_n=5):
        if not 1<=top_n<=100:
            raise ValueError('top_n must be between 1 and 100.')
        data=self.holdings(portfolio,base_currency)
        rows=data.pop('holdings')
        return {**data,'top_holdings':[{'symbol':r['symbol'],'weight':r['weight'],'value':r['value']} for r in rows[:top_n]],
            'top_weight':sum(r['weight'] for r in rows[:top_n]),'hhi':sum(r['weight']**2 for r in rows),
            'hhi_units':'0 to 1; 1 means a single position','scope':'Securities only; weights exclude unpriced holdings.'}

    def transactions(self, portfolio=None, base_currency=None, symbol=None, kind=None, start=None, end=None, limit=1000):
        ctx=self.context(portfolio,base_currency)
        if not 1<=limit<=10000:
            raise ValueError('Limit must be between 1 and 10000.')
        for day in (start,end):
            if day:
                date.fromisoformat(day)
        txs=self.db.transactions(portfolio)
        _,_,profits=build_ledger(txs,ctx['cost_basis'])
        rows=[]
        for tx in txs:
            if (symbol and tx['symbol']!=symbol) or (kind and tx['type']!=kind) or (start and tx['date']<start) or (end and tx['date']>end):
                continue
            row=dict(tx)
            for field in ('quantity','price','fee','tax','fx_rate'):
                row[field]=number(dec(tx[field]))
            gross=dec(tx['quantity'])*dec(tx['price']) if tx['type'] in {'buy','sell','opening'} else dec(tx['price'])
            total=gross+dec(tx['fee'])+dec(tx['tax']) if tx['type'] in {'buy','opening','withdrawal','fee'} else gross-dec(tx['fee'])-dec(tx['tax'])
            row['total']=number(total)
            row['base_total']=number(self.convert(total*dec(tx['fx_rate']),'IDR',ctx))
            row['profit']=number(self.convert(profits[tx['id']],'IDR',ctx)) if tx['id'] in profits else None
            rows.append(row)
        return {**self.envelope(ctx),'transactions':list(reversed(rows))[:limit],'count':len(rows)}

    def dividends(self, portfolio=None, base_currency=None, period='ALL', group_by='symbol'):
        if group_by not in {'symbol','month'}:
            raise ValueError('Group dividends by symbol or month.')
        ctx=self.context(portfolio,base_currency)
        txs=self.db.transactions(portfolio)
        start=period_start(period,txs[0]['date'] if txs else None).isoformat()
        actual=[]
        for tx in txs:
            if tx['type']!='dividend' or tx['date']<start:
                continue
            gross,tax,fee,fx=(dec(tx[k]) for k in ('price','tax','fee','fx_rate'))
            actual.append({'id':tx['id'],'symbol':tx['symbol'],'date':tx['date'],'portfolio':tx['portfolio'],
                'currency':tx['currency'],'gross':number(gross),'tax':number(tax),'fee':number(fee),'net':number(gross-tax-fee),
                'base_net':number(self.convert((gross-tax-fee)*fx,'IDR',ctx)),'status':'confirmed','reported_shares':number(dec(tx['quantity']))})
        holdings=self.holdings(portfolio,base_currency)['holdings']
        proposed=[]
        instruments={r['symbol']:r for r in self.db.rows('SELECT * FROM instruments')}
        for d in self.db.rows('SELECT * FROM dividends WHERE ex_date>=? ORDER BY ex_date DESC',(start,)):
            eligible=[t for t in txs if t['date']<d['ex_date']]
            positions,_,_=build_ledger(eligible,ctx['cost_basis'])
            for (pid,symbol),p in positions.items():
                if symbol!=d['symbol'] or p['quantity']<=EPS:
                    continue
                # A nearby confirmation reconciles the proposal, but never changes either recorded share count.
                confirmed=next((t for t in txs if t['type']=='dividend' and t['symbol']==symbol and t['portfolio_id']==pid
                    and 0<=(date.fromisoformat(t['date'])-date.fromisoformat(d['ex_date'])).days<=90),None)
                reported=dec(confirmed['quantity']) if confirmed else None
                mismatch=reported is not None and reported>0 and abs(reported-p['quantity'])>EPS
                if confirmed and not mismatch:
                    continue
                currency=instruments[symbol]['currency']
                proposed.append({'symbol':symbol,'portfolio_id':pid,'ex_date':d['ex_date'],
                    'quantity':number(p['quantity']),'reported_shares':number(reported),'reconciliation_warning':mismatch,
                    'amount_per_share':number(dec(d['amount_per_share'])), 'gross':number(p['quantity']*dec(d['amount_per_share'])),
                    'currency':currency,'status':'estimated' if not confirmed else 'discrepancy','confirmed_transaction_id':confirmed['id'] if confirmed else None})
        groups=defaultdict(float)
        for row in actual:
            groups[row['symbol'] if group_by=='symbol' else row['date'][:7]]+=row['base_net'] or 0
        trailing=sum((number(self.convert((dec(t['price'])-dec(t['tax'])-dec(t['fee']))*dec(t['fx_rate']),'IDR',ctx)) or 0
            for t in txs if t['type']=='dividend' and t['date']>(today()-timedelta(days=365)).isoformat()),0)
        forecasts=[]
        for h in holdings:
            rows=self.db.rows('SELECT * FROM dividends WHERE symbol=? AND ex_date>? ORDER BY ex_date DESC',
                (h['symbol'],(today()-timedelta(days=550)).isoformat()))
            if len(rows)<2:
                forecasts.append({'symbol':h['symbol'],'annual_estimate':None,'reason':'At least two recent payouts are needed to infer frequency.'})
                continue
            gap=(date.fromisoformat(rows[0]['ex_date'])-date.fromisoformat(rows[1]['ex_date'])).days
            frequency=min((1,2,4,12),key=lambda f:abs(365/f-gap))
            amount=dec(rows[0].get('adjusted_amount_per_share') or rows[0]['amount_per_share'])*dec(h['quantity'])*frequency
            forecasts.append({'symbol':h['symbol'],'annual_estimate':number(self.convert(amount,h['currency'],ctx)),
                'payouts_per_year':frequency,'status':'estimated','assumption':'Most recent payout amount and inferred frequency; gross, before tax.'})
        return {**self.envelope(ctx),'period':period,'confirmed':actual,'proposals':proposed,
            'total_net':sum(r['base_net'] or 0 for r in actual),'trailing_12_months':trailing,
            'groups':[{'name':k,'value':v} for k,v in groups.items()], 'forward_estimates':forecasts,
            'annual_estimate':sum(r['annual_estimate'] or 0 for r in forecasts),
            'estimate_complete':all(r['annual_estimate'] is not None for r in forecasts),
            'complete':all(r['base_net'] is not None for r in actual)}

    def performance(self, portfolio=None, base_currency=None, period='ALL'):
        ctx=self.context(portfolio,base_currency)
        txs=self.db.transactions(portfolio)
        start=period_start(period,txs[0]['date'] if txs else None)
        history_complete=all(t['type']!='opening' for t in txs)
        result={**self.envelope(ctx),'period':period,'start':start.isoformat(),'end':today().isoformat(),
            'xirr':None,'twr':None,'series':[],'warnings':[],
            'method':'Daily time-weighted return, external flows at end of day; untracked trade cash treated as external. Annualized XIRR uses actual dates.',
            'contributors':[]}
        if not txs:
            result['warnings'].append('Import transactions to calculate performance.')
            return result
        if not history_complete:
            result['warnings'].append('Holdings-only opening balances have no full return history. Import the underlying trades first.')
            return result
        if start<date.fromisoformat(txs[0]['date']):
            start=date.fromisoformat(txs[0]['date'])
            result['start']=start.isoformat()
        instruments={r['symbol']:r for r in self.db.rows('SELECT * FROM instruments')}
        prices=defaultdict(list)
        for p in self.db.rows('SELECT * FROM prices ORDER BY date'):
            prices[p['symbol']].append(p)
        fx_rows=self.db.rows("SELECT * FROM fx_rates WHERE pair='USDIDR' ORDER BY date")
        def nearest(rows,day,key):
            candidates=[r for r in rows if r['date']<=day.isoformat()]
            if not candidates or (day-date.fromisoformat(candidates[-1]['date'])).days>7:
                return None
            return dec(candidates[-1][key])
        def value_on(day,events):
            positions,cash,_=build_ledger(events,ctx['cost_basis'])
            rate=nearest(fx_rows,day,'rate')
            total=ZERO
            for (_,symbol),p in positions.items():
                if p['quantity']<=EPS:
                    continue
                close=nearest(prices[symbol],day,'close')
                if close is None:
                    return None,f'{symbol}: historical price missing near {day}.'
                if instruments[symbol]['currency']=='USD':
                    if rate is None:
                        return None,f'Historical USD/IDR missing near {day}.'
                    close*=rate
                total+=p['quantity']*close
            for (_,currency),amount in cash.items():
                if currency=='USD' and rate is None and amount:
                    return None,f'Historical USD/IDR missing near {day}.'
                total+=amount*(rate or ONE) if currency=='USD' else amount
            if ctx['base_currency']=='USD' and total:
                if rate is None:
                    return None,f'Historical USD/IDR missing near {day}.'
                total/=rate
            return total,None
        prior=[t for t in txs if t['date']<start.isoformat()]
        previous,reason=value_on(start-timedelta(days=1),prior)
        if reason:
            result['warnings'].append(reason+' Refresh full history; returns are unavailable.')
            return result
        flows=[]
        if previous:
            flows.append(((start-timedelta(days=1)).isoformat(),-previous))
        factor=ONE
        day=start
        while day<=today():
            events=[t for t in txs if t['date']<=day.isoformat()]
            value,reason=value_on(day,events)
            if reason:
                result['warnings'].append(reason+' Refresh full history; returns are unavailable.')
                result['series']=[]
                return result
            external=ZERO
            for tx in [t for t in txs if t['date']==day.isoformat()]:
                q,p,f,t,fx=(dec(tx[k]) for k in ('quantity','price','fee','tax','fx_rate'))
                amount=ZERO
                if tx['type']=='deposit':
                    amount=p
                elif tx['type']=='withdrawal':
                    amount=-p
                elif not tx['affects_cash']:
                    if tx['type']=='buy':
                        amount=q*p+f+t
                    elif tx['type']=='sell':
                        amount=-(q*p-f-t)
                    elif tx['type']=='dividend':
                        amount=-(p-f-t)
                if ctx['base_currency']=='IDR':
                    amount*=fx
                elif tx['currency']=='IDR':
                    rate=nearest(fx_rows,day,'rate')
                    if rate is None:
                        result['warnings'].append('Historical FX is incomplete; returns are unavailable.')
                        result['series']=[]
                        return result
                    amount/=rate
                external+=amount
            if external:
                flows.append((day.isoformat(),-external))
            if previous>EPS:
                factor*=(value-external)/previous
            elif abs(value-external)>Decimal('.01'):
                result['warnings'].append('Return begins after the first end-of-day contribution; first-day price movement is excluded from TWR.')
            result['series'].append({'date':day.isoformat(),'value':number(value),'return':number(factor-ONE),'external_flow':number(external)})
            previous=value
            day+=timedelta(days=1)
        flows.append((today().isoformat(),previous))
        result['xirr']=xirr(flows)
        result['twr']=number(factor-ONE)
        contributors=[]
        for symbol in sorted({t['symbol'] for t in txs if t['symbol']}):
            security=[{**t,'affects_cash':0} for t in txs if t['symbol']==symbol and t['type'] in {'buy','sell','dividend','split'}]
            opening,opening_error=value_on(start-timedelta(days=1),[t for t in security if t['date']<start.isoformat()])
            closing,closing_error=value_on(today(),security)
            security_flows=self._security_flows([t for t in security if t['date']>=start.isoformat()],symbol,ctx)
            gain=closing-opening+sum((v for _,v in security_flows),ZERO) if not opening_error and not closing_error and security_flows is not None else None
            contributors.append({'symbol':symbol,'profit':number(gain)})
        result['contributors']=sorted(contributors,key=lambda c:c['profit'] or 0,reverse=True)
        result['contributor_scope']='Selected-period change in security value plus net distributions minus contributions, using historical prices and FX. Unattributed cash expenses are excluded.'
        return result

    def status(self):
        settings=self.db.settings()
        return {'as_of':now(),'local_only':True,'demo':settings.get('demo',False),'database':str(self.db.path),
            'last_import':next(iter(self.db.rows('SELECT * FROM imports ORDER BY id DESC LIMIT 1')),None),
            'last_refresh':settings.get('last_refresh'),'refresh_errors':settings.get('refresh_errors',[]),
            'transaction_count':self.db.rows('SELECT COUNT(*) n FROM transactions')[0]['n'],
            'quote_count':self.db.rows('SELECT COUNT(*) n FROM quotes')[0]['n'],
            'warnings':self.holdings()['warnings']}
