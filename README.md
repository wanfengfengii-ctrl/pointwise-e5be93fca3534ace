# Research schema warehouse

Python 3.11, standard library only. Archive research batches under immutable
writer schemas and publish reproducible reads under fixed reader revisions.
SQLite stores original tagged JSON, task bindings, terminal results and receipts.
No service or installation is required.

Run the full suite:

    python3.11 -B -m unittest discover -s tests -v

Run the real database/reopen demo:

    python3.11 -B -m warehouse demo

Construct a populated legacy database in a fresh project-owned path:

    python3.11 -B -m warehouse seed example.sqlite

The seed appends a finite fixture; use a fresh path for reproducible IDs.
The demo owns and removes its temporary directory. A memory-only business probe:

    python3.11 -B -m warehouse probe

## Public API

    from warehouse import Warehouse

    with Warehouse("research.sqlite") as w:
        writer = w.register("measurements", "int")
        reader = w.register("readings", ["string", "long"])
        w.append("day-1", writer, [7, 7, -2])
        task = w.create_read(reader, ["day-1", "day-1"])
        assert w.result(task)["state"] == "pending"
        result = w.run(task)
        assert result["rows"] == [
            {"branch": 1, "value": n} for n in [7, 7, -2, 7, 7, -2]]
        assert w.run(task) == result

register(name, schema) always creates a new immutable integer revision and
updates that catalog name's head. Catalog names are independent of qualified
names inside a schema. schemas.get(revision) returns a detached schema;
schemas.head(name) resolves the catalog head. Revisions are never implicitly
substituted into a task or batch.

append(batch_id, writer_revision, rows) requires a list, validates every row,
then atomically archives the batch. Same ID, same revision and same JSON content
replays the original archive receipt; any other content raises Conflict. Object
key order does not matter. Empty batches are valid. Caller mutations cannot
change archived data. batches.get(batch_id) returns a detached archive.

create_read(reader_revision, batch_ids) requires an ordered list. Repeated
batch IDs mean repeated consumption; an empty list publishes an empty result.
All references are checked before creating the pending task. Tasks do not follow
later registrations. tasks.get(task_id) exposes their fixed bindings.

run(task_id, before_commit=None) consumes batches in task order and rows in
archive order. It builds a complete outcome before publication. Resolution
failure produces a failed terminal outcome with rows=None and the first
batch_id, batch_index, row_index, code, path, message. Indices start at zero;
paths use reader field names and array indices. Both success and failure receive
one stable read receipt. Replaying a terminal task returns its existing outcome.

result(task_id) returns task_id, state, rows, error, receipt.
Pending outcomes have null rows, error and receipt. Success has a list of rows
and null error; failure has null rows and an error object. Unknown identities
raise NotFound. Invalid schemas raise SchemaError; invalid writer rows raise
PayloadError. Neither leaves a partial catalog revision or batch.

Publication commits task state, complete result and receipt together.
before_commit(connection) is an optional transaction observer for deterministic
database failure testing; raising rolls back the entire publication and leaves
the task pending. Infrastructure exceptions propagate; they are not recorded as
schema-resolution failures. Reopen the same path and retry. This is a single
worker library, without concurrent execution or online migration guarantees.

## Storage and components

[PORTS.md](PORTS.md) specifies the complete supported schema language, branch
matching, runtime failures and SDK2 instruction format.

The business chain uses SDK1: schemas.py owns revisions and heads; batches.py
owns validated facts; tasks.py owns fixed requests; reader.py resolves each
batch's writer/reader pair; results.py owns atomic publication.
schema_v1.sql is the real persisted legacy format. samples.py constructs
recursive Node fixtures with repeated rows, an empty batch, pending and
successful tasks, and a failed enum read. Existing reads never use SDK2.
schema_v2.sql is an empty independent target format. SDK2 executes native JSON
programs in isolation; there is no schema compiler or upgrade entry.

## Business regressions

test_ingest.py: recursive archives, ambiguous aliases, invalid tags, bounds,
replay/conflict and rejection without partial state.
test_read.py: aliases, defaults, exact promotion, reordered unions, recursion,
mutual references, null/empty arrays, lazy incompatible branches, enum defaults,
first error and no fallback after branch selection.
test_evolution.py: immutable revisions, fixed task and batch order, duplicates,
missing references and pending results.
test_publish.py: real transaction failure, reopen/retry and a complete populated
legacy fixture.
test_ports.py: independent SDK2 serialization, recursive execution, explicit
failures and empty target constraints.
