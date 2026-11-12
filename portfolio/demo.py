"""Synthetic fixtures, stored only in a separate demo database. Never broker records."""
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .db import now, today
from .importer import save_manual


def seed_demo(db):
    if db.transactions():
        if not db.settings().get('demo'):
            raise ValueError('Refusing to seed a database containing real transactions.')
        return
    db.set_settings({'demo':True})
    end=today()
    start=end-timedelta(days=400)
    specs=[('BBCA',600,8700,9625,'Stockbit'),('BBRI',200,3650,4280,'Stockbit'),
        ('BMRI',300,4900,5625,'Stockbit'),('SIDO',1000,495,585,'Stockbit'),
        ('SMSM',500,1650,1920,'Stockbit'),('SCHD',2.75,25.3,28.64,'GoTrade'),
        ('VOO',.125,535,602.45,'GoTrade')]
    portfolios={r['name']:r['id'] for r in db.rows('SELECT * FROM portfolios')}
    for index,(symbol,q,entry,current,portfolio) in enumerate(specs):
        currency='USD' if portfolio=='GoTrade' else 'IDR'
        day=start+timedelta(days=index*5)
        save_manual(db,{'portfolio_id':portfolios[portfolio],'date':day.isoformat(),'type':'buy','symbol':symbol,
            'quantity':q,'quantity_unit':'share','price':entry,'currency':currency,'fx_rate':15680 if currency=='USD' else 1,
            'fee':round(q*entry*.0015,4),'note':'Synthetic example — not your broker records.'})
        with db.connect() as conn:
            for offset in range(401):
                point=start+timedelta(days=offset)
                t=offset/400
                wave=math.sin(t*16+index)*.026+math.sin(t*43)*.011
                v=entry+(current-entry)*(t*.85+.15*t*t)+entry*wave*math.sin(math.pi*t)
                conn.execute('INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?)',
                    (symbol,point.isoformat(),str(round(v,6)),str(round(v,6)),currency,now()))
            conn.execute('INSERT OR REPLACE INTO quotes VALUES (?,?,?,?,?,?,?,?)',
                (symbol,str(current),str(round(current*.994,6)),currency,now(),now(),1,'demo'))
        for n in range(4):
            ex=end-timedelta(days=35+n*90)
            per_share=(entry*.038/4) if currency=='IDR' else (entry*.034/4)
            gross=round(q*per_share,6)
            with db.connect() as conn:
                conn.execute('INSERT OR REPLACE INTO dividends(symbol,ex_date,pay_date,amount_per_share,currency,source) VALUES (?,?,?,?,?,?)',
                    (symbol,ex.isoformat(),None,str(per_share),currency,'provider'))
            save_manual(db,{'portfolio_id':portfolios[portfolio],'date':(ex+timedelta(days=14)).isoformat(),
                'type':'dividend','symbol':symbol,'quantity':q,'quantity_unit':'share','price':gross,
                'tax':round(gross*.15,6) if currency=='USD' else 0,'currency':currency,
                'fx_rate':16240 if currency=='USD' else 1,'note':'Synthetic sample payout.'})
    with db.connect() as conn:
        for offset in range(410):
            point=start-timedelta(days=9)+timedelta(days=offset)
            conn.execute('INSERT OR REPLACE INTO fx_rates VALUES (?,?,?,?)',
                ('USDIDR',point.isoformat(),str(round(15680+offset*1.3+math.sin(offset/24)*110,2)),now()))
    db.set_settings({'last_refresh':now()})
