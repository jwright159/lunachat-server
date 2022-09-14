import asyncio
from websockets.server import serve, WebSocketServerProtocol

async def handler(websocket: WebSocketServerProtocol):
	print(f"Connected to {websocket.host}")
	async for message in websocket:
		await websocket.send(f"Got {message}")
	print(f"Disconected from {websocket.host}")

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

start_server = serve(handler, 'localhost', 8000)

loop.run_until_complete(start_server)
loop.run_forever()