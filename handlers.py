from typing import Any
import bcrypt

from .app import App, WebsocketConnection
from .lang import *
from .network import Packet
from .types import Channel, ChannelID, Guild, GuildID, User, UserID


@App.handler
def register(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is not None:
		return conn.error_packets(ALREADY_LOGGED_IN)
	
	username: str = data['username']
	password: str = data['password']
	
	if conn.app.user_spec.entry_exists('username', username):
		return conn.error_packets("Username taken")
	
	user_id = UserID(conn.app.user_spec.generate_unique_id())
	user = User(
		id=user_id,
		name=user_id,
		username=username,
		password=str(bcrypt.hashpw(bytes(password, encoding='utf-8'), bcrypt.gensalt())),
		color='#000000',
		guilds=[]
	)
	conn.app.user_spec.insert(user)
	conn.log_in(user)
	return (
		conn.make_packets('loginSelf', {
			'me': user.json(),
			'users': [connection.user.json() for connection in conn.app.connected_users.values() if connection.user is not user],
		}, include_sender=True, include_others_in=None) +
		conn.make_packets('login', {
			'user': user.json()
		}, include_sender=False, include_others_in=user.guilds)
	)

@App.handler
def login(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is not None:
		return conn.error_packets(ALREADY_LOGGED_IN)
	
	username: str = data['username']
	password: str = data['password']

	user = conn.app.user_spec.select('username', username)
	
	if not user:
		return conn.error_packets(NO_USER_WITH_USERNAME)

	if not bcrypt.checkpw(bytes(password, encoding='utf-8'), bytes(user.password, encoding='utf-8')):
		return conn.error_packets("Wrong password")

	conn.log_in(user)
	return (
		conn.make_packets('loginSelf', {
			'me': user.json(),
			'users': [connection.user.json() for connection in conn.app.connected_users.values() if connection.user is not user],
		}, include_sender=True, include_others_in=None) +
		conn.make_packets('login', {
			'user': user.json()
		}, include_sender=False, include_others_in=user.guilds)
	)

@App.handler
def logout(conn: WebsocketConnection, data: Any) -> list[Packet]:
	if conn.sender is None:
		return conn.error_packets(NOT_LOGGED_IN)
	
	conn.log_out() # logout packets in here
	return conn.websocket_packets('logoutSelf', {})

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
	
	guild_name = data['name']
	
	guild_id = GuildID(conn.app.guild_spec.generate_unique_id())
	guild = Guild(
		id=guild_id,
		name=guild_name,
		channels=[],
		users=[],
	)

	initial_channel_id = guild.generate_channel_id()
	initial_channel = 

	conn.sender.guilds.append(guild_id)
	conn.app.user_spec.update(conn.sender, 'guilds')

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
		return conn.error_packets(ALREADY_IN_GUILD)
	
	conn.sender.guilds.append(guild_id)
	conn.app.user_spec.update(conn.sender, 'guilds')

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
		return conn.error_packets(NOT_IN_GUILD)
	
	conn.sender.guilds.remove(guild_id)
	conn.app.user_spec.update(conn.sender, 'guilds')
	
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