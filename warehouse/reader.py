from vendor.sdk1 import Resolver
from .errors import ResolutionError


class ReadExecutor:
    def __init__(self, schemas, batches, tasks, publisher):
        self.schemas, self.batches = schemas, batches
        self.tasks, self.publisher = tasks, publisher

    def run(self, task_id, before_commit=None):
        task = self.tasks.get(task_id)
        if task["state"] != "pending":
            return self.publisher.result(task_id)
        reader = self.schemas.get(task["reader_revision"])
        output = []
        for batch_index, batch_id in enumerate(task["batch_ids"]):
            batch = self.batches.get(batch_id)
            resolver = Resolver(self.schemas.get(batch["writer_revision"]), reader)
            for row_index, row in enumerate(batch["rows"]):
                try:
                    output.append(resolver.read(row))
                except ResolutionError as exc:
                    error = {"batch_id": batch_id, "batch_index": batch_index,
                             "row_index": row_index, "code": exc.code,
                             "path": exc.path, "message": exc.message}
                    return self.publisher.publish(task_id, None, error, before_commit)
        return self.publisher.publish(task_id, output, None, before_commit)
