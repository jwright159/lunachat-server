from abc import ABC, abstractmethod
import asyncio
import json
from websockets.server import WebSocketServerProtocol

from .app import UserConnections
from .types import Data


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
	
	@abstractmethod
	async def _send(self):
		pass

class UserPacket(Packet):
	def __init__(self, user: UserConnections, type: str, data: dict[str, Data]):
		super().__init__(type, data)
		self.user = user

	async def _send(self):
		loop = asyncio.get_running_loop()
		for conn in self.user.websockets:
			loop.create_task(conn.websocket.send(json.dumps(self.data)))
	
	def __str__(self):
		return f"{self.data['type']} to {self.user.user.username}"

class WebsocketPacket(Packet):
	def __init__(self, websocket: WebSocketServerProtocol, type: str, data: dict[str, Data]):
		super().__init__(type, data)
		self.websocket = websocket
	
	async def _send(self):
		await self.websocket.send(json.dumps(self.data))
	
	def __str__(self):
		return f"{self.data['type']} to {self.websocket.host}"

class ErrorPacket(WebsocketPacket):
	def __init__(self, websocket: WebSocketServerProtocol, error: str):
		super().__init__(websocket, 'error', {'error': error})
	
	def __str__(self):
		return f"{self.data['type']} {self.data['error']} to {self.websocket.host}"