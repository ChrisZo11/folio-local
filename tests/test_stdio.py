import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_client_can_initialize_and_read_portfolio(db):
    async def check():
        params=StdioServerParameters(command=sys.executable,args=['-m','portfolio','--db',str(db.path),'mcp'])
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                tools=await client.list_tools()
                assert len(tools.tools)==11
                result=await client.call_tool('list_portfolios',{})
                assert not result.isError
                body=json.loads(result.content[0].text)
                assert [p['name'] for p in body['portfolios']]==['Stockbit','GoTrade']
                status=await client.call_tool('data_status',{})
                assert json.loads(status.content[0].text)['transaction_count']==0
    asyncio.run(check())
