import sqlite3
from importlib.resources import files
from contextlib import contextmanager
from .errors import Conflict


class Database:
    def __init__(self, path=":memory:"):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(files("warehouse").joinpath("schema_v1.sql").read_text())
        self.connection.commit()

    @contextmanager
    def transaction(self):
        if self.connection.in_transaction:
            raise Conflict("nested transaction")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def close(self):
        self.connection.close()
