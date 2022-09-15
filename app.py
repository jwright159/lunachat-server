from abc import ABC
import abc
import asyncio
from dataclasses import dataclass
import json
from typing import Any
from websockets.server import serve, WebSocketServerProtocol

@dataclass
class User:
	username: str
	websocket: WebSocketServerProtocol

class Packet(ABC):
	def __init__(self, type: str, **data: Any):
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
	def __init__(self, user: User, type: str, **data: Any):
		super().__init__(type, **data)
		self.user = user

	async def _send(self):
		await self.user.websocket.send(json.dumps(self.data))
	
	def __str__(self):
		return f"{self.data['type']} to {self.user.username}"

class ErrorPacket(Packet):
	def __init__(self, websocket: WebSocketServerProtocol, error: str):
		super().__init__('error', error=error)
		self.websocket = websocket
	
	async def _send(self):
		await self.websocket.send(json.dumps(self.data))
	
	def __str__(self):
		return f"{self.data['type']} to {self.websocket.host}"


users: list[User] = []

def sender_packets(sender: User, type: str, **data: Any) -> list[Packet]:
	return [UserPacket(sender, type, **data)]

def all_packets(type: str, **data: Any) -> list[Packet]:
	return [UserPacket(user, type, **data) for user in users]

def all_but_sender_packets(sender: User, type: str, **data: Any) -> list[Packet]:
	return [UserPacket(user, type, **data) for user in users if user is not sender]

def error_packets(sender: WebSocketServerProtocol, error: str) -> list[Packet]:
	return [ErrorPacket(sender, error)]

async def send(packets: list[Packet]):
	await asyncio.wait([asyncio.create_task(packet.send()) for packet in packets])

async def handler(websocket: WebSocketServerProtocol):
	print(f"Connected to {websocket.host}")

	user: User | None = None

	try:
		async for message in websocket:
			data = json.loads(message)

			match data['type']:
				case 'login':
					if user is None:
						user = User(
							username=data['username'],
							websocket=websocket,
						)
						users.append(user)
						await send(
							sender_packets(user, 'loginSelf',
								username=user.username,
							) +
							all_but_sender_packets(user, 'login',
								username=user.username,
							)
						)
					else:
						await send(error_packets(websocket, 'Already logged in'))
				
				case 'post':
					if user is None:
						await send(error_packets(websocket, 'Not logged in'))
					else:
						await send(all_packets('post',
							username=user.username,
							text=data['text'],
						))
				
				case _:
					await send(error_packets(websocket, 'Invalid type of message'))
	
	finally:
		if (user):
			users.remove(user)
		print(f"Disconected from {websocket.host}")

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

start_server = serve(handler, 'localhost', 8000)

print("Starting")
loop.run_until_complete(start_server)
loop.run_forever()