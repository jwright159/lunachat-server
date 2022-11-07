import asyncio
import json
from types import TracebackType
from typing import Any, Callable
from websockets.server import WebSocketServerProtocol

from .db import DB, DBSpec
from .lang import *
from .network import Data, ErrorPacket, Packet, UserPacket, WebsocketPacket
from .types import Guild, GuildID, User, UserID


class UserConnections:
	def __init__(self, user: User) -> None:
		self.user = user
		self.websockets: list['WebsocketConnection'] = []

class WebsocketConnection:
	def __init__(self, app: 'App', websocket: WebSocketServerProtocol):
		self.app = app
		self.websocket = websocket
		self.__connections: UserConnections | None = None
	
	@property
	def sender(self):
		return self.__connections.user if self.__connections else None

	def __enter__(self):
		print(f"Connected to {self.websocket.host}")
		return self

	def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None):
		if self.__connections:
			host = str(self.sender)
			self.log_out()
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
				self.send(self.error_packets(INVALID_MESSAGE_TYPE))
		except Exception as e:
			self.send(self.error_packets(f"{INTERNAL_ERROR}: {e}"))

	def make_packets(self, type: str, data: dict[str, Data], *, include_sender: bool = True, include_others_in: list[GuildID] | GuildID | None = None) -> list[Packet]:
		assert self.__connections
		return self.app.make_packets(self.__connections, type, data, include_sender=include_sender, include_others_in=include_others_in)
	
	def websocket_packets(self, type: str, data: dict[str, Data]) -> list[Packet]:
		return [WebsocketPacket(self.websocket, type, data)]

	def error_packets(self, error: str) -> list[Packet]:
		return [ErrorPacket(self.websocket, error)]
	
	def log_in(self, user: User):
		assert self.__connections is None
		if user.id in self.app.connected_users:
			self.__connections = self.app.connected_users[user.id]
		else:
			self.__connections = UserConnections(user)
			self.app.connected_users[user.id] = self.__connections
		self.__connections.websockets.append(self)
	
	def log_out(self):
		assert self.__connections
		self.__connections.websockets.remove(self)
		if not self.__connections.websockets:
			self.send(self.make_packets('logout', {
				'user': self.__connections.user.id,
			}, include_sender=False, include_others_in=self.__connections.user.guilds))
			self.app.connected_users.pop(self.__connections.user.id)
			self.__connections = None
	
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
		self.connected_users: dict[UserID, UserConnections] = {}
		self.guilds: dict[str, Guild] = {}
	
	async def websocket_handler(self, websocket: WebSocketServerProtocol):
		with WebsocketConnection(self, websocket) as conn:
			async for message in websocket:
				conn.handle_message(message) # should i have a try here?
	
	def make_packets(self, connections: UserConnections, type: str, data: dict[str, Data], *, include_sender: bool = True, include_others_in: list[GuildID] | GuildID | None = None):
		packets: list[Packet] = []
		if include_sender:
			packets += [UserPacket(connections, type, data)]
		match include_others_in:
			case list():
				guilds = [self.guild_spec.select('id', guild) for guild in include_others_in]
			case GuildID():
				guilds = [self.guild_spec.select('id', include_others_in)]
			case None:
				guilds = []
		user_ids = set(user_id for guild in guilds if guild is not None for user_id in guild.users)
		packets += [UserPacket(self.connected_users[user_id], type, data) for user_id in user_ids if user_id is not connections.user.id and user_id in self.connected_users]
		return packets