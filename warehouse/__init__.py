from .storage import Database
from .errors import (WarehouseError, SchemaError, PayloadError, ResolutionError,
                     Conflict, NotFound)


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
