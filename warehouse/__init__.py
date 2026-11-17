from .storage import Database
from .errors import (WarehouseError, SchemaError, PayloadError, ResolutionError,
                     Conflict, MergeConflict, NotFound)
from .jsonutil import dumps


class Warehouse:
    def __init__(self, path=":memory:"):
        from .schemas import SchemaCatalog
        from .batches import BatchArchive
        from .tasks import ReadCatalog
        from .results import ResultPublisher
        from .reader import ReadExecutor
        self.db = Database(path)
        self.schemas = SchemaCatalog(self.db)
        self.batches = BatchArchive(self.db, self.schemas)
        self.tasks = ReadCatalog(self.db, self.schemas, self.batches)
        self.publisher = ResultPublisher(self.db, self.tasks)
        self.reader = ReadExecutor(self.schemas, self.batches, self.tasks, self.publisher)

    def register(self, name, schema):
        return self.schemas.register(name, schema)

    def append(self, batch_id, writer_revision, rows):
        return self.batches.append(batch_id, writer_revision, rows)

    def create_read(self, reader_revision, batch_ids):
        return self.tasks.create_read(reader_revision, batch_ids)

    def merge_schema(self, base, left, right):
        """Three-way confluence of exact revisions; no persistent state changes."""
        from .merge import merge_schemas
        return merge_schemas(self.schemas.get(base), self.schemas.get(left),
                             self.schemas.get(right))

    def create_merged_read(self, name, base, left, right, batch_ids,
                           before_commit=None):
        """Publish the merged revision, name head and pending task together."""
        from vendor.sdk1 import Schema
        if not isinstance(name, str) or not name:
            raise SchemaError("catalog name must be nonempty text")
        if (not isinstance(batch_ids, list)
                or any(not isinstance(x, str) for x in batch_ids)):
            raise PayloadError("batch_ids must be a list of strings")
        text = dumps(Schema(self.merge_schema(base, left, right)).raw)
        with self.db.transaction() as conn:
            for batch_id in batch_ids:
                self.batches.get(batch_id)
            revision = conn.execute(
                "INSERT INTO schema_revisions(name,schema_json) VALUES(?,?)",
                (name, text)).lastrowid
            conn.execute("INSERT INTO schema_heads VALUES(?,?) "
                         "ON CONFLICT(name) DO UPDATE SET revision=excluded.revision",
                         (name, revision))
            task_id = conn.execute(
                "INSERT INTO read_tasks(reader_revision,batches_json,state) "
                "VALUES(?,?,'pending')", (revision, dumps(batch_ids))).lastrowid
            if before_commit is not None:
                before_commit(conn)
        return {"revision": revision, "task_id": task_id}

    def run(self, task_id, before_commit=None):
        return self.reader.run(task_id, before_commit)

    def result(self, task_id):
        return self.publisher.result(task_id)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
