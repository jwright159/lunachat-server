from abc import ABC
import abc
import asyncio
from dataclasses import dataclass
import json
from types import TracebackType
from typing import Any, Callable, ParamSpec
import uuid
from websockets.server import serve, WebSocketServerProtocol
import bcrypt

P = ParamSpec('P')

Data = str | int | float | list['Data'] | dict[str, 'Data']

@dataclass
class User:
	id: str
	username: str
	password: bytes
	color: str
	connections: list['WebsocketConnection']
	
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
		loop = asyncio.get_running_loop()
		for conn in self.user.connections:
			loop.create_task(conn.websocket.send(json.dumps(self.data)))
	
	def __str__(self):
		return f"{self.data['type']} to {self.user.username}"

class ErrorPacket(Packet):
	def __init__(self, websocket: WebSocketServerProtocol, error: str):
		super().__init__('error', {'error': error})
		self.websocket = websocket
	
	async def _send(self):
		await self.websocket.send(json.dumps(self.data))
	
	def __str__(self):
		return f"{self.data['type']} {self.data['error']} to {self.websocket.host}"
		
async def send(packets: list[Packet]):
	loop = asyncio.get_running_loop()
	return [loop.create_task(packet.send()) for packet in packets]

class WebsocketConnection:
	def __init__(self, app: 'App', websocket: WebSocketServerProtocol):
		self.app = app
		self.websocket = websocket
		self.sender: User | None = None

	def __enter__(self):
		print(f"Connected to {self.websocket.host}")
		return self

	def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None):
		if self.sender:
			self.sender.connections.remove(self)
			host = str(self.sender)
		else:
			host = str(self.websocket.host)
		print(f"Disconected from {host} with {exc_value}")
	
	async def handle_message(self, message: str | bytes):
		try:
			data = json.loads(message)
			if data['type'] in self.app.handlers:
				handler = self.app.handlers[data['type']]
				await send(handler(self, data))
			else:
				await send(self.error_packets("Invalid type of message"))
		except Exception as e:
			await send(self.error_packets(f"Internal error: {e}"))
	
	def sender_packets(self, type: str, data: dict[str, Data]) -> list[Packet]:
		assert self.sender
		return self.app.sender_packets(self.sender, type, data)

	def all_packets(self, type: str, data: dict[str, Data]) -> list[Packet]:
		return self.app.all_packets(type, data)

	def all_but_sender_packets(self, type: str, data: dict[str, Data]) -> list[Packet]:
		assert self.sender
		return self.app.all_but_sender_packets(self.sender, type, data)

	def error_packets(self, error: str) -> list[Packet]:
		return self.app.error_packets(self.websocket, error)
			
Handler = Callable[[WebsocketConnection, Any], list[Packet]]

class App:
	handlers: dict[str, Handler] = {}

	@classmethod
	def handler(cls, handler: Handler):
		cls.handlers[handler.__name__] = handler
		return handler
	
	def __init__(self):
		self.users: dict[str, User] = {}
		self.users_by_username: dict[str, User] = {}

	async def websocket_handler(self, websocket: WebSocketServerProtocol):
		with WebsocketConnection(self, websocket) as conn:
			async for message in websocket:
				await conn.handle_message(message) # should i have a try here?
		
	def sender_packets(self, sender: User, type: str, data: dict[str, Data]) -> list[Packet]:
		return [UserPacket(sender, type, data)]

	def all_packets(self, type: str, data: dict[str, Data]) -> list[Packet]:
		return [UserPacket(user, type, data) for user in self.users.values()]

	def all_but_sender_packets(self, sender: User, type: str, data: dict[str, Data]) -> list[Packet]:
		return [UserPacket(user, type, data) for user in self.users.values() if user is not sender]

	def error_packets(self, sender: WebSocketServerProtocol, error: str) -> list[Packet]:
		return [ErrorPacket(sender, error)]

def generate_id():
	return uuid.uuid4().hex[:16]

@App.handler
def register(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is not None:
		return conn.error_packets("Already logged in")
	
	if data['username'] in conn.app.users_by_username:
		return conn.error_packets("Username taken")
	
	user_id = generate_id()
	while user_id in conn.app.users:
		user_id = generate_id()
	conn.sender = User(
		id=user_id,
		username=data['username'],
		password=bcrypt.hashpw(bytes(data['password'], encoding='utf-8'), bcrypt.gensalt()),
		color='#000000',
		connections=[conn],
	)
	conn.app.users[conn.sender.id] = conn.sender
	conn.app.users_by_username[conn.sender.username] = conn.sender
	return (
		conn.sender_packets('loginSelf', {
			'me': conn.sender.json(),
			'users': [user.json() for user in conn.app.users.values() if user is not conn.sender],
		}) +
		conn.all_but_sender_packets('login', {
			'user': conn.sender.json()
		})
	)

@App.handler
def login(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is not None:
		return conn.error_packets("Already logged in")
	
	if data['username'] not in conn.app.users_by_username:
		return conn.error_packets("No user with that username")

	attempted_user: User = conn.app.users_by_username[data['username']]
	if not bcrypt.checkpw(bytes(data['password'], encoding='utf-8'), attempted_user.password):
		return conn.error_packets("Wrong password")

	conn.sender = attempted_user
	conn.sender.connections.append(conn)
	return (
		conn.sender_packets('loginSelf', {
			'me': conn.sender.json(),
			'users': [user.json() for user in conn.app.users.values() if user is not conn.sender],
		}) +
		conn.all_but_sender_packets('login', {
			'user': conn.sender.json()
		})
	)

@App.handler
def post(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is None:
		return conn.error_packets("Not logged in")
	
	return conn.all_packets('post', {
		'user': conn.sender.id,
		'text': data['text'],
	})


def main():
	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)

	app = App()
	start_server = serve(app.websocket_handler, 'localhost', 8000)

	print("Server started")
	loop.run_until_complete(start_server)
	loop.run_forever()

if __name__ == '__main__':
	main()