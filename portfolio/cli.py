import argparse
import json
import logging
from pathlib import Path
import sys

from .db import Database, ROOT


def main():
    parser=argparse.ArgumentParser(prog='folio',description='Your local investment portfolio. No accounts or subscriptions.')
    parser.add_argument('--db',type=Path,help='SQLite database path (default: data/portfolio.db or FOLIO_DB)')
    sub=parser.add_subparsers(dest='command',required=True)
    serve=sub.add_parser('serve',help='Open the local web app at http://127.0.0.1:8000')
    serve.add_argument('--demo',action='store_true',help='Use separate synthetic data/demo.db, never your real database')
    sub.add_parser('mcp',help='Read-only MCP server over stdio for Claude')
    imp=sub.add_parser('import',help='Validate and preview CSV; --confirm writes valid rows')
    imp.add_argument('file',type=Path)
    imp.add_argument('--source',choices=['generic','stockbit','gotrade','snowball'],default='generic')
    imp.add_argument('--quantity-unit',choices=['share','lot'],default='')
    imp.add_argument('--default-fee-rate',default='0')
    imp.add_argument('--confirm',action='store_true')
    refresh=sub.add_parser('refresh',help='Refresh prices, history, dividends and FX')
    refresh.add_argument('symbols',nargs='*')
    report=sub.add_parser('report',help='Print a JSON portfolio report')
    report.add_argument('--portfolio',type=int)
    report.add_argument('--currency',choices=['IDR','USD'])
    add=sub.add_parser('add',help='Add a manually confirmed canonical JSON transaction')
    add.add_argument('json_file',type=Path)
    edit=sub.add_parser('edit',help='Replace a transaction from canonical JSON')
    edit.add_argument('id',type=int)
    edit.add_argument('json_file',type=Path)
    remove=sub.add_parser('delete',help='Delete a transaction if the remaining ledger is valid')
    remove.add_argument('id',type=int)
    backup=sub.add_parser('backup',help='Create a consistent SQLite backup without overwriting')
    backup.add_argument('destination',type=Path)
    args=parser.parse_args()
    logging.basicConfig(level=logging.WARNING,stream=sys.stderr)
    try:
        if args.command=='serve' and args.demo:
            if args.db:
                raise ValueError('--demo uses data/demo.db. Omit --db to keep demo data isolated.')
            db=Database(ROOT/'data'/'demo.db')
            from .demo import seed_demo
            seed_demo(db)
        else:
            db=Database(args.db)
        if args.command=='serve':
            import uvicorn
            from .api import create_app
            uvicorn.run(create_app(db),host='127.0.0.1',port=8000,log_level='info')
            return
        if args.command=='mcp':
            from .mcp_server import create_server
            create_server(db).run(transport='stdio')
            return
        if args.command=='import':
            from .importer import preview, commit
            content=args.file.read_text(encoding='utf-8-sig')
            if args.confirm:
                result=commit(db,content,args.file.name,args.source,args.quantity_unit,args.default_fee_rate)
            else:
                result=preview(db,content,args.source,args.quantity_unit,args.default_fee_rate)
        elif args.command=='refresh':
            from .market import MarketService
            result=MarketService(db).refresh(args.symbols or None)
        elif args.command=='report':
            from .analytics import Analytics
            result=Analytics(db).summary(args.portfolio,args.currency)
        elif args.command in {'add','edit'}:
            from .importer import save_manual
            result={'id':save_manual(db,json.loads(args.json_file.read_text(encoding='utf-8')),args.id if args.command=='edit' else None)}
        elif args.command=='delete':
            from .importer import delete_transaction
            delete_transaction(db,args.id)
            result={'deleted':args.id}
        elif args.command=='backup':
            result={'path':db.backup(args.destination)}
        print(json.dumps(result,indent=2,ensure_ascii=False))
    except (ValueError,OSError) as exc:
        print(f'Error: {exc}',file=sys.stderr)
        sys.exit(1)


if __name__=='__main__':
    main()
