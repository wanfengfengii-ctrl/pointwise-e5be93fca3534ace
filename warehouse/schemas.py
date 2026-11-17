from vendor.sdk1 import Schema
from .errors import SchemaError, NotFound
from .jsonutil import dumps, loads


class SchemaCatalog:
    def __init__(self, db):
        self.db = db

    def register(self, name, schema):
        if not isinstance(name, str) or not name:
            raise SchemaError("catalog name must be nonempty text")
        parsed = Schema(schema)
        text = dumps(parsed.raw)
        with self.db.transaction() as conn:
            revision = conn.execute(
                "INSERT INTO schema_revisions(name,schema_json) VALUES(?,?)",
                (name, text)).lastrowid
            conn.execute("INSERT INTO schema_heads VALUES(?,?) "
                         "ON CONFLICT(name) DO UPDATE SET revision=excluded.revision",
                         (name, revision))
        return revision

    def get(self, revision):
        row = self.db.connection.execute(
            "SELECT schema_json FROM schema_revisions WHERE revision=?", (revision,)).fetchone()
        if row is None:
            raise NotFound(f"schema revision {revision}")
        return loads(row[0])

    def head(self, name):
        row = self.db.connection.execute(
            "SELECT revision FROM schema_heads WHERE name=?", (name,)).fetchone()
        if row is None:
            raise NotFound(f"schema {name}")
        return row[0]
