import asyncio
import json
import os
import ssl
from types import TracebackType
from typing import Any, Callable
import uuid
from websockets.server import serve, WebSocketServerProtocol
import sqlite3
from dotenv import load_dotenv # type: ignore

from .db import DB, DBSpec
from .network import Data, ErrorPacket, Packet, UserPacket
from .types import Guild, GuildID, User, UserID


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

	def make_packets(self, type: str, data: dict[str, Data], *, include_sender: bool = True, include_others_in: list[GuildID] | GuildID | None = None):
		assert self.sender
		return self.app.make_packets(self.sender, type, data, include_sender=include_sender, include_others_in=include_others_in)

	def error_packets(self, error: str) -> list[Packet]:
		return self.app.error_packets(self.websocket, error)
	
	def log_in_as_user(self, user: User):
		assert self.sender is None
		self.sender = user
		user.connections.append(self)
		self.app.connected_users[user.id] = user
	
	def logout(self):
		assert self.sender
		self.send(self.make_packets('logout', {
			'user': self.sender.id,
		}, include_sender=False, include_others_in=self.sender.guilds))
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
	
	def __init__(self, db: DB):
		self.__db = db
		self.user_spec = DBSpec[User]('users', self.__db, User.spec())
		self.guild_spec = DBSpec[Guild]('guilds', self.__db, Guild.spec())
		self.connected_users: dict[str, User] = {}
		self.guilds: dict[str, Guild] = {}
	
	async def websocket_handler(self, websocket: WebSocketServerProtocol):
		with WebsocketConnection(self, websocket) as conn:
			async for message in websocket:
				conn.handle_message(message) # should i have a try here?
	
	def make_packets(self, sender: User, type: str, data: dict[str, Data], *, include_sender: bool = True, include_others_in: list[GuildID] | GuildID | None = None):
		packets: list[Packet] = []
		if include_sender:
			packets += [UserPacket(sender, type, data)]
		match include_others_in:
			case list():
				guilds = [self.guild_spec.select('id', guild) for guild in include_others_in]
			case GuildID():
				guilds = [self.guild_spec.select('id', include_others_in)]
			case None:
				guilds = []
		user_ids = set(user_id for guild in guilds if guild is not None for user_id in guild.users)
		packets += [UserPacket(self.connected_users[user_id], type, data) for user_id in user_ids if user_id is not sender and user_id in self.connected_users]
		return packets

	def error_packets(self, sender: WebSocketServerProtocol, error: str) -> list[Packet]:
		return [ErrorPacket(sender, error)]

	def __generate_id(self) -> UserID:
		return UserID(uuid.uuid4().hex[:16])

	def generate_user_id(self) -> UserID:
		user_id = self.__generate_id()
		while self.user_spec.entry_exists('id', user_id):
			user_id = self.__generate_id()
		return user_id


def main():
	import .packets
	
	load_dotenv()

	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)

	db = DB(sqlite3.connect('lunachat.db'))

	ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
	ssl_context.load_cert_chain('cert.pem', 'key.pem', os.getenv('LUNACHAT_SSL_PW'))

	app = App(db)
	start_server = serve(app.websocket_handler, 'localhost', 8000, ssl=ssl_context)

	print("Server started")
	loop.run_until_complete(start_server)
	loop.run_forever()

if __name__ == '__main__':
	main()