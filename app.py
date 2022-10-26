from abc import ABC
import abc
import asyncio
from dataclasses import dataclass, field
import json
import os
from sqlite3.dbapi2 import _Parameters
import ssl
from types import TracebackType
from typing import Any, Callable, Generic, Mapping, NewType, Sequence, TypeVar, cast
import uuid
from websockets.server import serve, WebSocketServerProtocol
import bcrypt
from sqlite3 import Connection, Cursor, connect
from dotenv import load_dotenv #type: ignore
load_dotenv()


GuildID = NewType('GuildID', str)
UserID = NewType('UserID', str)
ChannelID = NewType('ChannelID', str)

ObjectID = GuildID | UserID | ChannelID

DataBlock = Mapping[str, 'Data']
Data = str | int | float | ObjectID | Sequence['Data'] | DataBlock

Spec = set[str]


NOT_LOGGED_IN = "Not logged in"
ALREADY_LOGGED_IN = "Already logged in"
NO_GUILD_WITH_ID = "No guild with that id"


@dataclass
class UniqueObject:
	id: ObjectID
	name: str

	def __eq__(self, other: object) -> bool:
		return other is UniqueObject and cast(UniqueObject, other).id == self.id
	
	def json(self) -> DataBlock:
		return dict((attr, getattr(self, attr)) for attr in self.json_spec())
	
	@classmethod
	def json_spec(cls) -> Spec:
		return {'id', 'name'}
	
	@classmethod
	def spec(cls) -> Spec:
		return {'id', 'name'}

UNIQUE = TypeVar('UNIQUE', bound=UniqueObject)

@dataclass
class User(UniqueObject):
	id: UserID
	username: str
	password: str
	color: str
	guilds: list[GuildID]
	connections: list['WebsocketConnection'] = field(default_factory=list)
	
	def __str__(self):
		return f"{self.username} ({self.id})"
	
	@classmethod
	def json_spec(cls) -> Spec:
		return super().json_spec() | {'username', 'color'}
	
	@classmethod
	def spec(cls) -> Spec:
		return super().spec() | {'username', 'password', 'color', 'guilds'}

@dataclass
class Channel(UniqueObject):
	id: ChannelID
	
	@classmethod
	def json_spec(cls) -> Spec:
		return super().json_spec()
	
	@classmethod
	def spec(cls) -> Spec:
		return super().spec()

@dataclass
class Guild(UniqueObject):
	id: GuildID
	channels: list[ChannelID]
	users: list[UserID]

	def json(self) -> DataBlock:
		return super().json() | {
			'channels': self.channels,
			'users': self.users,
		}

class DB:
	def __init__(self, db: Connection) -> None:
		self.__db = db
		self.__cursor = db.cursor()
	
	def execute(self, sql: str, params: _Parameters):
		self.__cursor.execute(sql, params)
		self.__db.commit()
	
	def fetchone(self, sql: str, params: _Parameters):
		return self.__cursor.execute(sql, params).fetchone()
	
	def fetchall(self, sql: str, params: _Parameters):
		return self.__cursor.execute(sql, params).fetchall()
	
	def fetchmany(self, sql: str, params: _Parameters, size: int):
		return self.__cursor.execute(sql, params).fetchmany(size)


class DBSpec(Generic[UNIQUE]):
	def __init__(self, table_name: str, db: DB, spec: Spec) -> None:
		self.__table_name = table_name
		self.__db = db
		self.__spec = list(spec)
		self.__params = {'table': self.__table_name}

		if not spec:
			raise ValueError("Spec needs at least one key")

		self.__ensure_table_exists()
	
	def __table_exists(self) -> bool:
		return self.__db.fetchone("SELECT name FROM sqlite_master WHERE name = :table", (self.__table_name,)) is not None

	def __create_table(self):
		self.__db.execute(
			"CREATE TABLE :table (" + ", ".join([f":{self.__spec[0]} TEXT NOT NULL PRIMARY KEY"] + [f":{key} TEXT NOT NULL" for key in self.__spec[1:]]) + ")",
			self.__params | dict(zip(self.__spec, self.__spec)),
		)
	
	def __ensure_table_exists(self):
		if not self.__table_exists():
			self.__create_table()

	def insert(self, obj: UNIQUE):
		self.__db.execute(
			"INSERT INTO :table VALUES (" + ", ".join(f":{key}" for key in self.__spec) + ")",
			self.__params | dict((key, getattr(obj, key)) for key in self.__spec),
		)

	def update(self, obj: UNIQUE, key: str):
		self.__db.execute(
			"UPDATE :table SET :key = :value WHERE id = :id",
			self.__params | {'key': key, 'value': getattr(obj, key), 'id': obj.id},
		)

	def entry_exists(self, key: str, value: str):
		return self.__db.fetchone(
			"SELECT :key FROM :table WHERE :key = :value",
			self.__params | {'key': key, 'value': value},
		) is not None


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
				packets += [UserPacket(self.connected_users[user_id], type, data) for user_id in set(user_id for guild in include_others_in for user_id in self.fetch_guild(guild).users) if user_id is not sender and user_id in self.connected_users]
			case GuildID():
				packets += [UserPacket(self.connected_users[user_id], type, data) for user_id in self.fetch_guild(include_others_in).users if user_id is not sender and user_id in self.connected_users]
			case None:
				pass # needs a comment so the linter stops yelling at me
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
	
	def fetch_user_by_username(self, username: str):
		"""Users should not be stored outside of connected_users"""
		res = self.__db_cursor.execute("SELECT id, username, password, color FROM users WHERE username=?", (username,)).fetchone()
		if res[0] in self.connected_users:
			return self.connected_users[res[0]]
		else:
			return User(
				id=res[0],
				name=res[1],
				username=res[1],
				password=bytes(res[2], encoding='utf-8'),
				color=res[3],
				guilds=[],
			)
	
	def fetch_guild(self, guild_id: GuildID):
		res = self.__db_cursor.execute("SELECT id, name, channels, users FROM guilds WHERE id=?", (guild_id,)).fetchone()
		return Guild(
			id=res[0],
			name=res[1],
			channels=res[2].split(','),
			users=res[3].split(','),
		)


