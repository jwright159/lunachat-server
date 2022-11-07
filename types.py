from dataclasses import dataclass, field
from typing import Mapping, NewType, Sequence, cast

from .app import WebsocketConnection
from .db import DBSpec


GuildID = NewType('GuildID', str)
UserID = NewType('UserID', str)
ChannelID = NewType('ChannelID', str)

ObjectID = GuildID | UserID | ChannelID


DataBlock = Mapping[str, 'Data']
Data = str | int | float | ObjectID | Sequence['Data'] | DataBlock


Spec = set[str]


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

@dataclass
class User(UniqueObject):
	id: UserID
	username: str
	password: str
	color: str
	guilds: list[GuildID]
	connections: list[WebsocketConnection] = field(default_factory=list)
	
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

	def generate_channel_id(self) -> ChannelID:
		obj_id = DBSpec.generate_generic_id()
		while obj_id in self.channels:
			obj_id = DBSpec.generate_generic_id()
		return ChannelID(obj_id)
	
	@classmethod
	def json_spec(cls) -> Spec:
		return super().json_spec() | {'channels', 'users'}
	
	@classmethod
	def spec(cls) -> Spec:
		return super().spec() | {'channels', 'users'}