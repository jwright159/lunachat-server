import asyncio
import json
from websockets.server import serve, WebSocketServerProtocol

async def handler(websocket: WebSocketServerProtocol):
	print(f"Connected to {websocket.host}")
	username: str | None = None
	async for message in websocket:
		data = json.loads(message)
		match data['type']:
			case 'login':
				if username is None:
					username = data['username']
					await websocket.send(json.dumps({
						'type': 'login',
						'username': username,
					}))
				else:
					await websocket.send(json.dumps({
						'type': 'error',
						'error': 'Already logged in',
					}))
			case 'post':
				if username is None:
					await websocket.send(json.dumps({
						'type': 'error',
						'error': 'Not logged in',
					}))
				else:
					await websocket.send(json.dumps({
						'type': 'post',
						'username': username,
						'text': data['text'],
					}))
			case _:
				await websocket.send(json.dumps({
					'type': 'error',
					'error': 'Invalid type of message',
				}))
	print(f"Disconected from {websocket.host}")

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

start_server = serve(handler, 'localhost', 8000)

loop.run_until_complete(start_server)
loop.run_forever()