from vendor.sdk1 import Schema
from .errors import Conflict, NotFound, PayloadError
from .jsonutil import dumps, loads


class BatchArchive:
    def __init__(self, db, schemas):
        self.db, self.schemas = db, schemas

    def append(self, batch_id, writer_revision, rows):
        if not isinstance(batch_id, str) or not batch_id or not isinstance(rows, list):
            raise PayloadError("batch_id must be text and rows must be a list")
        schema = Schema(self.schemas.get(writer_revision))
        for row in rows:
            schema.validate(row)
        text = dumps(rows)
        with self.db.transaction() as conn:
            old = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if old:
                if old["writer_revision"] != writer_revision or old["rows_json"] != text:
                    raise Conflict("batch identity already has different content")
                return old["receipt"]
            receipt = "archive:" + batch_id
            conn.execute("INSERT INTO batches VALUES(?,?,?,?)",
                         (batch_id, writer_revision, text, receipt))
        return receipt

    def get(self, batch_id):
        row = self.db.connection.execute(
            "SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            raise NotFound(f"batch {batch_id}")
        return {"batch_id": row["batch_id"], "writer_revision": row["writer_revision"],
                "rows": loads(row["rows_json"]), "receipt": row["receipt"]}