@App.handler
def register(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is not None:
		return conn.error_packets(ALREADY_LOGGED_IN)
	
	username = data['username']
	password = data['password']
	
	if conn.app.user_name_exists(username):
		return conn.error_packets("Username taken")
	
	user_id = conn.app.generate_user_id()
	user = User(
		id=user_id,
		name=user_id,
		username=username,
		password=bcrypt.hashpw(bytes(password, encoding='utf-8'), bcrypt.gensalt()),
		color='#000000',
		guilds=[]
	)
	conn.app.insert_user(user)
	conn.log_in_as_user(user)
	return (
		conn.make_packets('loginSelf', {
			'me': user.json(),
			'users': [connected_user.json() for connected_user in conn.app.connected_users.values() if connected_user is not user],
		}, include_sender=True, include_others_in=None) +
		conn.make_packets('login', {
			'user': user.json()
		}, include_sender=False, include_others_in=user.guilds)
	)

@App.handler
def login(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is not None:
		return conn.error_packets(ALREADY_LOGGED_IN)
	
	username = data['username']
	password = data['password']
	
	if not conn.app.user_name_exists(username):
		return conn.error_packets("No user with that username")

	user: User = conn.app.fetch_user_by_username(username)
	if not bcrypt.checkpw(bytes(password, encoding='utf-8'), bytes(user.password, encoding='utf-8')):
		return conn.error_packets("Wrong password")

	conn.log_in_as_user(user)
	return (
		conn.make_packets('loginSelf', {
			'me': user.json(),
			'users': [connected_user.json() for connected_user in conn.app.connected_users.values() if connected_user is not user],
		}, include_sender=True, include_others_in=None) +
		conn.make_packets('login', {
			'user': user.json()
		}, include_sender=False, include_others_in=user.guilds)
	)

@App.handler
def post(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is None:
		return conn.error_packets(NOT_LOGGED_IN)
	
	text: str = data['text']
	guild_id: GuildID = data['guild']
	
	if guild_id not in conn.app.guilds:
		return conn.error_packets(NO_GUILD_WITH_ID)
	
	return conn.make_packets('post', {
		'user': conn.sender.id,
		'text': text,
		'guild': guild_id,
	}, include_sender=True, include_others_in=conn.sender.guilds)

@App.handler
def createGuild(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is None:
		return conn.error_packets(NOT_LOGGED_IN)
	
	conn.sender.guilds.append(guild_id)
	conn.app.update_user(conn.sender, 'guilds')

	return conn.make_packets('joinGuildSelf', {
			'user': conn.sender.id,
			'guild': guild_id,
		}, include_sender=True, include_others_in=None)

@App.handler
def joinGuild(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is None:
		return conn.error_packets(NOT_LOGGED_IN)
	
	guild_id: GuildID = data['guild']
	
	if guild_id not in conn.app.guilds:
		return conn.error_packets(NO_GUILD_WITH_ID)
	
	if guild_id in conn.sender.guilds:
		return conn.error_packets("Already in guild")
	
	conn.sender.guilds.append(guild_id)
	conn.app.update_user(conn.sender, 'guilds')

	return (
		conn.make_packets('joinGuildSelf', {
			'user': conn.sender.id,
			'guild': guild_id,
		}, include_sender=True, include_others_in=None) +
		conn.make_packets('joinGuild', {
			'user': conn.sender.id,
			'guild': guild_id,
		}, include_sender=False, include_others_in=guild_id)
	)

@App.handler
def leaveGuild(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is None:
		return conn.error_packets(NOT_LOGGED_IN)
	
	guild_id: GuildID = data['guild']
	
	if guild_id not in conn.app.guilds:
		return conn.error_packets(NO_GUILD_WITH_ID)
	
	if guild_id not in conn.sender.guilds:
		return conn.error_packets("Not in guild")
	
	conn.sender.guilds.remove(guild_id)
	conn.app.update_user(conn.sender, 'guilds')
	
	return (
		conn.make_packets('leaveGuildSelf', {
			'user': conn.sender.id,
			'guild': guild_id,
		}, include_sender=True, include_others_in=None) +
		conn.make_packets('leaveGuild', {
			'user': conn.sender.id,
			'guild': guild_id,
		}, include_sender=False, include_others_in=guild_id)
	)


def main():
	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)

	db = DB(connect('lunachat.db'))

	ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
	ssl_context.load_cert_chain('cert.pem', 'key.pem', os.getenv('LUNACHAT_SSL_PW'))

	app = App(db)
	start_server = serve(app.websocket_handler, 'localhost', 8000, ssl=ssl_context)

	print("Server started")
	loop.run_until_complete(start_server)
	loop.run_forever()

if __name__ == '__main__':
	main()