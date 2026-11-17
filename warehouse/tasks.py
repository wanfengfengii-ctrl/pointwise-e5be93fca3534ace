from .errors import NotFound, PayloadError
from .jsonutil import dumps, loads


class ReadCatalog:
    def __init__(self, db, schemas, batches):
        self.db, self.schemas, self.batches = db, schemas, batches

    def create_read(self, reader_revision, batch_ids):
        if (not isinstance(batch_ids, list)
                or any(not isinstance(x, str) for x in batch_ids)):
            raise PayloadError("batch_ids must be a list of strings")
        text = dumps(batch_ids)
        with self.db.transaction() as conn:
            self.schemas.get(reader_revision)
            for batch_id in batch_ids:
                self.batches.get(batch_id)
            return conn.execute(
                "INSERT INTO read_tasks(reader_revision,batches_json,state) VALUES(?,?,'pending')",
                (reader_revision, text)).lastrowid

    def get(self, task_id):
        row = self.db.connection.execute(
            "SELECT * FROM read_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise NotFound(f"task {task_id}")
        return {"task_id": task_id, "reader_revision": row["reader_revision"],
                "batch_ids": loads(row["batches_json"]), "state": row["state"]}
