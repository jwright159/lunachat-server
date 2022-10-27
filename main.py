import asyncio
from dotenv import load_dotenv # type: ignore
import os
import ssl
import sqlite3
from websockets.server import serve

from . import packets # type: ignore
from .app import App
from .db import DB

def main():
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