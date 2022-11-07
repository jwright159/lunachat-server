from sqlite3 import Connection
from sqlite3.dbapi2 import _Parameters # type: ignore
from typing import Generic, TypeVar
import uuid

from .types import Spec, UniqueObject


UNIQUE = TypeVar('UNIQUE', bound=UniqueObject)

class DB:
	def __init__(self, db: Connection) -> None:
		self.__db = db
		self.__cursor = db.cursor()
	
	def execute(self, sql: str, params: _Parameters):
		self.__cursor.execute(sql, params)
		self.__db.commit()
	
	def fetch(self, sql: str, params: _Parameters, *, size: int | None = 1):
		if size is None or size <= 0:
			return self.__cursor.execute(sql, params).fetchall()
		elif size == 1:
			return self.__cursor.execute(sql, params).fetchone()
		else:
			return self.__cursor.execute(sql, params).fetchmany(size)


class DBSpec(Generic[UNIQUE]):
	def __init__(self, table_name: str, db: DB, spec: Spec) -> None:
		if not spec:
			raise ValueError("Spec needs at least one key")
		
		self.__table_name = table_name
		self.__db = db
		self.__spec = list(spec)
		self.__table_params = {'table': self.__table_name}
		self.__spec_params = dict(zip(self.__spec, self.__spec))
		self.__spec_param_string = ", ".join(f":{key}" for key in self.__spec)
		self.__spec_param_key_string = ", ".join([f":{self.__spec[0]} TEXT NOT NULL PRIMARY KEY"] + [f":{key} TEXT NOT NULL" for key in self.__spec[1:]])

		self.__ensure_table_exists()
	
	def __table_exists(self) -> bool:
		return self.__db.fetch(
			"SELECT name FROM sqlite_master WHERE name = :table",
			self.__table_params,
		) is not None

	def __create_table(self):
		self.__db.execute(
			f"CREATE TABLE :table ({self.__spec_param_key_string})",
			self.__table_params | self.__spec_params,
		)
	
	def __ensure_table_exists(self):
		if not self.__table_exists():
			self.__create_table()

	def insert(self, obj: UNIQUE) -> None:
		self.__db.execute(
			f"INSERT INTO :table VALUES ({self.__spec_param_string})",
			self.__table_params | self.__create_spec_value_params(obj),
		)

	def update(self, obj: UNIQUE, key: str) -> None:
		self.__db.execute(
			"UPDATE :table SET :key = :value WHERE id = :id",
			self.__table_params | {'key': key, 'value': getattr(obj, key), 'id': obj.id},
		)
	
	def select(self, key: str, value: str, *, size: int | None = 1) -> UNIQUE | None:
		res = self.__db.fetch(
			f"SELECT {self.__spec_param_string} FROM :table WHERE :key = :value",
			self.__table_params | self.__spec_params | {'key': key, 'value': value},
			size=size,
		)
		if not res:
			return None
		else:
			attrs = dict(zip(self.__spec, res))
			generic_type: type[UNIQUE] = self.__getattribute__('__orig_class__')[0]
			return generic_type(**attrs)

	def entry_exists(self, key: str, value: str) -> bool:
		return self.__db.fetch(
			"SELECT :key FROM :table WHERE :key = :value",
			self.__table_params | {'key': key, 'value': value},
		) is not None
	
	def __create_spec_value_params(self, obj: UNIQUE):
		return dict((key, getattr(obj, key)) for key in self.__spec)
	
	@classmethod
	def generate_generic_id(cls) -> str:
		return uuid.uuid4().hex[:16]

	def generate_unique_id(self) -> str:
		obj_id = self.generate_generic_id()
		while self.entry_exists('id', obj_id):
			obj_id = self.generate_generic_id()
		return obj_id