A simple WebSocket server written in Python with curio.

## Example
```python
from curio import run
from simplews import ws_server

async def test_server(client, addr, recv, send):
    print('accepted')
    async for data in recv:
        print('received: {}'.format(data))
        await send(data.upper())

run(ws_server, '', 8000, test_server)
```
