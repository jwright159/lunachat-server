from abc import ABC
import abc
import asyncio
import json
import uuid
from websockets.server import serve, WebSocketServerProtocol

Data = str | int | float | list['Data'] | dict[str, 'Data']

class User:
	def __init__(self, *, username: str, color: str, websocket: WebSocketServerProtocol):
		self.id = uuid.uuid4().hex[:16]
		self.username = username
		self.color = color
		self.websocket = websocket
	
	def json(self) -> dict[str, Data]:
		return {
			'id': self.id,
			'username': self.username,
			'color': self.color,
		}
	
	def __str__(self):
		return f"{self.username} ({self.id})"

class Packet(ABC):
	def __init__(self, type: str, data: dict[str, Data]):
		self.data = data
		self.data['type'] = type
		self.sent = False

	async def send(self):
		if not self.sent:
			print(f"Sending {self}")
			await self._send()
			self.sent = True
		else:
			raise RuntimeError(f"Already sent {self}")
	
	@abc.abstractmethod
	async def _send(self):
		pass

class UserPacket(Packet):
	def __init__(self, user: User, type: str, data: dict[str, Data]):
		super().__init__(type, data)
		self.user = user

	async def _send(self):
		await self.user.websocket.send(json.dumps(self.data))
	
	def __str__(self):
		return f"{self.data['type']} to {self.user.username}"

class ErrorPacket(Packet):
	def __init__(self, websocket: WebSocketServerProtocol, error: str):
		super().__init__('error', {'error': error})
		self.websocket = websocket
	
	async def _send(self):
		await self.websocket.send(json.dumps(self.data))
	
	def __str__(self):
		return f"{self.data['type']} to {self.websocket.host}"


users: dict[str, User] = {}

def sender_packets(sender: User, type: str, data: dict[str, Data]) -> list[Packet]:
	return [UserPacket(sender, type, data)]

def all_packets(type: str, data: dict[str, Data]) -> list[Packet]:
	return [UserPacket(user, type, data) for user in users.values()]

def all_but_sender_packets(sender: User, type: str, data: dict[str, Data]) -> list[Packet]:
	return [UserPacket(user, type, data) for user in users.values() if user is not sender]

def error_packets(sender: WebSocketServerProtocol, error: str) -> list[Packet]:
	return [ErrorPacket(sender, error)]

async def send(packets: list[Packet]):
	await asyncio.wait([asyncio.create_task(packet.send()) for packet in packets])

async def handler(websocket: WebSocketServerProtocol):
	print(f"Connected to {websocket.host}")

	sender: User | None = None

	try:
		async for message in websocket:
			data = json.loads(message)

			match data['type']:
				case 'login':
					if sender is None:
						sender = User(
							username=data['username'],
							color=data['color'],
							websocket=websocket,
						)
						users[sender.id] = sender
						await send(
							sender_packets(sender, 'loginSelf', {
								'me': sender.json(),
								'users': [user.json() for user in users.values() if user is not sender],
							}) +
							all_but_sender_packets(sender, 'login', {
								'user': sender.json()
							})
						)
					else:
						await send(error_packets(websocket, 'Already logged in'))
				
				case 'post':
					if sender is None:
						await send(error_packets(websocket, 'Not logged in'))
					else:
						await send(all_packets('post', {
							'user': sender.id,
							'text': data['text'],
						}))
				
				case _:
					await send(error_packets(websocket, 'Invalid type of message'))
	
	finally:
		if (sender):
			users.pop(sender.id)
			print(f"Disconnected from {sender}")
		else:
			print(f"Disconected from {websocket.host}")

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

start_server = serve(handler, 'localhost', 8000)

print("Server started")
loop.run_until_complete(start_server)
loop.run_forever()