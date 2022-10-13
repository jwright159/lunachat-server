from abc import ABC
import abc
import asyncio
from dataclasses import dataclass, field
import json
import os
import ssl
from types import TracebackType
from typing import Any, Callable
import uuid
from websockets.server import serve, WebSocketServerProtocol
import bcrypt
import sqlite3
from dotenv import load_dotenv #type: ignore
load_dotenv()

Data = str | int | float | list['Data'] | dict[str, 'Data']

@dataclass
class User:
	id: str
	username: str
	password: bytes
	color: str
	connections: list['WebsocketConnection'] = field(default_factory=list)
	
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
			if len(self.sender.connections) == 0:
				self.logout()

			host = str(self.sender)
		else:
			host = str(self.websocket.host)
		print(f"Disconected from {host} with {exc_value}")
	
	def handle_message(self, message: str | bytes):
		try:
			data = json.loads(message)
			if data['type'] in self.app.handlers:
				handler = self.app.handlers[data['type']]
				self.send(handler(self, data))
			else:
				self.send(self.error_packets("Invalid type of message"))
		except Exception as e:
			self.send(self.error_packets(f"Internal error: {e}"))
	
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
	
	def log_in_as_user(self, user: User):
		assert self.sender is None
		self.sender = user
		user.connections.append(self)
		self.app.connected_users[user.id] = user
	
	def logout(self):
		assert self.sender
		self.send(self.all_but_sender_packets('logout', {
			'user': self.sender.id,
		}))
		self.app.connected_users.pop(self.sender.id)
	
	def send(self, packets: list[Packet]):
		loop = asyncio.get_running_loop()
		return [loop.create_task(packet.send()) for packet in packets]
			
Handler = Callable[[WebsocketConnection, Any], list[Packet]]

class App:
	handlers: dict[str, Handler] = {}

	@classmethod
	def handler(cls, handler: Handler):
		cls.handlers[handler.__name__] = handler
		return handler
	
	def __init__(self, db: sqlite3.Connection):
		self._db = db
		self._db_cursor = db.cursor()
		if self._db_cursor.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone() is None:
			self._db_cursor.execute("""CREATE TABLE users(
				id TEXT NOT NULL PRIMARY KEY,
				username TEXT NOT NULL,
				password TEXT NOT NULL,
				color TEXT NOT NULL
			)""")
			self._db.commit()
		self.connected_users: dict[str, User] = {}

	async def websocket_handler(self, websocket: WebSocketServerProtocol):
		with WebsocketConnection(self, websocket) as conn:
			async for message in websocket:
				conn.handle_message(message) # should i have a try here?
		
	def sender_packets(self, sender: User, type: str, data: dict[str, Data]) -> list[Packet]:
		return [UserPacket(sender, type, data)]

	def all_packets(self, type: str, data: dict[str, Data]) -> list[Packet]:
		return [UserPacket(user, type, data) for user in self.connected_users.values()]

	def all_but_sender_packets(self, sender: User, type: str, data: dict[str, Data]) -> list[Packet]:
		return [UserPacket(user, type, data) for user in self.connected_users.values() if user is not sender]

	def error_packets(self, sender: WebSocketServerProtocol, error: str) -> list[Packet]:
		return [ErrorPacket(sender, error)]

	def _generate_id(self):
		return uuid.uuid4().hex[:16]

	def generate_valid_id(self):
		user_id = self._generate_id()
		while self.user_id_exists(user_id):
			user_id = self._generate_id()
		return user_id
	
	def user_id_exists(self, user_id: str):
		return self._db_cursor.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone() is not None
	
	def user_name_exists(self, username: str):
		return self._db_cursor.execute("SELECT username FROM users WHERE username=?", (username,)).fetchone() is not None
	
	def insert_user(self, user: User):
		self._db_cursor.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (user.id, user.username, str(user.password, encoding='utf-8'), user.color))
		self._db.commit()
	
	def select_user_by_name(self, username: str):
		"""Users should not be stored outside of connected_users"""
		res = self._db_cursor.execute("SELECT id, username, password, color FROM users WHERE username=?", (username,)).fetchone()
		if res[0] in self.connected_users:
			return self.connected_users[res[0]]
		else:
			return User(
				id=res[0],
				username=res[1],
				password=bytes(res[2], encoding='utf-8'),
				color=res[3],
			)


@App.handler
def register(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is not None:
		return conn.error_packets("Already logged in")
	
	if conn.app.user_name_exists(data['username']):
		return conn.error_packets("Username taken")
	
	user = User(
		id=conn.app.generate_valid_id(),
		username=data['username'],
		password=bcrypt.hashpw(bytes(data['password'], encoding='utf-8'), bcrypt.gensalt()),
		color='#000000',
	)
	conn.app.insert_user(user)
	conn.log_in_as_user(user)
	return (
		conn.sender_packets('loginSelf', {
			'me': user.json(),
			'users': [connected_user.json() for connected_user in conn.app.connected_users.values() if connected_user is not user],
		}) +
		conn.all_but_sender_packets('login', {
			'user': user.json()
		})
	)

@App.handler
def login(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is not None:
		return conn.error_packets("Already logged in")
	
	if not conn.app.user_name_exists(data['username']):
		return conn.error_packets("No user with that username")

	user: User = conn.app.select_user_by_name(data['username'])
	if not bcrypt.checkpw(bytes(data['password'], encoding='utf-8'), user.password):
		return conn.error_packets("Wrong password")

	conn.log_in_as_user(user)
	return (
		conn.sender_packets('loginSelf', {
			'me': user.json(),
			'users': [connected_user.json() for connected_user in conn.app.connected_users.values() if connected_user is not user],
		}) +
		conn.all_but_sender_packets('login', {
			'user': user.json()
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

	db = sqlite3.connect('lunachat.db')

	ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
	ssl_context.load_cert_chain('cert.pem', 'key.pem', os.getenv('LUNACHAT_SSL_PW'))

	app = App(db)
	start_server = serve(app.websocket_handler, 'localhost', 8000, ssl=ssl_context)

	print("Server started")
	loop.run_until_complete(start_server)
	loop.run_forever()

if __name__ == '__main__':
	main()